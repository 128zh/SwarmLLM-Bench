"""
UAV Agent 模块
==============
每个无人机是一个自主Agent，具有：
- LangGraph风格的状态机（状态图驱动决策）
- 感知-决策-行动循环
- 通信能力（通过MessageBroker收发消息）
- 角色能力模型（指挥/侦察/攻击/中继）
- 自主行为：移动、探测、交战、报告
"""
import math
import asyncio
import random
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional

from config import (
    UAV_MAX_SPEED,
    UAV_DETECTION_RANGE,
    UAV_WEAPON_RANGE,
    UAV_MAX_BATTERY,
    UAV_BATTERY_DRAIN,
    UAV_HEALTH_MAX,
    UAV_ROLES,
    COMM_HEARTBEAT_INTERVAL,
    distance,
)
from core.communication import AsyncMessageBroker, MessagePriority, MessageType
from core.ooda_engine import OODAPhase


class UAVState(Enum):
    """无人机状态"""
    INIT = auto()           # 初始化
    IDLE = auto()           # 待命
    MOVING = auto()         # 移动中
    SCOUTING = auto()       # 侦察中
    ENGAGING = auto()       # 交战中
    RETREATING = auto()     # 撤退中
    RELAYING = auto()       # 中继通信
    DAMAGED = auto()        # 受损（仍可工作，降级）
    DESTROYED = auto()      # 摧毁（节点失效，触发重组网）
    MANUAL = auto()         # 手动操控模式
    EVADING = auto()        # 规避拦截威胁


class UAVRole(Enum):
    """无人机角色"""
    LEADER = "leader"
    SCOUT = "scout"
    STRIKER = "striker"
    RELAY = "relay"


@dataclass
class Task:
    """任务"""
    task_id: str
    task_type: str           # scout / strike / defend / relay
    target_id: Optional[str] = None
    target_pos: Optional[dict] = None
    assigned_to: Optional[str] = None
    status: str = "pending"  # pending / active / completed / failed
    priority: int = 1        # 1=高, 2=中, 3=低
    created_tick: int = 0
    completed_tick: int = 0


