"""
仿真引擎模块
=============
统筹整个仿真系统的运行：
- 物理仿真：UAV移动、碰撞检测、环境更新
- 交战模型：处理攻击-伤害计算、目标摧毁
- 损伤建模：UAV受损概率、环境威胁
- 时间步进：固定tick率驱动所有子系统
- 事件系统：随机故障注入、威胁生成
- 状态聚合：汇总所有子系统状态供可视化
- 并行计算：多进程UAV步进（大规模蜂群）
- 蒙特卡洛：批量仿真统计分析
"""
import asyncio
import random
import math
import time
import concurrent.futures
import multiprocessing
from collections import defaultdict
from typing import Optional

from config import (
    SIM_FPS,
    MAP_WIDTH,
    MAP_HEIGHT,
    DEFAULT_UAV_COUNT,
    MISSION_TARGET_COUNT,
    MISSION_TARGET_HEALTH,
    UAV_ROLES,
    UAV_HEALTH_MAX,
    distance,
)
from core.communication import AsyncMessageBroker, MessagePriority, MessageType
from core.uav_agent import UAVAgent, UAVRole, UAVState
from core.swarm_network import SwarmNetwork
from core.mission import MissionCoordinator
from core.parallel import compute_uav_step_batch, MonteCarloRunner
from core.environment_effects import EnvironmentEngine
from core.interceptor import InterceptorSwarm, ThreatAssessment, EvasionExecutor
from core.ooda_engine import OODAEngine, OODAPhase


