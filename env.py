"""
Gymnasium 强化学习环境
======================
将蜂群协同作战仿真系统封装为标准Gymnasium环境，
支持单智能体（集中式控制）和多智能体（分布式控制）模式。

兼容框架：
- Stable-Baselines3 (单智能体)
- RLlib (多智能体)
- PettingZoo / SuperSuit (多智能体)
- CleanRL (单/多智能体)

环境模式：
1. centralized: 单个策略控制所有UAV（全局观测、全局行动）
2. decentralized: 每个UAV有独立观测和行动（共享策略或独立策略）
3. hierarchical: 领导者制定高层计划，执行者进行具体行动

使用示例:
    from swarm_sim.env import SwarmEnv

    env = SwarmEnv(mode="centralized", uav_count=8, target_count=3)
    obs, info = env.reset()
    done = False
    total_reward = 0
    while not done:
        action = env.action_space.sample()  # 替换为模型推理
        obs, reward, terminated, truncated, info = env.step(action)
        total_reward += reward
    print(f"Total reward: {total_reward}")
    env.close()
"""
import math
import random
import sys
import os
import time
from typing import Optional, Union
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

try:
    import gymnasium as gym
    from gymnasium import spaces
except ImportError:
    import gym
    from gym import spaces

# 确保项目可导入
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from config import (
    MAP_WIDTH, MAP_HEIGHT,
    UAV_MAX_SPEED, UAV_DETECTION_RANGE, UAV_WEAPON_RANGE,
    UAV_MAX_BATTERY, UAV_HEALTH_MAX,
    MISSION_TARGET_HEALTH,
)


# ============ 环境配置 ============

@dataclass
class EnvConfig:
    """RL环境配置"""
    # 仿真参数
    uav_count: int = 8
    target_count: int = 3
    max_ticks: int = 500
    sim_fps: int = 0          # 0 = 不限制（最快速度训练）

    # 环境模式
    mode: str = "centralized"  # centralized / decentralized / hierarchical

    # 观测空间
    obs_include_velocity: bool = True
    obs_include_known_targets: bool = True
    obs_include_neighbor_info: bool = True
    obs_normalize: bool = True
    max_known_targets: int = 10  # 观测中最大已知目标数（填充用）

    # 行动空间
    continuous_actions: bool = True  # True=连续速度控制, False=离散方向
    action_scale: float = 1.0        # 行动缩放因子

    # 奖励权重
    reward_target_destroyed: float = 100.0
    reward_uav_destroyed: float = -50.0
    reward_target_damage: float = 0.15
    reward_uav_damage: float = -0.25
    reward_mission_complete: float = 200.0
    reward_step_penalty: float = -0.01
    reward_near_target: float = 0.05       # 接近目标的正奖励
    reward_formation: float = 0.02         # 编队保持奖励
    reward_share_info: float = 0.1         # 信息共享奖励
    reward_leader_alive: float = 0.02      # 领导者存活奖励

    # 随机种子
    seed: Optional[int] = None

    # 渲染
    render_mode: Optional[str] = None  # None / "human" / "rgb_array"


# ============ 观测/行动空间构建 ============

def _build_observation_space(config: EnvConfig) -> spaces.Dict:
    """构建单UAV观测空间"""
    n_targets = config.max_known_targets

    obs_dict = {
        # 自身状态
        "position": spaces.Box(-1.0, 1.0, shape=(3,), dtype=np.float32),
        "health": spaces.Box(0.0, 1.0, shape=(1,), dtype=np.float32),
        "battery": spaces.Box(0.0, 1.0, shape=(1,), dtype=np.float32),
        "role": spaces.Box(0.0, 1.0, shape=(4,), dtype=np.float32),  # one-hot
        "state": spaces.Box(0.0, 1.0, shape=(9,), dtype=np.float32),  # one-hot
        "is_leader": spaces.Discrete(2),
    }

    if config.obs_include_velocity:
        obs_dict["velocity"] = spaces.Box(-1.0, 1.0, shape=(3,), dtype=np.float32)

    if config.obs_include_known_targets:
        # 每个已知目标: (rel_x, rel_y, rel_z, health, destroyed)
        obs_dict["known_targets"] = spaces.Box(
            -1.0, 1.0, shape=(n_targets, 5), dtype=np.float32,
        )

    if config.obs_include_neighbor_info:
        obs_dict["neighbor_info"] = spaces.Box(
            0.0, 1.0, shape=(3,), dtype=np.float32,  # neighbor_count, avg_dist, is_connected
        )

    return spaces.Dict(obs_dict)