class UAVAgent:
    """
    无人机Agent - 蜂群中的单个智能体
    
    核心能力：
    1. 状态机决策：根据当前状态和环境感知选择行动
    2. 自主移动：朝目标点移动，支持避障
    3. 传感器探测：在一定范围内发现目标
    4. 武器交战：在武器射程内攻击目标
    5. 通信协同：通过Broker与其他Agent交换信息
    6. 心跳上报：定期发送心跳给蜂群网络
    7. 故障容错：受损降级运行，摧毁时触发重组网
    """

    def __init__(
        self,
        uav_id: str,
        role: UAVRole,
        position: dict,
        broker: AsyncMessageBroker,
        config: dict = None,
    ):
        self.id = uav_id
        self.role = role
        self.position = dict(position)
        self.velocity = {"x": 0.0, "y": 0.0, "z": 0.0}
        self.broker = broker
        self.config = config or {}

        # 角色能力修正
        role_mods = UAV_ROLES.get(role.value, {})
        self.max_speed = UAV_MAX_SPEED * role_mods.get("speed", 1.0)
        self.detection_range = UAV_DETECTION_RANGE * role_mods.get("detection", 1.0)
        self.weapon_range = UAV_WEAPON_RANGE * role_mods.get("weapon", 1.0)
        self.weapon_damage = self.config.get("weapon_damage", 35.0) * role_mods.get("weapon", 1.0)

        # 状态
        self.state = UAVState.INIT
        self.health = UAV_HEALTH_MAX
        self.battery = UAV_MAX_BATTERY
        self.alive = True

        # 飞行姿态 (roll/pitch/yaw in degrees)
        self.attitude = {"roll": 0.0, "pitch": 0.0, "yaw": 0.0}
        # 载荷与传感器
        self.camera_angle = 0.0       # 相机俯仰角 (-90 ~ 90)
        self.gps_signal = 100.0       # GPS信号强度 (0-100)
        self.payload = {"type": "none", "deployed": False, "quantity": 0}
        self.manual_mode = False      # 是否处于手动操控模式

        # 仿真引擎引用（用于OODA阶段标注）
        self._engine = None

        # 拦截威胁感知
        self.threat_level = "none"    # none/low/medium/high/critical
        self.threat_distance = float("inf")
        self.threat_interceptor_id = None
        self.evasion_strategy = "none"
        self._evasion_tick = 0
        self._zigzag_phase = random.uniform(0, 2 * math.pi)

        # 任务
        self.current_task: Optional[Task] = None
        self.target_pos: Optional[dict] = None

        # 通信
        self.heartbeat_counter = 0
        self.last_heartbeat_tick = 0
        self.neighbors: set = set()          # 邻居节点ID
        self.known_targets: dict = {}        # target_id -> {pos, health, type}
        self.leader_id: Optional[str] = None
        self.is_leader = False

        # 统计
        self.targets_engaged = 0
        self.targets_destroyed = 0
        self.distance_traveled = 0.0
        self.messages_sent = 0
        self.messages_received = 0

        # 订阅消息
        self._setup_subscriptions()

    def _setup_subscriptions(self):
        """设置消息订阅"""
        # 订阅自身ID（精准投递）
        self.broker.subscribe(self.id, self.id, self._on_message)
        # 订阅广播
        self.broker.subscribe(MessageType.BROADCAST, self.id, self._on_message)
        # 订阅各类消息
        for msg_type in [
            MessageType.TASK_ASSIGN,
            MessageType.TARGET_INFO,
            MessageType.ENGAGE_CMD,
            MessageType.NODE_FAILURE,
            MessageType.RECONNECT,
            MessageType.LEADER_ELECT,
            MessageType.FORMATION,
            MessageType.RETREAT,
        ]:
            self.broker.subscribe(msg_type, self.id, self._on_message)

    async def _on_message(self, msg_type: str, sender: str, payload: dict, tick: int):
        """消息回调处理"""
        self.messages_received += 1

        if msg_type == MessageType.TASK_ASSIGN:
            await self._handle_task_assign(payload, tick)
        elif msg_type == MessageType.TARGET_INFO:
            self._handle_target_info(sender, payload)
        elif msg_type == MessageType.ENGAGE_CMD:
            await self._handle_engage_cmd(payload, tick)
        elif msg_type == MessageType.NODE_FAILURE:
            self._handle_node_failure(payload, tick)
        elif msg_type == MessageType.LEADER_ELECT:
            self._handle_leader_elect(payload)
        elif msg_type == MessageType.RECONNECT:
            self._handle_reconnect(payload)
        elif msg_type == MessageType.RETREAT:
            await self._handle_retreat(payload, tick)
        elif msg_type == MessageType.FORMATION:
            self._handle_formation(payload)

    # ============ 消息处理 ============

    async def _handle_task_assign(self, payload: dict, tick: int):
        """处理任务分配"""
        if not self.alive or self.state == UAVState.DESTROYED:
            return
        task = Task(
            task_id=payload["task_id"],
            task_type=payload["task_type"],
            target_id=payload.get("target_id"),
            target_pos=payload.get("target_pos"),
            assigned_to=self.id,
            status="active",
            created_tick=tick,
        )
        self.current_task = task
        self.target_pos = task.target_pos

        if task.task_type == "scout":
            self.state = UAVState.SCOUTING
        elif task.task_type == "strike":
            self.state = UAVState.MOVING
        elif task.task_type == "relay":
            self.state = UAVState.RELAYING
        else:
            self.state = UAVState.MOVING

    def _handle_target_info(self, sender: str, payload: dict):
        """处理目标信息共享"""
        target_id = payload.get("target_id")
        if target_id:
            self.known_targets[target_id] = {
                "pos": payload.get("pos"),
                "health": payload.get("health", 100),
                "type": payload.get("type", "unknown"),
                "reported_by": sender,
            }

    async def _handle_engage_cmd(self, payload: dict, tick: int):
        """处理交战指令"""
        if not self.alive:
            return
        target_id = payload.get("target_id")
        target_pos = payload.get("target_pos")
        if target_pos:
            self.target_pos = target_pos
        self.state = UAVState.ENGAGING
        if target_id:
            self.current_task = Task(
                task_id=f"engage_{target_id}",
                task_type="strike",
                target_id=target_id,
                target_pos=target_pos,
                assigned_to=self.id,
                status="active",
                created_tick=tick,
            )

    def _handle_node_failure(self, payload: dict, tick: int):
        """处理节点故障通知"""
        failed_id = payload.get("uav_id")
        if failed_id:
            self.neighbors.discard(failed_id)
            if self.leader_id == failed_id:
                self.leader_id = None
                self.is_leader = False

    def _handle_leader_elect(self, payload: dict):
        """处理领导者选举结果"""
        new_leader = payload.get("leader_id")
        self.leader_id = new_leader
        self.is_leader = (new_leader == self.id)

    def _handle_reconnect(self, payload: dict):
        """处理重组网通知"""
        new_neighbors = payload.get("neighbors", [])
        self.neighbors = set(new_neighbors)

    async def _handle_retreat(self, payload: dict, tick: int):
        """处理撤退指令"""
        if not self.alive:
            return
        rally_point = payload.get("rally_point")
        if rally_point:
            self.target_pos = rally_point
        self.state = UAVState.RETREATING
        self.current_task = None

    def _handle_formation(self, payload: dict):
        """处理编队指令"""
        if not self.alive:
            return
        new_pos = payload.get("position")
        if new_pos:
            self.target_pos = new_pos
            if self.state in (UAVState.IDLE, UAVState.INIT):
                self.state = UAVState.MOVING

    # ============ 感知-决策-行动循环 ============

    def apply_manual_command(self, cmd: dict):
        """
        应用手动操控命令

        Args:
            cmd: {throttle, roll, pitch, yaw, mode}
                throttle: 0.0-1.0 油门
                roll: -1.0 to 1.0 翻滚
                pitch: -1.0 to 1.0 俯仰
                yaw: -1.0 to 1.0 偏航
        """
        throttle = cmd.get("throttle", 0.0)
        roll = cmd.get("roll", 0.0)
        pitch = cmd.get("pitch", 0.0)
        yaw = cmd.get("yaw", 0.0)
        mode = cmd.get("mode", "gps")  # gps | attitude

        self.manual_mode = True
        self.state = UAVState.MANUAL

        speed = self.max_speed * max(0.0, min(1.0, throttle))

        if self.attitude["yaw"] is None:
            self.attitude["yaw"] = 0.0

        current_yaw = math.radians(self.attitude["yaw"])
        # 在GPS模式下，roll/pitch控制水平移动方向
        forward_x = math.cos(current_yaw) * pitch * speed
        forward_y = math.sin(current_yaw) * pitch * speed
        lateral_x = math.cos(current_yaw + math.pi / 2) * roll * speed
        lateral_y = math.sin(current_yaw + math.pi / 2) * roll * speed

        self.velocity = {
            "x": forward_x + lateral_x,
            "y": forward_y + lateral_y,
            "z": throttle * speed * 0.3,  # 垂直分量
        }

        # 更新姿态
        self.attitude["roll"] = roll * 45.0
        self.attitude["pitch"] = pitch * 30.0
        self.attitude["yaw"] = (self.attitude["yaw"] + yaw * 15.0) % 360.0

    async def step(self, tick: int, env_targets: dict, interceptor_swarm=None):
        """
        Agent每tick执行一次：感知 -> 决策 -> 行动

        Args:
            tick: 当前仿真tick
            env_targets: 环境中的所有目标 {target_id: Target}
            interceptor_swarm: 拦截器集群 (用于感知和反击)
        """
        if not self.alive or self.state == UAVState.DESTROYED:
            return

        # 电量检查
        self.battery -= UAV_BATTERY_DRAIN
        if self.battery <= 0:
            await self._destroy(tick, reason="battery_depleted")
            return

        # 健康检查
        if self.health <= 0:
            await self._destroy(tick, reason="destroyed_in_combat")
            return
        elif self.health < 30 and self.state not in (UAVState.RETREATING, UAVState.MANUAL):
            self.state = UAVState.DAMAGED

        # 心跳上报
        self.heartbeat_counter += 1
        if self.heartbeat_counter >= COMM_HEARTBEAT_INTERVAL:
            await self._send_heartbeat(tick)
            self.heartbeat_counter = 0

        # 手动模式：跳过自主决策，仅应用手动命令物理效果
        if self.state == UAVState.MANUAL:
            self.position["x"] += self.velocity["x"]
            self.position["y"] += self.velocity["y"]
            self.position["z"] = self.position.get("z", 200.0) + self.velocity["z"]
            self.distance_traveled += math.sqrt(
                self.velocity["x"] ** 2 + self.velocity["y"] ** 2 + self.velocity["z"] ** 2
            )
            self.gps_signal = max(60.0, min(100.0, 95.0 + random.uniform(-5, 5)))
            return

        # 规避模式：仍需感知（以便反击），但执行规避机动
        if self.state == UAVState.EVADING:
            self.battery -= UAV_BATTERY_DRAIN
            self.heartbeat_counter += 1
            if self.heartbeat_counter >= COMM_HEARTBEAT_INTERVAL:
                await self._send_heartbeat(tick)
                self.heartbeat_counter = 0
            # 感知拦截器（规避中也要知道敌人在哪！）
            self._sense(env_targets, interceptor_swarm)
            await self._evade_step(tick)
            return

        # OODA P1: OBSERVE → 感知环境（目标 + 拦截器）
        detected_targets = self._sense(env_targets, interceptor_swarm)

        # OODA P1: ORIENT → 真正的多维态势评估（avg_orient_depth=0 的修复）
        situation = self._orient(detected_targets, env_targets, tick)

        # 将态势评估结果注入 OODA 引擎的当前循环
        # 这样 orient_depth 才会正确反映评估深度
        if hasattr(self, '_engine') and self._engine and self._engine.ooda_engine:
            ooda = self._engine.ooda_engine
            cycle = ooda.current.get(self.id)
            if cycle:
                # 注入威胁向量（从态势中构建）
                if situation.get("threat_present"):
                    cycle.threat_vectors = [{
                        "source_id": self.threat_interceptor_id or "unknown",
                        "distance": self.threat_distance,
                        "threat_level": self.threat_level,
                    }]
                # 注入机会窗口（从目标优先级中构建）
                if situation.get("target_priorities"):
                    best = situation["target_priorities"][0]
                    cycle.opportunity_windows = [{
                        "target_id": best[0], "success_probability": min(0.9, 1.0 / (best[2] / 1000 + 1)),
                        "risk_level": "high" if situation.get("threat_near") else "medium",
                    }]
                # 注入决策选项
                options = []
                if situation.get("should_attack"):
                    options.append({"option_id": "attack", "action": "ENGAGE", "score": 0.8})
                if situation.get("should_retreat"):
                    options.append({"option_id": "retreat", "action": "RETREAT", "score": 0.9})
                if situation.get("should_coordinate"):
                    options.append({"option_id": "coordinate", "action": "COORDINATE", "score": 0.7})
                if situation.get("should_seek_support"):
                    options.append({"option_id": "seek_support", "action": "REQUEST_SUPPORT", "score": 0.65})
                if situation.get("should_change_approach"):
                    options.append({"option_id": "change_approach", "action": "CHANGE_TACTIC", "score": 0.6})
                if options:
                    cycle.decision_options = options
                    cycle.selected_option = max(options, key=lambda o: o["score"])["option_id"]

            ooda.mark_phase(self.id, OODAPhase.OBSERVE, tick, quality=0.85)
            ooda.mark_phase(self.id, OODAPhase.ORIENT, tick, quality=situation.get("orient_quality", 0.5))
            ooda.mark_phase(self.id, OODAPhase.DECIDE, tick, quality=situation.get("decision_confidence", 0.5))

        # OODA P1: DECIDE → 基于态势评估的智能决策
        await self._decide_and_act(tick, detected_targets, env_targets, situation)

        # OODA P1: 完成循环（让 orient_depth 进入统计）
        if hasattr(self, '_engine') and self._engine and self._engine.ooda_engine:
            self._engine.ooda_engine.mark_phase(self.id, OODAPhase.ACT, tick,
                quality=situation.get("action_effectiveness", 0.7))
            self._engine.ooda_engine.complete_cycle(self.id, tick,
                success=self.alive and self.state not in (UAVState.DESTROYED, UAVState.DAMAGED),
                feedback=f"Health={self.health:.0f} Threat={self.threat_level}",
                action_result={
                    "damage_dealt": self.targets_engaged * self.weapon_damage,
                    "uav_preserved": self.alive,
                })
            # 立即开始下一轮循环
            self._engine.ooda_engine.start_cycle(self.id, self.role.value, tick)

    def _sense(self, env_targets: dict, interceptor_swarm=None) -> dict:
        """感知周围环境，返回探测到的目标 + 拦截器"""
        detected = {}
        effective_range = self.detection_range
        if self.state == UAVState.DAMAGED:
            effective_range *= 0.5

        # --- 探测地面目标 ---
        for tid, target in env_targets.items():
            if target.get("destroyed"):
                continue
            t_pos = target["position"]
            dist = distance(self.position, t_pos)
            if dist <= effective_range:
                detected[tid] = target
                if tid not in self.known_targets or self.known_targets[tid].get("health") != target.get("health"):
                    self.known_targets[tid] = {
                        "pos": dict(t_pos),
                        "health": target.get("health", 100),
                        "type": target.get("type", "ground"),
                    }

        # --- 探测拦截器（新增：对空感知） ---
        self._detected_interceptors = {}
        if interceptor_swarm and hasattr(interceptor_swarm, 'units'):
            for iid, intr in interceptor_swarm.units.items():
                if not intr.alive:
                    continue
                dist = distance(self.position, intr.position)
                if dist <= effective_range:
                    self._detected_interceptors[iid] = {
                        "id": iid,
                        "position": dict(intr.position),
                        "health": intr.health,
                        "max_health": intr.max_health,
                        "state": intr.state.name if hasattr(intr.state, 'name') else str(intr.state),
                        "damage_per_hit": intr.damage_per_hit,
                    }

        return detected

    async def _evade_step(self, tick: int):
        """
        规避机动执行

        根据当前威胁等级和规避策略执行机动:
        - speed: 全速直线逃离
        - zigzag: 蛇形机动
        - altitude: 急剧改变高度
        - split: 蜂群分散
        """
        strategy = self.evasion_strategy
        speed = self.max_speed * (1.1 if self.threat_level in ("high", "critical") else 0.9)

        if strategy == "speed":
            # 全速逃离：朝远离威胁的方向
            if self.target_pos:
                dx = self.target_pos["x"] - self.position["x"]
                dy = self.target_pos["y"] - self.position["y"]
                dist = math.sqrt(dx * dx + dy * dy) or 1
                self.velocity = {
                    "x": (dx / dist) * speed,
                    "y": (dy / dist) * speed,
                    "z": 0,
                }

        elif strategy == "zigzag":
            # 蛇形机动
            self._zigzag_phase += 0.25
            if self.target_pos:
                dx = self.target_pos["x"] - self.position["x"]
                dy = self.target_pos["y"] - self.position["y"]
                dist = math.sqrt(dx * dx + dy * dy) or 1
                base_vx = (dx / dist) * speed * 0.85
                base_vy = (dy / dist) * speed * 0.85
                perp_x = -dy / dist
                perp_y = dx / dist
                zigzag = math.sin(self._zigzag_phase) * speed * 0.35
                self.velocity = {
                    "x": base_vx + perp_x * zigzag,
                    "y": base_vy + perp_y * zigzag,
                    "z": math.cos(self._zigzag_phase * 1.7) * speed * 0.15,
                }

        elif strategy == "altitude":
            # 高度变化
            target_z = min(500, self.position.get("z", 200) + 180)
            dz = target_z - self.position.get("z", 200)
            if self.target_pos:
                dx = self.target_pos["x"] - self.position["x"]
                dy = self.target_pos["y"] - self.position["y"]
                dist = math.sqrt(dx * dx + dy * dy) or 1
                self.velocity = {
                    "x": (dx / dist) * speed * 0.6,
                    "y": (dy / dist) * speed * 0.6,
                    "z": max(-speed * 0.5, min(speed * 0.5, dz)),
                }

        elif strategy == "split":
            # 蜂群分散
            if not self.target_pos:
                scatter = random.uniform(0, 2 * math.pi)
                self.target_pos = {
                    "x": self.position["x"] + math.cos(scatter) * 3000,
                    "y": self.position["y"] + math.sin(scatter) * 3000,
                    "z": self.position.get("z", 200) + random.uniform(50, 150),
                }
            if self.target_pos:
                dx = self.target_pos["x"] - self.position["x"]
                dy = self.target_pos["y"] - self.position["y"]
                dist = math.sqrt(dx * dx + dy * dy) or 1
                self.velocity = {
                    "x": (dx / dist) * speed * 0.9,
                    "y": (dy / dist) * speed * 0.9,
                    "z": random.uniform(-speed * 0.25, speed * 0.3),
                }

        else:
            # 默认：远离威胁
            if self.target_pos:
                dx = self.target_pos["x"] - self.position["x"]
                dy = self.target_pos["y"] - self.position["y"]
                dist = math.sqrt(dx * dx + dy * dy) or 1
                self.velocity = {
                    "x": (dx / dist) * speed,
                    "y": (dy / dist) * speed,
                    "z": 0,
                }

        # 应用速度
        self.position["x"] += self.velocity["x"]
        self.position["y"] += self.velocity["y"]
        self.position["z"] = self.position.get("z", 200) + self.velocity.get("z", 0)
        self.distance_traveled += math.sqrt(
            self.velocity["x"] ** 2 + self.velocity["y"] ** 2 + self.velocity.get("z", 0) ** 2
        )

        # 更新姿态
        vx, vy = self.velocity["x"], self.velocity["y"]
        if abs(vx) > 0.01 or abs(vy) > 0.01:
            self.attitude["yaw"] = math.degrees(math.atan2(vy, vx)) % 360
        self.attitude["roll"] = self.attitude["roll"] * 0.7
        self.attitude["pitch"] = self.attitude["pitch"] * 0.6

        # 立即反击检查：拦截器在射程内且血量足够→马上打！
        if hasattr(self, '_detected_interceptors') and self._detected_interceptors:
            for iid, intr in self._detected_interceptors.items():
                if distance(self.position, intr["position"]) <= self.weapon_range * 0.8 and self.health >= 30:
                    if self.role in (UAVRole.STRIKER, UAVRole.LEADER):
                        self._evasion_tick = 0
                        self.state = UAVState.ENGAGING
                        return  # 跳出规避，进入交战

        # 规避冷却：10 ticks后重新评估
        self._evasion_tick += 1
        if self._evasion_tick > 10:
            self._evasion_tick = 0
            # 如果威胁仍在且我是攻击机→反击！
            if self.threat_level in ("high", "critical") and self.role in (UAVRole.STRIKER, UAVRole.LEADER) and self.health >= 50:
                self.state = UAVState.ENGAGING
                # 将威胁位置设为目标
                if hasattr(self, '_pre_evasion_target') and self._pre_evasion_target:
                    self.target_pos = self._pre_evasion_target
            elif self.threat_level in ("medium", "high") and self.health >= 30:
                # 中等威胁: 继续规避但换策略
                strategies = ["zigzag", "altitude", "speed"]
                self.evasion_strategy = strategies[(self._evasion_tick // 10) % 3]
                self._evasion_tick = 5  # 再避5个tick
            else:
                self.state = UAVState.IDLE  # 威胁解除，恢复正常

    # ============ 威胁响应接口 ============

    def set_threat(self, threat_assessment):
        """
        接收威胁评估并触发规避响应

        由 SimulationEngine 每tick调用。
        当威胁等级 >= MEDIUM 时自动切换为 EVADING 状态。
        """
        from core.interceptor import ThreatLevel
        self.threat_level = threat_assessment.threat_level.name.lower()
        self.threat_distance = threat_assessment.nearest_distance
        self.threat_interceptor_id = threat_assessment.nearest_interceptor_id

        if threat_assessment.threat_level in (ThreatLevel.HIGH, ThreatLevel.CRITICAL):
            self.evasion_strategy = threat_assessment.recommended_evasion.value
            if self.state not in (UAVState.DESTROYED, UAVState.MANUAL, UAVState.EVADING):
                self.state = UAVState.EVADING
                self._evasion_tick = 0
                # 保存原目标以便规避后恢复
                if not hasattr(self, '_pre_evasion_target'):
                    self._pre_evasion_target = dict(self.target_pos) if self.target_pos else None
                self.target_pos = dict(threat_assessment.evasion_direction)
        elif threat_assessment.threat_level == ThreatLevel.MEDIUM:
            # 中威胁：如果不在交战中，开始规避
            if self.state not in (UAVState.ENGAGING, UAVState.DESTROYED, UAVState.MANUAL, UAVState.EVADING, UAVState.RETREATING):
                self.evasion_strategy = threat_assessment.recommended_evasion.value
                self.state = UAVState.EVADING
                self._evasion_tick = 0
                if not hasattr(self, '_pre_evasion_target'):
                    self._pre_evasion_target = dict(self.target_pos) if self.target_pos else None
                self.target_pos = dict(threat_assessment.evasion_direction)
        else:
            # 低/无威胁：恢复正常
            self.threat_level = "none" if threat_assessment.threat_level == ThreatLevel.NONE else "low"
            self.evasion_strategy = "none"

    # ============ OODA Orient: 态势评估（战报 avg_orient_depth=0 的核心修复） ============

    def _orient(self, detected_targets: dict, env_targets: dict, tick: int) -> dict:
        """
        OODA Orient 阶段 — 真正的多维态势评估

        战报中所有 Agent 的 avg_orient_depth=0，因为此前只有占位计算。
        现在评估五个维度，输出结构化态势理解供决策使用。
        """
        depth = 0.0
        factors = []

        # --- 维度1: 目标价值排序 ---
        priorities = []
        for tid, t in env_targets.items():
            if t.get("destroyed"): continue
            d = distance(self.position, t["position"])
            hp_r = t.get("health", 100) / max(t.get("max_health", 100), 1)
            bonus = {"command_post": 1.5, "radar_station": 1.3, "vehicle_convoy": 1.0}.get(t.get("type",""), 1.0)
            value = bonus / max(d/2000.0, 1.0) * (2.0 - hp_r)
            priorities.append((tid, value, d, hp_r))
        priorities.sort(key=lambda x: x[1], reverse=True)
        if priorities: factors.append("target_prioritized"); depth += 0.18

        # --- 维度2: 自身战斗力 ---
        effective = self.health >= 40 and self.battery >= 15
        if self.health < 25:  factors.append("critical_hp"); depth += 0.28
        elif self.health < 50: factors.append("damaged"); depth += 0.18
        if self.battery < 20:  factors.append("low_battery"); depth += 0.18
        if effective and self.health >= 70: depth += 0.08

        # --- 维度3: 威胁态势 (含拦截器可打击性评估) ---
        threat_present = self.threat_level in ("high", "critical")
        threat_near = self.threat_distance < 2500 and self.threat_level in ("medium","high","critical")
        if threat_present: factors.append("threat_active"); depth += 0.30
        if threat_near:    factors.append("threat_imminent"); depth += 0.30

        # 拦截器可反击性评估（新增）
        can_engage_interceptor = False
        interceptor_target = None
        if hasattr(self, '_detected_interceptors') and self._detected_interceptors:
            for iid, intr in self._detected_interceptors.items():
                d = distance(self.position, intr["position"])
                if d <= self.weapon_range and self.health >= 30:
                    can_engage_interceptor = True
                    interceptor_target = iid
                    factors.append("interceptor_in_range"); depth += 0.25
                    break

        # --- 维度4: 队友支援 ---
        nbr = len(self.neighbors)
        supported = nbr >= 2
        isolated = nbr == 0 and self.role != UAVRole.RELAY
        if supported: factors.append("team_support"); depth += 0.10
        if isolated:  factors.append("isolated"); depth += 0.22

        # --- 维度5: 策略有效性 ---
        stale = False
        if hasattr(self, '_last_damage_tick'):
            since_dmg = tick - self._last_damage_tick
            if since_dmg > 30 and self.targets_engaged > 0:
                stale = True; factors.append("strategy_stale"); depth += 0.22

        # --- 综合战术建议 ---
        retreat = (not effective) or (threat_near and self.health < 35)
        support = isolated and threat_near
        attack = effective and not threat_near and len(priorities) > 0
        coordinate = supported and len(priorities) > 1
        change = stale

        return {
            "orient_depth": min(1.0, depth),
            "orient_factors": factors,
            "target_priorities": priorities,
            "combat_effective": effective,
            "threat_present": threat_present,
            "threat_near": threat_near,
            "team_supported": supported,
            "neighbor_count": nbr,
            "should_retreat": retreat,
            "should_seek_support": support,
            "should_attack": attack,
            "should_coordinate": coordinate,
            "should_change_approach": change,
            "can_engage_interceptor": can_engage_interceptor,
            "interceptor_target": interceptor_target,
            "decision_confidence": 0.85 if retreat else (0.7 if attack else 0.55),
            "action_effectiveness": 0.0,
            "orient_quality": min(1.0, depth),
        }

    async def _decide_and_act(self, tick: int, detected_targets: dict, env_targets: dict,
                              situation: dict = None):
        """决策与行动 — 基于 Orient 态势评估的智能决策"""
        if situation is None:
            situation = {"should_attack": True, "should_retreat": False, "target_priorities": [],
                         "combat_effective": True}

        state = self.state

        if state == UAVState.INIT:
            self.state = UAVState.IDLE

        elif state == UAVState.IDLE:
            # 优先级1: 拦截器在射程内→反击
            if situation.get("can_engage_interceptor") and situation.get("interceptor_target"):
                self.state = UAVState.ENGAGING
                await self._engage_interceptor(situation["interceptor_target"], tick)
            # 优先级2: 基于态势评估主动出击地面目标
            elif self.role in (UAVRole.STRIKER, UAVRole.LEADER) and situation.get("should_attack"):
                if situation.get("target_priorities"):
                    best_target = situation["target_priorities"][0][0]
                    if best_target in self.known_targets:
                        await self.broker.publish(
                            MessageType.ENGAGE_CMD, self.id,
                            {"target_id": best_target, "target_pos": self.known_targets[best_target]["pos"]},
                            target=self.id, priority=MessagePriority.HIGH, tick=tick,
                        )
                        self.messages_sent += 1
                        self._last_engage_tick = tick
                        self._last_engage_target_hp = self.known_targets[best_target].get("health", 100)
            # 优先级3: 有任务但未知目标位置→用任务自带的目标位置移动
            elif self.current_task and self.current_task.target_pos and not self.known_targets:
                self.target_pos = self.current_task.target_pos
                self.state = UAVState.MOVING
            # 优先级4: 无目标时主动搜索（朝地图右侧目标区域移动）
            elif not self.known_targets and not self.current_task:
                self.state = UAVState.SCOUTING if self.role == UAVRole.SCOUT else UAVState.MOVING
                # 搜索目标区域（地图右侧 50%-90% 范围）
                search_x = 5000 + random.uniform(1000, 4000)
                search_y = 3000 + random.uniform(-2000, 2000)
                self.target_pos = {"x": search_x, "y": search_y, "z": self.position.get("z", 200) + 100}
                # 共享搜索意图
                await self.broker.publish(MessageType.STATUS_REPORT, self.id,
                    {"status": "searching", "search_area": self.target_pos},
                    priority=MessagePriority.LOW, tick=tick)
                self.messages_sent += 1

        elif state in (UAVState.MOVING, UAVState.SCOUTING, UAVState.RETREATING):
            # Leader 保护：威胁逼近时立即规避（比普通UAV更早）
            if self.is_leader and situation.get("threat_present") and state != UAVState.RETREATING:
                self.state = UAVState.EVADING; self._evasion_tick = 0
                self.evasion_strategy = "speed"
                if not hasattr(self, '_pre_evasion_target') or not self._pre_evasion_target:
                    self._pre_evasion_target = dict(self.target_pos) if self.target_pos else None
                # 向地图左侧（安全区）撤退
                self.target_pos = {"x": 1000, "y": self.position["y"], "z": self.position.get("z", 200) + 150}
                return
            # 普通UAV：威胁逼近时转为规避
            elif situation.get("threat_near") and state != UAVState.RETREATING:
                self.state = UAVState.EVADING; self._evasion_tick = 0
                if not hasattr(self, '_pre_evasion_target') or not self._pre_evasion_target:
                    self._pre_evasion_target = dict(self.target_pos) if self.target_pos else None
                # 远离威胁方向
                if self.threat_interceptor_id:
                    self.target_pos = {
                        "x": self.position["x"] + (self.position["x"] - 5000) * 0.5,
                        "y": self.position["y"] + (self.position["y"] - 5000) * 0.5,
                        "z": self.position.get("z", 200) + 100,
                    }
                return

            if state == UAVState.MOVING and self.current_task and self.current_task.task_type == "strike":
                target = env_targets.get(self.current_task.target_id) if self.current_task.target_id else None
                if target and not target.get("destroyed"):
                    if distance(self.position, target["position"]) <= self.weapon_range:
                        self.state = UAVState.ENGAGING
                        await self._engage(detected_targets, env_targets, tick)
                        return
            await self._move_to_target(tick)
            if detected_targets and self.role == UAVRole.SCOUT:
                await self._share_target_info(detected_targets, tick)

        elif state == UAVState.ENGAGING:
            # 态势感知：交战中发现致命威胁→立即脱离
            if situation.get("should_retreat"):
                self.state = UAVState.RETREATING
                await self.broker.publish(MessageType.STATUS_REPORT, self.id,
                    {"status": "retreating", "health": self.health, "reason": "threat_overwhelming"},
                    priority=MessagePriority.HIGH, tick=tick)
                self.messages_sent += 1
                return
            # 优先攻击拦截器（如果可反击），再攻击地面目标
            if situation.get("can_engage_interceptor") and situation.get("interceptor_target"):
                await self._engage_interceptor(situation["interceptor_target"], tick)
            else:
                await self._engage(detected_targets, env_targets, tick)

        elif state == UAVState.RELAYING:
            await self._move_to_target(tick)

        elif state == UAVState.DAMAGED:
            # 态势感知：伤势严重→撤退；仍可战→继续
            if self.health < 20 or situation.get("should_retreat"):
                self.state = UAVState.RETREATING
                await self.broker.publish(MessageType.STATUS_REPORT, self.id,
                    {"status": "retreating", "health": self.health, "reason": "critical_damage"},
                    priority=MessagePriority.HIGH, tick=tick)
                self.messages_sent += 1
            elif self.current_task:
                await self._engage(detected_targets, env_targets, tick)
            else:
                await self._move_to_target(tick)

    async def _move_to_target(self, tick: int):
        """朝目标点移动"""
        if not self.target_pos:
            return

        dx = self.target_pos["x"] - self.position["x"]
        dy = self.target_pos["y"] - self.position["y"]
        dz = self.target_pos.get("z", self.position.get("z", 200)) - self.position.get("z", 200)
        dist = math.sqrt(dx * dx + dy * dy)

        if dist < 50:  # 到达目标点
            self.velocity = {"x": 0.0, "y": 0.0, "z": 0.0}
            if self.state == UAVState.MOVING:
                if self.current_task and self.current_task.task_type == "strike":
                    self.state = UAVState.ENGAGING
                elif self.current_task and self.current_task.task_type == "scout":
                    self.state = UAVState.SCOUTING
                else:
                    self.state = UAVState.IDLE
            elif self.state == UAVState.RETREATING:
                self.state = UAVState.IDLE
            return

        # 计算速度向量
        speed = self.max_speed * (0.5 if self.state == UAVState.DAMAGED else 1.0)
        vx = (dx / dist) * speed
        vy = (dy / dist) * speed
        vz = (dz / max(abs(dz), 1.0)) * speed * 0.2 if abs(dz) > 10 else 0.0

        self.velocity = {"x": vx, "y": vy, "z": vz}
        # 更新位置
        self.position["x"] += vx
        self.position["y"] += vy
        self.position["z"] = self.position.get("z", 200) + vz
        self.distance_traveled += speed

        # 更新姿态（基于速度方向计算偏航和俯仰角）
        self.attitude["yaw"] = math.degrees(math.atan2(vy, vx)) % 360
        if abs(vz) > 0.1:
            self.attitude["pitch"] = math.degrees(math.atan2(vz, speed)) * 0.3
        else:
            self.attitude["pitch"] *= 0.7  # 平滑回中
        self.attitude["roll"] = self.attitude["roll"] * 0.6  # 平滑回中

    async def _engage(self, detected_targets: dict, env_targets: dict, tick: int):
        """交战逻辑"""
        target_id = None
        target_pos = None

        # 优先攻击当前任务目标
        if self.current_task and self.current_task.target_id:
            target_id = self.current_task.target_id
            target = env_targets.get(target_id)
            if target and not target.get("destroyed"):
                target_pos = target["position"]
            else:
                # 目标已摧毁，任务完成
                if self.current_task:
                    self.current_task.status = "completed"
                    self.current_task.completed_tick = tick
                self.current_task = None
                self.state = UAVState.IDLE
                return
        elif detected_targets:
            # 攻击最近的目标
            target_id, target = min(
                detected_targets.items(),
                key=lambda x: distance(self.position, x[1]["position"]),
            )
            target_pos = target["position"]

        if not target_id or not target_pos:
            self.state = UAVState.IDLE
            return

        # 移动到武器射程内
        dist = distance(self.position, target_pos)
        effective_weapon_range = self.weapon_range * (0.7 if self.state == UAVState.DAMAGED else 1.0)

        if dist > effective_weapon_range:
            # 朝目标移动
            self.target_pos = target_pos
            await self._move_to_target(tick)
            self.state = UAVState.ENGAGING  # 保持交战状态
        else:
            # 在射程内，攻击
            self.targets_engaged += 1
            # 通过Broker广播攻击结果（其他UAV可以同步目标血量）
            await self.broker.publish(
                MessageType.ENGAGE_RESULT, self.id,
                {
                    "target_id": target_id,
                    "damage": self.weapon_damage,
                    "attacker": self.id,
                    "pos": dict(self.position),
                },
                target="",  # 广播给所有人
                priority=MessagePriority.HIGH,
                tick=tick,
            )
            self.messages_sent += 1

            # 共享目标信息
            await self.broker.publish(
                MessageType.TARGET_INFO, self.id,
                {
                    "target_id": target_id,
                    "pos": dict(target_pos),
                    "health": env_targets.get(target_id, {}).get("health", 100),
                    "type": env_targets.get(target_id, {}).get("type", "ground"),
                },
                priority=MessagePriority.MEDIUM,
                tick=tick,
            )
            self.messages_sent += 1

    async def _engage_interceptor(self, interceptor_id: str, tick: int):
        """攻击拦截器 — 蜂群对空反击"""
        if not hasattr(self, '_detected_interceptors') or interceptor_id not in self._detected_interceptors:
            return
        intr = self._detected_interceptors[interceptor_id]
        dist = distance(self.position, intr["position"])

        if dist > self.weapon_range:
            # 移动接近拦截器
            self.target_pos = intr["position"]
            await self._move_to_target(tick)
            self.state = UAVState.ENGAGING
            return

        # 在射程内，攻击拦截器
        self.targets_engaged += 1
        actual_damage = self.weapon_damage * (0.8 if self.role == UAVRole.STRIKER else 0.6)
        await self.broker.publish(
            "interceptor_damage", self.id,
            {
                "interceptor_id": interceptor_id,
                "damage": actual_damage,
                "attacker": self.id,
                "pos": dict(self.position),
            },
            priority=MessagePriority.HIGH,
            tick=tick,
        )
        self.messages_sent += 1

    async def _share_target_info(self, detected_targets: dict, tick: int):
        """共享目标信息给蜂群"""
        for tid, target in detected_targets.items():
            await self.broker.publish(
                MessageType.TARGET_INFO, self.id,
                {
                    "target_id": tid,
                    "pos": dict(target["position"]),
                    "health": target.get("health", 100),
                    "type": target.get("type", "ground"),
                },
                priority=MessagePriority.HIGH,
                tick=tick,
            )
            self.messages_sent += 1

    def _find_nearest_target(self) -> Optional[str]:
        """找到最近的已知目标"""
        if not self.known_targets:
            return None
        nearest_id = None
        nearest_dist = float("inf")
        for tid, info in self.known_targets.items():
            if info.get("health", 100) <= 0:
                continue
            dist = distance(self.position, info["pos"])
            if dist < nearest_dist:
                nearest_dist = dist
                nearest_id = tid
        return nearest_id

    async def _send_heartbeat(self, tick: int):
        """发送心跳"""
        self.last_heartbeat_tick = tick
        await self.broker.publish(
            MessageType.HEARTBEAT, self.id,
            {
                "uav_id": self.id,
                "position": dict(self.position),
                "state": self.state.name,
                "health": self.health,
                "battery": self.battery,
                "role": self.role.value,
                "task": self.current_task.task_id if self.current_task else None,
            },
            priority=MessagePriority.LOW,
            tick=tick,
        )
        self.messages_sent += 1

    async def take_damage(self, damage: float, tick: int):
        """受到伤害"""
        if not self.alive:
            return
        self.health -= damage
        self._last_damage_tick = tick
        if self.health <= 0:
            await self._destroy(tick, reason="destroyed_in_combat")
        elif self.health < 30:
            self.state = UAVState.DAMAGED

    async def _destroy(self, tick: int, reason: str = "unknown"):
        """无人机摧毁 - 触发重组网"""
        self.alive = False
        self.state = UAVState.DESTROYED
        self.health = 0
        self.velocity = {"x": 0.0, "y": 0.0, "z": 0.0}

        # 广播故障通知（高优先级，触发重组网）
        await self.broker.publish(
            MessageType.NODE_FAILURE, self.id,
            {
                "uav_id": self.id,
                "reason": reason,
                "last_position": dict(self.position),
                "tick": tick,
            },
            priority=MessagePriority.HIGH,
            tick=tick,
        )
        self.messages_sent += 1

    def get_status(self) -> dict:
        """获取Agent状态摘要"""
        return {
            "id": self.id,
            "role": self.role.value,
            "state": self.state.name,
            "position": dict(self.position),
            "velocity": dict(self.velocity),
            "attitude": dict(self.attitude),
            "health": round(self.health, 1),
            "battery": round(self.battery, 1),
            "alive": self.alive,
            "is_leader": self.is_leader,
            "leader_id": self.leader_id,
            "neighbors": list(self.neighbors),
            "current_task": self.current_task.task_id if self.current_task else None,
            "known_targets": len(self.known_targets),
            "targets_engaged": self.targets_engaged,
            "targets_destroyed": self.targets_destroyed,
            "distance_traveled": round(self.distance_traveled, 1),
            "messages_sent": self.messages_sent,
            "messages_received": self.messages_received,
            "detection_range": round(self.detection_range, 1),
            "weapon_range": round(self.weapon_range, 1),
            "max_speed": round(self.max_speed, 1),
            "camera_angle": self.camera_angle,
            "gps_signal": round(self.gps_signal, 1),
            "payload": dict(self.payload),
            "manual_mode": self.manual_mode,
            "threat_level": self.threat_level,
            "threat_distance": round(self.threat_distance, 1) if self.threat_distance != float("inf") else -1,
            "evasion_strategy": self.evasion_strategy,
        }