class SimulationEngine:
    """
    仿真引擎 - 蜂群作战仿真核心
    
    架构层次：
    ┌─────────────────────────────────┐
    │        Visualization Layer       │ ← Web Dashboard
    ├─────────────────────────────────┤
    │       Simulation Engine          │ ← 本模块（时间驱动）
    ├───────────┬─────────┬───────────┤
    │  Mission  │  Swarm  │  Comm     │ ← 协调层
    │  Coord    │ Network │  Broker   │
    ├───────────┴─────────┴───────────┤
    │          UAV Agents              │ ← 执行层
    ├─────────────────────────────────┤
    │       Environment (Targets)      │ ← 物理层
    └─────────────────────────────────┘
    
    每个tick的执行流程：
    1. 通信Broker处理消息队列
    2. 每个UAV Agent执行感知-决策-行动
    3. 蜂群网络更新拓扑和检测故障
    4. 任务协调器更新任务状态
    5. 环境更新（目标状态、威胁）
    6. 随机事件注入（故障、威胁）
    7. 汇总状态输出
    """

    def __init__(self, uav_count: int = DEFAULT_UAV_COUNT, target_count: int = MISSION_TARGET_COUNT,
                 parallel_uavs: bool = False, max_workers: int = None,
                 enable_interceptors: bool = True, interceptor_difficulty: str = "normal",
                 llm_enabled: bool = False, llm_client=None):
        # 核心组件
        self.broker = AsyncMessageBroker()
        self.swarm_network = SwarmNetwork(self.broker)
        self.mission_coord = MissionCoordinator(self.broker)

        # 仿真状态
        self.uavs: dict[str, UAVAgent] = {}
        self.targets: dict = {}         # target_id -> target_info
        self.tick = 0
        self.running = False
        self.start_time = 0.0
        self.uav_count = uav_count
        self.target_count = target_count

        # 事件日志
        self.event_log: list = []
        self._max_log_size = 500

        # 故障注入参数
        self.fault_injection_enabled = False
        self.fault_injection_tick = 0   # 0=禁用，>0表示在第几tick注入故障
        self._fault_injected = False

        # 状态变化回调（供可视化层订阅）
        self._state_callbacks: list = []

        # 并行计算配置
        self._parallel_uavs = parallel_uavs
        self._max_workers = max_workers or max(1, multiprocessing.cpu_count() - 1)
        self._executor: concurrent.futures.ProcessPoolExecutor | None = None
        self._parallel_threshold = 20  # UAV数量超过此阈值才启用并行（避免小规模时的序列化开销）

        # 动态环境引擎
        self.environment: EnvironmentEngine | None = None
        self._environment_enabled = True

        # 拦截集群（敌方防御无人机）
        self.interceptor_swarm: InterceptorSwarm | None = None
        self._interceptor_enabled = enable_interceptors
        self._interceptor_difficulty = interceptor_difficulty

        # LLM增强
        self._llm_enabled = llm_enabled
        self._llm_client = llm_client
        self._evasion_executor = EvasionExecutor()

        # OODA 决策循环引擎
        self.ooda_engine = OODAEngine()
        self._ooda_enabled = True

        # 设置网络故障回调 -> 任务重分配
        self.swarm_network.on_node_failed_callback = self._on_node_failed

        # 订阅交战结果 - 仿真引擎需要更新自身目标血量
        self.broker.subscribe(MessageType.ENGAGE_RESULT, "sim_engine", self._on_engage_result)
        # 订阅拦截器伤害 - UAV 反击拦截器
        self.broker.subscribe(MessageType.INTERCEPTOR_DAMAGE, "sim_engine", self._on_interceptor_damage)

    def initialize(self):
        """初始化仿真环境"""
        # 初始化动态环境引擎
        if self._environment_enabled and self.environment is None:
            self.environment = EnvironmentEngine()

        # 初始化拦截集群
        if self._interceptor_enabled and self.interceptor_swarm is None:
            self.interceptor_swarm = InterceptorSwarm(
                unit_count=4,
                difficulty=self._interceptor_difficulty,
                ai_level="advanced",
            )
            self.interceptor_swarm.initialize()

        # 创建UAV
        roles = self._assign_roles(self.uav_count)
        base_x = MAP_WIDTH * 0.1
        base_y = MAP_HEIGHT * 0.5

        for i in range(self.uav_count):
            uav_id = f"UAV_{i+1:02d}"
            role = roles[i]
            # 编队起始位置
            angle = (i / self.uav_count) * 2 * math.pi
            radius = 200 + i * 30
            position = {
                "x": base_x + radius * math.cos(angle),
                "y": base_y + radius * math.sin(angle),
                "z": 200 + random.uniform(-50, 50),
            }
            # LLM增强模式：使用HybridUAVAgent (FSM + LLM)
            if self._llm_enabled and self._llm_client:
                from core.hybrid_uav import HybridUAVAgent
                uav = HybridUAVAgent(
                    uav_id=uav_id,
                    role=role,
                    position=position,
                    broker=self.broker,
                    llm_client=self._llm_client,
                    llm_enabled=True,
                )
            else:
                uav = UAVAgent(
                    uav_id=uav_id,
                    role=role,
                    position=position,
                    broker=self.broker,
                )
            uav.state = UAVState.IDLE
            uav._engine = self  # OODA阶段标注用
            self.uavs[uav_id] = uav
            self.swarm_network.register_uav(uav)

        # 设置初始领导者
        leader_uid = None
        for uid, uav in self.uavs.items():
            if uav.role == UAVRole.LEADER:
                leader_uid = uid
                uav.is_leader = True
                uav.leader_id = uid
                break
        if leader_uid:
            self.swarm_network.set_initial_leader(leader_uid)
            for uid, uav in self.uavs.items():
                uav.leader_id = leader_uid

        # 创建目标
        self._create_targets()

        # 创建任务并分配
        target_list = list(self.targets.values())
        asyncio.ensure_future(self.mission_coord.create_mission(target_list, self.uavs, 0))

        self._log("SYSTEM", "仿真环境初始化完成", {
            "uav_count": self.uav_count,
            "target_count": self.target_count,
            "leader": leader_uid,
        })

    def _assign_roles(self, count: int) -> list:
        """分配UAV角色"""
        roles = []
        if count >= 1:
            roles.append(UAVRole.LEADER)
        scout_count = max(1, count // 4)
        striker_count = max(2, count // 2)
        relay_count = max(1, count // 5)

        roles.extend([UAVRole.SCOUT] * scout_count)
        roles.extend([UAVRole.STRIKER] * striker_count)
        roles.extend([UAVRole.RELAY] * relay_count)

        # 补充到指定数量
        while len(roles) < count:
            roles.append(UAVRole.STRIKER)
        return roles[:count]

    def _create_targets(self):
        """创建作战目标"""
        for i in range(self.target_count):
            target_id = f"TARGET_{i+1:02d}"
            self.targets[target_id] = {
                "id": target_id,
                "position": {
                    "x": random.uniform(MAP_WIDTH * 0.5, MAP_WIDTH * 0.9),
                    "y": random.uniform(MAP_HEIGHT * 0.2, MAP_HEIGHT * 0.8),
                    "z": 0,
                },
                "health": MISSION_TARGET_HEALTH,
                "max_health": MISSION_TARGET_HEALTH,
                "type": random.choice(["command_post", "radar_station", "vehicle_convoy"]),
                "destroyed": False,
                "defense_range": 2000.0,   # 防御范围
                "defense_damage": 4.0,     # 每tick对范围内UAV的伤害
            }

    async def _on_node_failed(self, failed_id: str, tick: int):
        """节点故障回调 - 触发任务重分配"""
        reassigned = await self.mission_coord.handle_uav_failure(failed_id, self.uavs, tick)
        self._log("RECONNECT", f"节点 {failed_id} 故障，任务重分配", {
            "failed_node": failed_id,
            "reassigned_tasks": reassigned,
            "alive_uavs": sum(1 for u in self.uavs.values() if u.alive),
            "tick": tick,
        })

    async def _on_engage_result(self, msg_type: str, sender: str, payload: dict, tick: int):
        """处理交战结果 - 更新仿真引擎自身的目标血量"""
        target_id = payload.get("target_id")
        damage = payload.get("damage", 0)
        if target_id and target_id in self.targets:
            target = self.targets[target_id]
            if not target.get("destroyed"):
                target["health"] = max(0, target["health"] - damage)
                if target["health"] <= 0:
                    target["destroyed"] = True
                    self._log("MISSION", f"目标 {target_id} 已被摧毁!", {
                        "target_id": target_id,
                        "destroyed_by": payload.get("attacker", sender),
                        "tick": tick,
                    })
                    # 统计击杀
                    attacker_id = payload.get("attacker", sender)
                    if attacker_id in self.uavs:
                        self.uavs[attacker_id].targets_destroyed += 1

    async def _on_interceptor_damage(self, msg_type: str, sender: str, payload: dict, tick: int):
        """处理UAV对拦截器的反击伤害"""
        if not self.interceptor_swarm:
            return
        interceptor_id = payload.get("interceptor_id")
        damage = payload.get("damage", 0)
        if interceptor_id and interceptor_id in self.interceptor_swarm.units:
            intr = self.interceptor_swarm.units[interceptor_id]
            if intr.alive:
                intr.health -= damage
                if intr.health <= 0:
                    intr.alive = False
                    from core.interceptor import InterceptorState
                    intr.state = InterceptorState.DESTROYED
                    self._log("MISSION", f"拦截器 {interceptor_id} 被 {sender} 击落!", {
                        "interceptor_id": interceptor_id,
                        "destroyed_by": sender,
                        "tick": tick,
                    })
                    if sender in self.uavs:
                        self.uavs[sender].targets_destroyed += 1  # 计入战果

    async def _parallel_uav_step(self):
        """
        并行UAV步进 — 使用ProcessPoolExecutor分配计算到多核

        仅当 UAV 数量 >= _parallel_threshold 时使用，
        避免小规模时序列化开销超过并行收益。
        """
        if not self._executor:
            self._executor = concurrent.futures.ProcessPoolExecutor(
                max_workers=self._max_workers
            )

        # 准备可序列化的UAV状态
        uav_states = []
        alive_uav_ids = []
        for uid, uav in self.uavs.items():
            if uav.alive:
                alive_uav_ids.append(uid)
                uav_states.append({
                    "uav_id": uav.id,
                    "role": uav.role.value,
                    "position": dict(uav.position),
                    "health": uav.health,
                    "battery": uav.battery,
                    "alive": uav.alive,
                    "state": uav.state.name,
                    "max_speed": uav.max_speed,
                    "detection_range": uav.detection_range,
                    "weapon_range": uav.weapon_range,
                    "weapon_damage": uav.weapon_damage,
                    "target_pos": dict(uav.target_pos) if uav.target_pos else None,
                    "current_task_type": uav.current_task.task_type if uav.current_task else None,
                    "current_task_target_id": uav.current_task.target_id if uav.current_task else None,
                    "is_leader": uav.is_leader,
                    "leader_id": uav.leader_id,
                    "known_targets": {
                        tid: {"pos": dict(info["pos"]), "health": info["health"], "type": info["type"]}
                        for tid, info in uav.known_targets.items()
                    },
                    "heartbeat_counter": uav.heartbeat_counter,
                    "targets_engaged": uav.targets_engaged,
                    "distance_traveled": uav.distance_traveled,
                })

        # 准备配置
        sim_config = {
            "battery_drain": 0.05,
            "health_max": 100.0,
            "heartbeat_interval": 2,
        }

        # 准备目标（序列化）
        serializable_targets = {
            tid: {
                "id": t["id"],
                "position": dict(t["position"]),
                "health": t["health"],
                "max_health": t["max_health"],
                "type": t["type"],
                "destroyed": t["destroyed"],
            }
            for tid, t in self.targets.items()
        }

        # 并行执行
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            self._executor,
            compute_uav_step_batch,
            uav_states,
            self.tick,
            serializable_targets,
            sim_config,
        )

        # 应用结果 + 发送消息
        for result in results:
            uid = result["uav_id"]
            if uid not in self.uavs:
                continue
            uav = self.uavs[uid]

            # 应用状态
            uav.position = result["position"]
            uav.velocity = result["velocity"]
            try:
                uav.state = UAVState[result["state"]]
            except (KeyError, ValueError):
                pass
            uav.health = result["health"]
            uav.battery = result["battery"]
            uav.alive = result["alive"]
            if result.get("target_pos"):
                uav.target_pos = result["target_pos"]
            uav.distance_traveled = result.get("distance_traveled", uav.distance_traveled)
            uav.targets_engaged = result.get("targets_engaged", uav.targets_engaged)
            uav.heartbeat_counter = result.get("heartbeat_counter", uav.heartbeat_counter)

            # 更新已知目标
            for tid, info in result.get("known_targets", {}).items():
                if tid not in uav.known_targets or uav.known_targets[tid].get("health") != info.get("health"):
                    uav.known_targets[tid] = info

            # 发送待发消息
            for msg in result.get("pending_messages", []):
                if "tick" in msg and msg["tick"] == 0:
                    msg["tick"] = self.tick
                priority_map = {0: MessagePriority.HIGH, 1: MessagePriority.MEDIUM, 2: MessagePriority.LOW}
                priority = priority_map.get(msg.get("priority", 1), MessagePriority.MEDIUM)

                await self.broker.publish(
                    msg_type=msg["msg_type"],
                    sender=msg["sender"],
                    payload=msg["payload"],
                    target=msg.get("target", ""),
                    priority=priority,
                    tick=msg.get("tick", self.tick),
                )
                uav.messages_sent += 1

            # 发送目标信息共享
            for st in result.get("shared_targets", []):
                await self.broker.publish(
                    "target_info", uid,
                    {
                        "target_id": st["target_id"],
                        "pos": st["pos"],
                        "health": st["health"],
                        "type": st["type"],
                    },
                    priority=MessagePriority.HIGH,
                    tick=self.tick,
                )
                uav.messages_sent += 1

    async def step(self):
        """
        执行一个仿真tick
        
        执行顺序：
        1. 设置broker当前tick
        2. 处理通信消息
        3. 每个UAV执行感知-决策-行动
        4. 更新蜂群网络拓扑
        5. 处理交战结果
        6. 更新任务状态
        7. 故障注入
        8. 汇总状态
        """
        self.tick += 1
        self.broker.set_tick(self.tick)

        # 0. 更新动态环境
        if self._environment_enabled and self.environment:
            self.environment.update(self.tick)

        # 0.5 更新拦截集群
        if self._interceptor_enabled and self.interceptor_swarm:
            self.interceptor_swarm.update(self.tick, self.uavs)

            # 威胁评估：对每个存活的UAV进行威胁评估
            alive_uavs_list = [u for u in self.uavs.values() if u.alive]
            for uav in alive_uavs_list:
                if uav.state.name in ("DESTROYED", "MANUAL"):
                    continue
                threat = self.interceptor_swarm.assess_threat(uav.position, uav.id)
                uav.set_threat(threat)

        # 1. 处理通信消息队列
        await self.broker.process_batch()

        # 2. 每个UAV执行step（大规模蜂群自动启用并行模式）
        alive_count = sum(1 for u in self.uavs.values() if u.alive)
        if self._parallel_uavs and alive_count >= self._parallel_threshold:
            await self._parallel_uav_step()
        else:
            uav_tasks = []
            for uid, uav in self.uavs.items():
                if uav.alive:
                    uav_tasks.append(uav.step(self.tick, self.targets, self.interceptor_swarm))
            if uav_tasks:
                await asyncio.gather(*uav_tasks, return_exceptions=True)

        # 3. 更新蜂群网络拓扑
        await self.swarm_network.update_topology(self.tick, self.uavs)

        # 4. 处理交战结果 - 目标受到的伤害
        await self._process_combat()

        # 5. 更新任务状态 & 周期性任务重分配（每10 tick检查空闲UAV）
        self.mission_coord.update_task_status(self.tick)
        if self.tick % 10 == 0:
            await self.mission_coord._assign_tasks(self.uavs, self.tick)

        # 6. 故障注入
        await self._inject_faults()

        # 7. 检查任务完成
        if self.mission_coord.check_mission_complete() and self.mission_coord.mission_status != "completed":
            self.mission_coord.mission_status = "completed"
            self._log("MISSION", "所有目标已摧毁，任务完成!", {"tick": self.tick})
            self.running = False

        # 7.5 OODA 循环跟踪
        if self._ooda_enabled:
            self._track_ooda_cycles()

        # 8. 通知状态变化
        state = self.get_state()
        for callback in self._state_callbacks:
            try:
                await callback(state)
            except Exception:
                pass

    async def _process_combat(self):
        """处理交战 - 目标防御反击"""
        for target in self.targets.values():
            if target.get("destroyed"):
                continue
            target_pos = target["position"]
            defense_range = target.get("defense_range", 0)
            defense_damage = target.get("defense_damage", 0)

            if defense_range <= 0 or defense_damage <= 0:
                continue

            # 对防御范围内的UAV造成伤害
            for uid, uav in self.uavs.items():
                if not uav.alive:
                    continue
                dist = distance(uav.position, target_pos)
                if dist <= defense_range:
                    # 距离越近伤害越高
                    damage = defense_damage * (1 - dist / defense_range) * 0.5
                    if damage > 0:
                        await uav.take_damage(damage, self.tick)

    async def _inject_faults(self):
        """故障注入 - 模拟随机无人机故障"""
        if not self.fault_injection_enabled:
            return

        # 定时故障注入
        if self.fault_injection_tick > 0 and self.tick >= self.fault_injection_tick and not self._fault_injected:
            alive_uavs = [u for u in self.uavs.values() if u.alive]
            if alive_uavs:
                # 随机选择一架UAV摧毁（不选leader，测试非leader故障）
                non_leader = [u for u in alive_uavs if not u.is_leader]
                target_uav = random.choice(non_leader) if non_leader else random.choice(alive_uavs)
                await target_uav._destroy(self.tick, reason="fault_injection")
                self._fault_injected = True
                self._log("FAULT", f"故障注入: {target_uav.id} 被摧毁", {
                    "uav_id": target_uav.id,
                    "role": target_uav.role.value,
                    "tick": self.tick,
                })

    def enable_fault_injection(self, tick: int = 0):
        """启用故障注入"""
        self.fault_injection_enabled = True
        self.fault_injection_tick = tick
        self._fault_injection_tick = tick
        self._log("SYSTEM", f"故障注入已启用，将在tick={tick}时触发", {})

    async def destroy_uav(self, uav_id: str, reason: str = "manual"):
        """手动摧毁UAV（供API调用）"""
        if uav_id in self.uavs and self.uavs[uav_id].alive:
            await self.uavs[uav_id]._destroy(self.tick, reason)
            self._log("FAULT", f"手动摧毁: {uav_id}", {"reason": reason, "tick": self.tick})

    def _log(self, category: str, message: str, data: dict = None):
        """记录事件日志"""
        entry = {
            "tick": self.tick,
            "timestamp": time.time(),
            "category": category,
            "message": message,
            "data": data or {},
        }
        self.event_log.append(entry)
        if len(self.event_log) > self._max_log_size:
            self.event_log = self.event_log[-self._max_log_size:]

    def _track_ooda_cycles(self):
        """跟踪各UAV的OODA循环"""
        for uid, uav in self.uavs.items():
            if not uav.alive:
                continue

            # 为每个UAV维护一个持续的OODA循环
            if uid not in self.ooda_engine.current:
                self.ooda_engine.start_cycle(uid, uav.role.value, self.tick)

            # 基于UAV状态推断当前OODA阶段
            state_name = uav.state.name
            if state_name in ("INIT", "IDLE"):
                phase = OODAPhase.OBSERVE
            elif state_name in ("MOVING", "SCOUTING", "DAMAGED"):
                phase = OODAPhase.ORIENT
            elif state_name in ("ENGAGING", "EVADING", "RETREATING"):
                phase = OODAPhase.DECIDE
            elif state_name == "MANUAL":
                phase = OODAPhase.ACT
            else:
                phase = OODAPhase.OBSERVE

            self.ooda_engine.mark_phase(uid, phase, self.tick)

            # 每20个tick完成一个循环并开始新的
            cycle_age = self.tick - self.ooda_engine.current[uid].tick_start
            if cycle_age >= 20:
                self.ooda_engine.complete_cycle(
                    uid, self.tick,
                    success=uav.alive and uav.health > 30,
                    feedback=f"State={state_name}, HP={uav.health:.0f}%",
                )
                self.ooda_engine.start_cycle(uid, uav.role.value, self.tick)

        # 计算OODA优势
        blue_ids = [uid for uid, u in self.uavs.items() if u.alive]
        red_ids = [
            uid for uid, intr in self.interceptor_swarm.units.items()
            if intr.alive
        ] if self.interceptor_swarm else []
        if blue_ids and red_ids:
            self.ooda_engine.compute_ooda_advantage(blue_ids, red_ids)

    def add_state_callback(self, callback):
        """添加状态变化回调（供可视化层订阅）"""
        self._state_callbacks.append(callback)

    def get_state(self) -> dict:
        """获取完整仿真状态"""
        alive_count = sum(1 for u in self.uavs.values() if u.alive)
        state = {
            "tick": self.tick,
            "running": self.running,
            "elapsed_time": round(time.time() - self.start_time, 1) if self.start_time else 0,
            "uavs": {uid: uav.get_status() for uid, uav in self.uavs.items()},
            "targets": {
                tid: {
                    "id": t["id"],
                    "position": dict(t["position"]),
                    "health": round(t["health"], 1),
                    "max_health": t["max_health"],
                    "type": t["type"],
                    "destroyed": t["destroyed"],
                }
                for tid, t in self.targets.items()
            },
            "network": self.swarm_network.get_network_status(),
            "mission": self.mission_coord.get_mission_status(),
            "communication": self.broker.get_stats(),
            "summary": {
                "total_uavs": len(self.uavs),
                "alive_uavs": alive_count,
                "destroyed_uavs": len(self.uavs) - alive_count,
                "total_targets": len(self.targets),
                "destroyed_targets": sum(1 for t in self.targets.values() if t["destroyed"]),
                "reconnect_count": self.swarm_network._reconnect_count,
                "leader_changes": self.swarm_network._leader_changes,
                "leader_id": self.swarm_network.leader_id,
            },
            "events": self.event_log[-50:],  # 最近50条事件
            "environment": self.environment.get_state() if self.environment else {},
            "interceptors": self.interceptor_swarm.get_state() if self.interceptor_swarm else {},
            "ooda": self.ooda_engine.get_ooda_state() if self._ooda_enabled else {},
        }
        return state

    async def run(self, max_ticks: int = 0):
        """运行仿真"""
        self.running = True
        self.start_time = time.time()
        self._log("SYSTEM", "仿真开始", {"uav_count": self.uav_count})

        interval = 1.0 / SIM_FPS
        while self.running:
            await self.step()
            if max_ticks > 0 and self.tick >= max_ticks:
                self.running = False
                self._log("SYSTEM", f"仿真结束（达到最大tick数 {max_ticks}）", {})
            if self.mission_coord.mission_status == "completed":
                self.running = False
                self._log("SYSTEM", "任务完成，仿真结束", {"total_ticks": self.tick})
                break
            await asyncio.sleep(interval)

    async def run_steps(self, steps: int = 1):
        """运行指定步数（供手动控制，始终允许）"""
        for _ in range(steps):
            await self.step()

    def shutdown(self):
        """关闭仿真引擎，清理进程池"""
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    @staticmethod
    def run_monte_carlo(
        num_runs: int = 100,
        uav_count: int = 8,
        target_count: int = 3,
        max_ticks: int = 500,
        fault_tick: int = 0,
        varied_params: dict = None,
        max_workers: int = None,
    ) -> dict:
        """
        运行蒙特卡洛批量仿真（静态方法，可直接调用）

        使用示例:
            stats = SimulationEngine.run_monte_carlo(
                num_runs=50, uav_count=8, target_count=3,
                varied_params={"fault_tick": [0, 30, 60]},
            )
            print(f"任务成功率: {stats['success_rate']:.1%}")

        Returns:
            统计分析结果字典
        """
        runner = MonteCarloRunner(max_workers=max_workers)
        import os
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return runner.run_batch(
            num_runs=num_runs,
            uav_count=uav_count,
            target_count=target_count,
            max_ticks=max_ticks,
            fault_tick=fault_tick,
            varied_params=varied_params,
            project_root=project_root,
        )