def _build_action_space(config: EnvConfig) -> spaces.Dict:
    """构建单UAV行动空间"""
    if config.continuous_actions:
        return spaces.Dict({
            # 速度向量 (vx, vy, vz) 归一化方向
            "movement": spaces.Box(-1.0, 1.0, shape=(3,), dtype=np.float32),
            # 攻击目标索引 (0=none, 1..N=target)
            "attack_target": spaces.Discrete(config.max_known_targets + 1),
        })
    else:
        return spaces.Dict({
            # 离散移动方向: 0=停, 1=N, 2=NE, 3=E, 4=SE, 5=S, 6=SW, 7=W, 8=NW
            "move_direction": spaces.Discrete(9),
            "attack_target": spaces.Discrete(config.max_known_targets + 1),
        })


# ============ 环境主体 ============

class SwarmEnv(gym.Env):
    """
    蜂群协同作战 Gymnasium 环境

    支持三种控制模式：

    1. centralized (集中式):
       - 单一智能体控制所有UAV
       - 观测 = 全局状态 (所有UAV + 所有目标)
       - 行动 = N * (movement + attack_target)
       - 适用于 PPO, SAC, TD3

    2. decentralized (分布式):
       - 多个独立智能体，每个控制一架UAV
       - 每个智能体的观测 = 该UAV的局部信息
       - 每个智能体的行动 = (movement + attack_target)
       - 适用于 MADDPG, QMIX, MAPPO

    3. hierarchical (层级式):
       - 领导者制定高层目标分配
       - 执行者执行具体移动/攻击
       - 适用于 HRL, Feudal Networks
    """

    metadata = {"render_modes": ["human", "rgb_array"], "render_fps": 10}

    def __init__(self, config: EnvConfig = None, **kwargs):
        super().__init__()

        self._cfg = config or EnvConfig(**kwargs)

        # 延迟导入仿真引擎（避免导入时实例化）
        from core.simulation import SimulationEngine
        from core.uav_agent import UAVRole, UAVState
        from core.communication import MessageType

        self._UAVRole = UAVRole
        self._UAVState = UAVState
        self._MessageType = MessageType

        # 构建观测/行动空间
        if self._cfg.mode == "centralized":
            single_obs = _build_observation_space(self._cfg)
            single_act = _build_action_space(self._cfg)
            n = self._cfg.uav_count
            self.observation_space = spaces.Dict({
                f"uav_{i}": single_obs for i in range(n)
            })
            self.observation_space["global"] = spaces.Box(
                -1.0, 1.0, shape=(self._cfg.target_count * 5 + 5,), dtype=np.float32,
            )
            self.action_space = spaces.Dict({
                f"uav_{i}": single_act for i in range(n)
            })
        else:
            single_obs = _build_observation_space(self._cfg)
            single_act = _build_action_space(self._cfg)
            self.observation_space = spaces.Dict({
                f"uav_{i}": single_obs for i in range(self._cfg.uav_count)
            })
            self.action_space = spaces.Dict({
                f"uav_{i}": single_act for i in range(self._cfg.uav_count)
            })

        self._engine: Optional[SimulationEngine] = None
        self._prev_target_health = {}
        self._prev_uav_health = {}
        self._episode_reward = 0.0

    def reset(self, seed=None, options=None) -> tuple:
        """重置环境"""
        super().reset(seed=seed)

        if seed is not None:
            random.seed(seed)
            np.random.seed(seed)

        from core.simulation import SimulationEngine

        self._engine = SimulationEngine(
            uav_count=self._cfg.uav_count,
            target_count=self._cfg.target_count,
            parallel_uavs=(self._cfg.uav_count >= 20),
        )
        self._engine.initialize()

        if self._cfg.fault_tick and hasattr(self._cfg, 'fault_tick') and self._cfg.fault_tick > 0:
            self._engine.enable_fault_injection(self._cfg.fault_tick)

        self._episode_reward = 0.0
        self._prev_target_health = {
            tid: t["health"] for tid, t in self._engine.targets.items()
        }
        self._prev_uav_health = {
            uid: u.health for uid, u in self._engine.uavs.items()
        }

        return self._get_obs(), self._get_info()

    def step(self, action: dict) -> tuple:
        """
        执行一步

        Args:
            action: 行动字典，格式取决于mode
                centralized: {"uav_0": {"movement": ..., "attack_target": ...}, ...}
                decentralized: 同上

        Returns:
            observation, reward, terminated, truncated, info
        """
        # 1. 将 RL 行动映射为 UAV 控制指令
        self._apply_actions(action)

        # 2. 执行仿真tick
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._engine.step())
        finally:
            loop.close()

        # 3. 计算奖励
        reward = self._compute_reward()

        # 4. 判断终止条件
        terminated = self._is_terminated()
        truncated = self._is_truncated()

        # 5. 获取观测
        obs = self._get_obs()
        info = self._get_info()

        self._episode_reward += reward

        if terminated or truncated:
            info["episode"] = {
                "r": self._episode_reward,
                "l": self._engine.tick,
                "alive_uavs": sum(1 for u in self._engine.uavs.values() if u.alive),
                "destroyed_targets": sum(1 for t in self._engine.targets.values() if t["destroyed"]),
            }

        return obs, reward, terminated, truncated, info

    def _apply_actions(self, action: dict):
        """将RL行动应用到UAV"""
        for uav_id, uav_action in action.items():
            uav_idx = int(uav_id.split("_")[1])
            uid = f"UAV_{uav_idx + 1:02d}"
            if uid not in self._engine.uavs:
                continue
            uav = self._engine.uavs[uid]
            if not uav.alive:
                continue

            # 解析移动
            if self._cfg.continuous_actions:
                mov = uav_action.get("movement", np.zeros(3))
                speed = min(float(np.linalg.norm(mov)), 1.0) * uav.max_speed
                if speed > 0.01:
                    direction = mov / (np.linalg.norm(mov) + 1e-8)
                    uav.target_pos = {
                        "x": uav.position["x"] + direction[0] * 500,
                        "y": uav.position["y"] + direction[1] * 500,
                        "z": uav.position["z"],
                    }
            else:
                move_dir = uav_action.get("move_direction", 0)
                angle_map = {
                    0: (0, 0), 1: (0, -1), 2: (1, -1), 3: (1, 0),
                    4: (1, 1), 5: (0, 1), 6: (-1, 1), 7: (-1, 0), 8: (-1, -1),
                }
                dx, dy = angle_map.get(move_dir, (0, 0))
                if dx != 0 or dy != 0:
                    uav.target_pos = {
                        "x": uav.position["x"] + dx * 500,
                        "y": uav.position["y"] + dy * 500,
                        "z": uav.position["z"],
                    }

            # 解析攻击目标
            attack_idx = uav_action.get("attack_target", 0)
            if attack_idx > 0:
                # 找到对应的目标
                alive_targets = [
                    tid for tid, t in self._engine.targets.items()
                    if not t["destroyed"]
                ]
                if attack_idx <= len(alive_targets):
                    target_id = alive_targets[attack_idx - 1]
                    if uav.state.value not in ("ENGAGING",):
                        uav.current_task = type(uav.current_task)(
                            task_id=f"rl_engage_{target_id}",
                            task_type="strike",
                            target_id=target_id,
                            target_pos=dict(self._engine.targets[target_id]["position"]),
                            assigned_to=uid,
                            status="active",
                            created_tick=self._engine.tick,
                        ) if uav.current_task else None
                    uav.target_pos = dict(self._engine.targets[target_id]["position"])
                    uav.state = self._UAVState.ENGAGING

    def _compute_reward(self) -> float:
        """计算奖励"""
        reward = 0.0

        # 目标摧毁奖励
        for tid, t in self._engine.targets.items():
            prev_health = self._prev_target_health.get(tid, t["health"])
            health_diff = prev_health - t["health"]
            if health_diff > 0:
                reward += health_diff * self._cfg.reward_target_damage
            if t["destroyed"] and prev_health > 0:
                reward += self._cfg.reward_target_destroyed

        # UAV损伤惩罚
        for uid, u in self._engine.uavs.items():
            prev_h = self._prev_uav_health.get(uid, u.health)
            health_diff = prev_h - u.health
            if health_diff > 0:
                reward += health_diff * self._cfg.reward_uav_damage
            if not u.alive and prev_h > 0:
                reward += self._cfg.reward_uav_destroyed

        # 接近目标奖励
        for uav in self._engine.uavs.values():
            if not uav.alive:
                continue
            for tid, t in self._engine.targets.items():
                if t["destroyed"]:
                    continue
                from config import distance
                dist = distance(uav.position, t["position"])
                reward += self._cfg.reward_near_target * (1.0 / max(dist / UAV_DETECTION_RANGE, 1.0))

        # 领导者存活奖励
        leader = next((u for u in self._engine.uavs.values() if u.is_leader and u.alive), None)
        if leader:
            reward += self._cfg.reward_leader_alive

        # 任务完成奖励
        if self._engine.mission_coord.check_mission_complete():
            reward += self._cfg.reward_mission_complete

        # 步数惩罚
        reward += self._cfg.reward_step_penalty

        # 更新前一帧状态
        self._prev_target_health = {
            tid: t["health"] for tid, t in self._engine.targets.items()
        }
        self._prev_uav_health = {
            uid: u.health for uid, u in self._engine.uavs.items()
        }

        return float(reward)

    def _is_terminated(self) -> bool:
        """是否达到终止条件"""
        # 所有目标被摧毁
        if self._engine.mission_coord.check_mission_complete():
            return True
        # 所有UAV被摧毁
        if all(not u.alive for u in self._engine.uavs.values()):
            return True
        return False

    def _is_truncated(self) -> bool:
        """是否超时"""
        return self._engine.tick >= self._cfg.max_ticks

    def _get_obs(self) -> dict:
        """构建观测"""
        obs = {}
        engine = self._engine

        targets_list = sorted(engine.targets.items())
        alive_uavs = [u for u in engine.uavs.values() if u.alive]

        for uav in engine.uavs.values():
            key = f"uav_{int(uav.id.split('_')[1]) - 1}"
            obs[key] = self._build_uav_obs(uav, targets_list, alive_uavs)

        if self._cfg.mode == "centralized":
            # 全局观测: 所有目标的归一化状态
            global_obs = np.zeros(self._cfg.target_count * 5 + 5, dtype=np.float32)
            for i, (tid, t) in enumerate(targets_list):
                if i >= self._cfg.target_count:
                    break
                idx = i * 5
                global_obs[idx] = t["position"]["x"] / MAP_WIDTH
                global_obs[idx + 1] = t["position"]["y"] / MAP_HEIGHT
                global_obs[idx + 2] = t["position"].get("z", 0) / 500.0
                global_obs[idx + 3] = t["health"] / MISSION_TARGET_HEALTH
                global_obs[idx + 4] = 1.0 if t["destroyed"] else 0.0
            # 全局统计
            base = self._cfg.target_count * 5
            global_obs[base] = sum(1 for u in engine.uavs.values() if u.alive) / self._cfg.uav_count
            global_obs[base + 1] = engine.tick / self._cfg.max_ticks
            global_obs[base + 2] = 1.0 if engine.mission_coord.mission_status == "active" else 0.0
            global_obs[base + 3] = sum(1 for t in engine.targets.values() if t["destroyed"]) / self._cfg.target_count
            global_obs[base + 4] = (1.0 if engine.swarm_network.leader_id else 0.0)
            obs["global"] = global_obs

        return obs

    def _build_uav_obs(self, uav, targets_list, alive_uavs) -> dict:
        """构建单个UAV的观测"""
        cfg = self._cfg

        if cfg.obs_normalize:
            pos = np.array([
                uav.position["x"] / MAP_WIDTH,
                uav.position["y"] / MAP_HEIGHT,
                uav.position.get("z", 0) / 500.0,
            ], dtype=np.float32)
        else:
            pos = np.array([
                uav.position["x"], uav.position["y"], uav.position.get("z", 0),
            ], dtype=np.float32)

        role_onehot = np.zeros(4, dtype=np.float32)
        role_idx = {"leader": 0, "scout": 1, "striker": 2, "relay": 3}
        role_onehot[role_idx.get(uav.role.value, 0)] = 1.0

        state_onehot = np.zeros(9, dtype=np.float32)
        state_idx = {
            "INIT": 0, "IDLE": 1, "MOVING": 2, "SCOUTING": 3,
            "ENGAGING": 4, "RETREATING": 5, "RELAYING": 6,
            "DAMAGED": 7, "DESTROYED": 8,
        }
        state_onehot[state_idx.get(uav.state.name, 0)] = 1.0

        obs_dict = {
            "position": pos.astype(np.float32),
            "health": np.array([uav.health / UAV_HEALTH_MAX], dtype=np.float32),
            "battery": np.array([uav.battery / UAV_MAX_BATTERY], dtype=np.float32),
            "role": role_onehot,
            "state": state_onehot,
            "is_leader": 1 if uav.is_leader else 0,
        }

        if cfg.obs_include_velocity:
            obs_dict["velocity"] = np.array([
                uav.velocity["x"] / (UAV_MAX_SPEED + 1e-8),
                uav.velocity["y"] / (UAV_MAX_SPEED + 1e-8),
                uav.velocity.get("z", 0) / (UAV_MAX_SPEED + 1e-8),
            ], dtype=np.float32)

        if cfg.obs_include_known_targets:
            kt = np.zeros((cfg.max_known_targets, 5), dtype=np.float32)
            known = list(uav.known_targets.items())
            for i in range(min(len(known), cfg.max_known_targets)):
                tid, info = known[i]
                # 相对位置
                kt[i, 0] = (info["pos"]["x"] - uav.position["x"]) / MAP_WIDTH
                kt[i, 1] = (info["pos"]["y"] - uav.position["y"]) / MAP_HEIGHT
                kt[i, 2] = (info["pos"].get("z", 0) - uav.position.get("z", 0)) / 500.0
                kt[i, 3] = info.get("health", 100) / MISSION_TARGET_HEALTH
                kt[i, 4] = 1.0 if info.get("destroyed", False) else 0.0
            obs_dict["known_targets"] = kt

        if cfg.obs_include_neighbor_info:
            from config import distance
            neighbors = [uid for uid in (uav.neighbors or set())]
            avg_dist = 0.0
            if neighbors:
                alive_neighbors = [n for n in neighbors if n in self._engine.uavs and self._engine.uavs[n].alive]
                if alive_neighbors:
                    avg_dist = np.mean([
                        distance(uav.position, self._engine.uavs[n].position)
                        for n in alive_neighbors
                    ])
            obs_dict["neighbor_info"] = np.array([
                min(len(neighbors), 10) / 10.0,
                min(avg_dist / 10000.0, 1.0),
                1.0 if self._engine.swarm_network.topology.get(uav.id) else 0.0,
            ], dtype=np.float32)

        return obs_dict

    def _get_info(self) -> dict:
        """额外信息"""
        engine = self._engine
        return {
            "tick": engine.tick,
            "alive_uavs": sum(1 for u in engine.uavs.values() if u.alive),
            "destroyed_targets": sum(1 for t in engine.targets.values() if t["destroyed"]),
            "mission_status": engine.mission_coord.mission_status,
            "leader_id": engine.swarm_network.leader_id,
            "messages_sent": engine.broker.stats["total_sent"],
            "messages_dropped": engine.broker.stats["total_dropped"],
            "episode_reward": self._episode_reward,
        }

    def render(self):
        """渲染（可选实现）"""
        if self._cfg.render_mode == "human":
            state = self._engine.get_state()
            # 文本渲染
            print(f"\r[T={state['tick']}] "
                  f"存活: {state['summary']['alive_uavs']}/{state['summary']['total_uavs']} "
                  f"摧毁: {state['summary']['destroyed_targets']}/{state['summary']['total_targets']} "
                  f"奖励: {self._episode_reward:.1f}", end="")
        elif self._cfg.render_mode == "rgb_array":
            # 返回 RGB 数组（可接CNN）
            return np.zeros((600, 900, 3), dtype=np.uint8)

    def close(self):
        """关闭环境"""
        if self._engine:
            self._engine.shutdown()
            self._engine = None


# ============ 辅助函数 ============

def make_env(
    mode: str = "centralized",
    uav_count: int = 8,
    target_count: int = 3,
    max_ticks: int = 500,
    **kwargs,
) -> SwarmEnv:
    """
    创建环境的便捷函数

    Args:
        mode: "centralized" / "decentralized" / "hierarchical"
        uav_count: 无人机数量
        target_count: 目标数量
        max_ticks: 最大步数
        **kwargs: 其他 EnvConfig 参数

    Returns:
        SwarmEnv 实例
    """
    config = EnvConfig(
        mode=mode,
        uav_count=uav_count,
        target_count=target_count,
        max_ticks=max_ticks,
        **kwargs,
    )
    return SwarmEnv(config)


def make_vec_env(
    num_envs: int = 4,
    mode: str = "centralized",
    uav_count: int = 8,
    target_count: int = 3,
    **kwargs,
) -> gym.vector.SyncVectorEnv:
    """
    创建向量化环境（并行训练用）

    使用示例:
        from stable_baselines3 import PPO
        vec_env = make_vec_env(num_envs=4, mode="centralized")
        model = PPO("MultiInputPolicy", vec_env, verbose=1)
        model.learn(total_timesteps=1_000_000)
    """
    def _make():
        return make_env(mode=mode, uav_count=uav_count, target_count=target_count, **kwargs)

    return gym.vector.SyncVectorEnv([_make for _ in range(num_envs)])


# ============ 注册环境 ============

def register_envs():
    """向Gymnasium注册环境"""
    try:
        gym.register(
            id="SwarmCombat-v0",
            entry_point="swarm_sim.env:SwarmEnv",
            max_episode_steps=500,
            kwargs={"mode": "centralized", "uav_count": 8, "target_count": 3},
        )
        gym.register(
            id="SwarmCombat-Decentralized-v0",
            entry_point="swarm_sim.env:SwarmEnv",
            max_episode_steps=500,
            kwargs={"mode": "decentralized", "uav_count": 8, "target_count": 3},
        )
        gym.register(
            id="SwarmCombat-Large-v0",
            entry_point="swarm_sim.env:SwarmEnv",
            max_episode_steps=1000,
            kwargs={"mode": "centralized", "uav_count": 32, "target_count": 10},
        )
    except gym.error.Error:
        pass  # 已注册


# 自动注册
register_envs()
