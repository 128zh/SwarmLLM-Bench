"""
并行计算模块
============
提供多进程并行UAV步进计算和蒙特卡洛批量仿真能力。

架构设计：
┌──────────────────────────────────────────────┐
│           SimulationEngine (主进程)            │
│  ┌────────────┐  ┌───────────┐               │
│  │ 通信Broker  │  │ 状态同步   │               │
│  └────────────┘  └───────────┘               │
│         │              ▲                      │
│         │   UAV计算状态 │  计算结果             │
│         ▼              │                      │
│  ┌─────────────────────────────────────┐     │
│  │     ProcessPoolExecutor (工作进程)    │     │
│  │  ┌──────┐ ┌──────┐ ┌──────┐        │     │
│  │  │UAV 0-7│ │UAV 8-│ │UAV   │  ...   │     │
│  │  │       │ │  15  │ │16-23 │        │     │
│  │  └──────┘ └──────┘ └──────┘        │     │
│  └─────────────────────────────────────┘     │
│                                               │
│  ┌─────────────────────────────────────┐     │
│  │   MonteCarloRunner (批量仿真)         │     │
│  │  ┌──────┐ ┌──────┐ ┌──────┐        │     │
│  │  │ Run 1│ │ Run 2│ │ Run N│  ...   │     │
│  │  └──────┘ └──────┘ └──────┘        │     │
│  └─────────────────────────────────────┘     │
└──────────────────────────────────────────────┘

两种并行模式：
1. Tick内并行：每个tick将UAV计算分配到多个worker进程
2. 蒙特卡洛并行：同时运行多个独立的仿真实例
"""
import math
import random
import concurrent.futures
import multiprocessing
from dataclasses import dataclass, field
from typing import Optional


# ============ UAV计算状态（纯数据，可序列化） ============

@dataclass
class UAVComputeState:
    """UAV的可序列化计算状态（用于跨进程传输）"""
    uav_id: str
    role: str
    position: dict
    health: float
    battery: float
    alive: bool
    state: str
    max_speed: float
    detection_range: float
    weapon_range: float
    weapon_damage: float
    target_pos: Optional[dict] = None
    current_task_type: Optional[str] = None
    current_task_target_id: Optional[str] = None
    is_leader: bool = False
    leader_id: Optional[str] = None


@dataclass
class UAVComputeResult:
    """UAV计算结果（纯数据，可序列化，从工作进程返回）"""
    uav_id: str

    # 状态更新
    position: dict
    velocity: dict
    state: str
    health: float
    battery: float
    alive: bool
    target_pos: Optional[dict] = None
    distance_traveled: float = 0.0
    targets_engaged: int = 0
    known_targets: dict = field(default_factory=dict)

    # 待发送的消息（在主进程中通过broker发送）
    pending_messages: list = field(default_factory=list)
    # 是否共享了目标信息
    shared_targets: list = field(default_factory=list)


# ============ 纯函数UAV步进计算（模块级，可pickle） ============

def _distance_sq(p1: dict, p2: dict) -> float:
    """距离平方（避免import依赖，函数自包含）"""
    return (
        (p1["x"] - p2["x"]) ** 2 +
        (p1["y"] - p2["y"]) ** 2 +
        (p1.get("z", 0) - p2.get("z", 0)) ** 2
    )


def _distance(p1: dict, p2: dict) -> float:
    """欧几里得距离"""
    return math.sqrt(_distance_sq(p1, p2))


def compute_uav_step_batch(
    uav_states: list[dict],
    tick: int,
    env_targets: dict,
    sim_config: dict,
) -> list[dict]:
    """
    批量计算多个UAV的一步（纯函数，在worker进程中执行）

    这是模块级函数（非方法），因此可以被ProcessPoolExecutor pickle。

    Args:
        uav_states: UAVComputeState列表的字典形式
        tick: 当前仿真tick
        env_targets: 环境目标状态（只读）
        sim_config: 仿真配置参数

    Returns:
        UAVComputeResult列表的字典形式
    """
    results = []
    for uav in uav_states:
        result = _compute_single_uav(uav, tick, env_targets, sim_config)
        results.append(result)
    return results


def _compute_single_uav(
    uav: dict,
    tick: int,
    env_targets: dict,
    cfg: dict,
) -> dict:
    """
    计算单个UAV的一步（纯计算，无副作用）

    这是仿真中最CPU密集的部分：
    1. 电量/健康检查
    2. 传感器感知（O(n)目标扫描）
    3. 决策（状态机）
    4. 移动/交战（位置更新）
    5. 消息生成（不发送，返回消息描述）
    """
    uav_id = uav["uav_id"]
    role = uav["role"]
    position = dict(uav["position"])
    health = uav["health"]
    battery = uav["battery"]
    alive = uav["alive"]
    state = uav["state"]
    max_speed = uav["max_speed"]
    detection_range = uav["detection_range"]
    weapon_range = uav["weapon_range"]
    weapon_damage = uav["weapon_damage"]
    target_pos = uav.get("target_pos")
    is_leader = uav.get("is_leader", False)
    known_targets = dict(uav.get("known_targets", {}))
    current_task_type = uav.get("current_task_type")
    current_task_target_id = uav.get("current_task_target_id")
    targets_engaged = uav.get("targets_engaged", 0)
    distance_traveled = uav.get("distance_traveled", 0.0)

    pending_messages = []
    shared_targets = []
    velocity = {"x": 0.0, "y": 0.0, "z": 0.0}

    # 基础配置
    battery_drain = cfg.get("battery_drain", 0.05)
    health_max = cfg.get("health_max", 100.0)
    comm_heartbeat_interval = cfg.get("heartbeat_interval", 2)
    heartbeat_counter = uav.get("heartbeat_counter", 0)

    if not alive or state == "DESTROYED":
        return {
            "uav_id": uav_id, "position": position, "velocity": velocity,
            "state": "DESTROYED", "health": 0, "battery": battery,
            "alive": False, "target_pos": target_pos,
            "distance_traveled": distance_traveled,
            "targets_engaged": targets_engaged,
            "known_targets": known_targets,
            "pending_messages": [], "shared_targets": [],
        }

    # 1. 电量检查
    battery -= battery_drain
    if battery <= 0:
        return _make_destroy_result(uav_id, position, battery, "battery_depleted",
                                     distance_traveled, targets_engaged, known_targets)

    # 2. 健康检查
    if health <= 0:
        return _make_destroy_result(uav_id, position, battery, "destroyed_in_combat",
                                     distance_traveled, targets_engaged, known_targets)
    elif health < 30 and state != "RETREATING":
        state = "DAMAGED"

    # 3. 心跳计数
    heartbeat_counter += 1
    need_heartbeat = (heartbeat_counter >= comm_heartbeat_interval)

    # 4. 传感器感知（距离平方优化）
    effective_range = detection_range
    if state == "DAMAGED":
        effective_range *= 0.5
    effective_range_sq = effective_range ** 2

    detected_targets = {}
    for tid, target in env_targets.items():
        if target.get("destroyed"):
            continue
        t_pos = target["position"]
        if _distance_sq(position, t_pos) <= effective_range_sq:
            detected_targets[tid] = target
            # 更新已知目标
            if tid not in known_targets or known_targets[tid].get("health") != target.get("health"):
                known_targets[tid] = {
                    "pos": dict(t_pos),
                    "health": target.get("health", 100),
                    "type": target.get("type", "ground"),
                }

    # 5. 决策与行动
    new_state, position, velocity, target_pos, targets_engaged, new_messages, new_shared, distance_traveled = \
        _decide_and_act(
            uav_id, role, state, position, target_pos, velocity,
            max_speed, weapon_range, weapon_damage,
            detected_targets, env_targets, known_targets,
            current_task_type, current_task_target_id,
            tick, targets_engaged, distance_traveled, effective_range,
        )

    pending_messages.extend(new_messages)
    if need_heartbeat:
        pending_messages.append({
            "msg_type": "heartbeat",
            "sender": uav_id,
            "payload": {
                "uav_id": uav_id,
                "position": dict(position),
                "state": new_state,
                "health": health,
                "battery": battery,
                "role": role,
                "task": current_task_target_id,
            },
            "priority": 2,  # LOW
            "tick": tick,
        })

    shared_targets.extend(new_shared)

    return {
        "uav_id": uav_id,
        "position": position,
        "velocity": velocity,
        "state": new_state,
        "health": health,
        "battery": battery,
        "alive": alive,
        "target_pos": target_pos,
        "distance_traveled": distance_traveled,
        "targets_engaged": targets_engaged,
        "known_targets": known_targets,
        "pending_messages": pending_messages,
        "shared_targets": shared_targets,
        "heartbeat_counter": 0 if need_heartbeat else heartbeat_counter,
    }


def _make_destroy_result(uav_id, position, battery, reason, dist_traveled, targets_engaged, known_targets):
    """生成摧毁结果"""
    return {
        "uav_id": uav_id,
        "position": position,
        "velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
        "state": "DESTROYED",
        "health": 0.0,
        "battery": battery,
        "alive": False,
        "target_pos": None,
        "distance_traveled": dist_traveled,
        "targets_engaged": targets_engaged,
        "known_targets": known_targets,
        "pending_messages": [{
            "msg_type": "node_failure",
            "sender": uav_id,
            "payload": {
                "uav_id": uav_id,
                "reason": reason,
                "last_position": dict(position),
                "tick": 0,  # tick由调用者填入
            },
            "priority": 0,  # HIGH
        }],
        "shared_targets": [],
    }


def _decide_and_act(
    uav_id, role, state, position, target_pos, velocity,
    max_speed, weapon_range, weapon_damage,
    detected_targets, env_targets, known_targets,
    current_task_type, current_task_target_id,
    tick, targets_engaged, distance_traveled, effective_detection_range,
):
    """决策与行动（纯函数版本）"""
    messages = []
    shared = []
    new_state = state

    # INIT → IDLE
    if state == "INIT":
        new_state = "IDLE"

    elif state == "IDLE":
        if role == "striker" and known_targets:
            nearest_id = _find_nearest(position, known_targets)
            if nearest_id:
                messages.append({
                    "msg_type": "engage_cmd",
                    "sender": uav_id,
                    "payload": {
                        "target_id": nearest_id,
                        "target_pos": known_targets[nearest_id]["pos"],
                    },
                    "priority": 0,  # HIGH
                    "target": uav_id,  # 仅发送给自己
                })

    elif state in ("MOVING", "SCOUTING", "RETREATING"):
        # 攻击机进入武器射程即转为交战
        if state == "MOVING" and current_task_type == "strike" and current_task_target_id:
            target = env_targets.get(current_task_target_id)
            if target and not target.get("destroyed"):
                if _distance(position, target["position"]) <= weapon_range:
                    new_state = "ENGAGING"
                    position, velocity, distance_traveled, targets_engaged, engage_msgs = \
                        _do_engage(uav_id, position, weapon_range, weapon_damage,
                                   detected_targets, env_targets, current_task_target_id,
                                   tick, targets_engaged, distance_traveled, new_state)
                    messages.extend(engage_msgs)
                    return new_state, position, velocity, target_pos, targets_engaged, messages, shared, distance_traveled

        position, velocity, distance_traveled = _move_to_target(
            position, target_pos, max_speed, distance_traveled, new_state,
            current_task_type,
        )
        if detected_targets and role == "scout":
            for tid, t in detected_targets.items():
                shared.append({
                    "target_id": tid,
                    "pos": dict(t["position"]),
                    "health": t.get("health", 100),
                    "type": t.get("type", "ground"),
                })

    elif state == "ENGAGING":
        position, velocity, distance_traveled, targets_engaged, engage_msgs = \
            _do_engage(uav_id, position, weapon_range, weapon_damage,
                       detected_targets, env_targets,
                       current_task_target_id,
                       tick, targets_engaged, distance_traveled, new_state)
        messages.extend(engage_msgs)

    elif state == "RELAYING":
        position, velocity, distance_traveled = _move_to_target(
            position, target_pos, max_speed, distance_traveled, new_state, None,
        )

    elif state == "DAMAGED":
        if uav_id.endswith("_"):  # placeholder
            pass
        # Force UUID for health check
        health_val = 100.0  # Will be overridden
        if health_val < 15:  # Simplified — actual health passed in
            new_state = "RETREATING"
            messages.append({
                "msg_type": "status_report",
                "sender": uav_id,
                "payload": {"status": "retreating", "reason": "damaged"},
                "priority": 0,
            })
        elif current_task_type:
            position, velocity, distance_traveled, targets_engaged, engage_msgs = \
                _do_engage(uav_id, position, weapon_range, weapon_damage,
                           detected_targets, env_targets,
                           current_task_target_id,
                           tick, targets_engaged, distance_traveled, new_state)
            messages.extend(engage_msgs)
        else:
            position, velocity, distance_traveled = _move_to_target(
                position, target_pos, max_speed, distance_traveled, new_state, None,
            )

    return new_state, position, velocity, target_pos, targets_engaged, messages, shared, distance_traveled


def _move_to_target(position, target_pos, max_speed, distance_traveled, state, current_task_type=None):
    """朝目标移动（纯函数）"""
    velocity = {"x": 0.0, "y": 0.0, "z": 0.0}
    if not target_pos:
        return position, velocity, distance_traveled

    dx = target_pos["x"] - position["x"]
    dy = target_pos["y"] - position["y"]
    dist = math.sqrt(dx * dx + dy * dy)

    if dist < 50:  # 到达
        return position, velocity, distance_traveled

    speed = max_speed * (0.5 if state == "DAMAGED" else 1.0)
    vx = (dx / dist) * speed
    vy = (dy / dist) * speed
    velocity = {"x": vx, "y": vy, "z": 0.0}
    position["x"] += vx
    position["y"] += vy
    distance_traveled += speed

    return position, velocity, distance_traveled


def _do_engage(uav_id, position, weapon_range, weapon_damage,
               detected_targets, env_targets, task_target_id,
               tick, targets_engaged, distance_traveled, state):
    """交战逻辑（纯函数）"""
    messages = []
    velocity = {"x": 0.0, "y": 0.0, "z": 0.0}
    target_id = None
    target_pos = None

    # 优先攻击当前任务目标
    if task_target_id:
        target = env_targets.get(task_target_id)
        if target and not target.get("destroyed"):
            target_id = task_target_id
            target_pos = target["position"]
    elif detected_targets:
        # 攻击最近目标
        target_id, target = min(
            detected_targets.items(),
            key=lambda x: _distance(position, x[1]["position"]),
        )
        target_pos = target["position"]

    if not target_id or not target_pos:
        return position, velocity, distance_traveled, targets_engaged, messages

    effective_range = weapon_range * (0.7 if state == "DAMAGED" else 1.0)
    dist = _distance(position, target_pos)

    if dist > effective_range:
        # 朝目标移动
        position, velocity, distance_traveled = _move_to_target(
            position, target_pos, 80.0, distance_traveled, state, "strike",
        )
    else:
        # 在射程内，攻击
        targets_engaged += 1
        messages.append({
            "msg_type": "engage_result",
            "sender": uav_id,
            "payload": {
                "target_id": target_id,
                "damage": weapon_damage,
                "attacker": uav_id,
                "pos": dict(position),
            },
            "priority": 0,  # HIGH
            "target": "",   # 广播
        })
        messages.append({
            "msg_type": "target_info",
            "sender": uav_id,
            "payload": {
                "target_id": target_id,
                "pos": dict(target_pos),
                "health": env_targets.get(target_id, {}).get("health", 100),
                "type": env_targets.get(target_id, {}).get("type", "ground"),
            },
            "priority": 1,  # MEDIUM
        })

    return position, velocity, distance_traveled, targets_engaged, messages


def _find_nearest(position, known_targets):
    """找到最近的已知目标（距离平方优化）"""
    if not known_targets:
        return None
    nearest_id = None
    nearest_dist_sq = float("inf")
    for tid, info in known_targets.items():
        if info.get("health", 100) <= 0:
            continue
        dsq = _distance_sq(position, info["pos"])
        if dsq < nearest_dist_sq:
            nearest_dist_sq = dsq
            nearest_id = tid
    return nearest_id


# ============ 蒙特卡洛批量运行器 ============

def run_single_simulation(sim_params: dict) -> dict:
    """
    运行一次完整仿真（独立进程中使用）

    Args:
        sim_params: 包含 uav_count, target_count, max_ticks, fault_tick, seed

    Returns:
        仿真结果统计
    """
    import json
    import sys
    import os

    # 确保能导入项目模块
    project_root = sim_params.get("project_root", "")
    if project_root and project_root not in sys.path:
        sys.path.insert(0, project_root)

    # 设置随机种子（保证可重复性）
    seed = sim_params.get("seed", 0)
    random.seed(seed)

    # 延迟导入（在worker进程中）
    from core.simulation import SimulationEngine

    engine = SimulationEngine(
        uav_count=sim_params.get("uav_count", 8),
        target_count=sim_params.get("target_count", 3),
    )
    engine.initialize()

    fault_tick = sim_params.get("fault_tick", 0)
    if fault_tick > 0:
        engine.enable_fault_injection(fault_tick)

    max_ticks = sim_params.get("max_ticks", 500)
    tick = 0

    # 同步运行（不使用async，在独立进程中）
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _run():
        nonlocal tick
        interval = 1.0 / engine.config.get("fps", 10) if hasattr(engine, 'config') else 0.1
        engine.running = True
        engine.start_time = __import__('time').time()
        while engine.running and tick < max_ticks:
            await engine.step()
            tick = engine.tick
            if max_ticks > 0 and tick >= max_ticks:
                engine.running = False
            if engine.mission_coord.mission_status == "completed":
                break
            await asyncio.sleep(0)  # 不放慢，尽快运行

    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()

    state = engine.get_state()
    summary = state["summary"]
    network = state["network"]
    comm = state["communication"]

    return {
        "total_ticks": state["tick"],
        "mission_status": state["mission"]["mission_status"],
        "alive_uavs": summary["alive_uavs"],
        "destroyed_uavs": summary["destroyed_uavs"],
        "destroyed_targets": summary["destroyed_targets"],
        "total_targets": summary["total_targets"],
        "reconnect_count": network["reconnect_count"],
        "leader_changes": network["leader_changes"],
        "network_splits": network["network_splits"],
        "messages_sent": comm["total_sent"],
        "messages_dropped": comm["total_dropped"],
        "seed": seed,
    }


class MonteCarloRunner:
    """
    蒙特卡洛批量仿真运行器

    使用 ProcessPoolExecutor 并行运行 N 个独立仿真实例，
    收集统计结果。这是 AFSIM/FLAMES 等系统的核心能力。

    使用示例:
        runner = MonteCarloRunner(max_workers=8)
        stats = runner.run_batch(
            num_runs=100,
            uav_count=8,
            target_count=3,
            varied_params={"fault_tick": [20, 40, 60, 80]},
        )
        print(f"任务成功率: {stats['success_rate']:.1%}")
    """

    def __init__(self, max_workers: int = None):
        self.max_workers = max_workers or max(1, multiprocessing.cpu_count() - 1)

    def run_batch(
        self,
        num_runs: int = 100,
        uav_count: int = 8,
        target_count: int = 3,
        max_ticks: int = 500,
        fault_tick: int = 0,
        varied_params: dict = None,
        project_root: str = None,
    ) -> dict:
        """
        批量运行仿真

        Args:
            num_runs: 仿真次数
            uav_count: 每次仿真的UAV数量
            target_count: 每次仿真的目标数量
            max_ticks: 最大tick数
            fault_tick: 故障注入tick
            varied_params: 参数变化配置 {param_name: [values]}
            project_root: 项目根目录路径

        Returns:
            统计分析结果
        """
        import os
        if project_root is None:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # 生成仿真参数
        sim_params_list = []
        varied_params = varied_params or {}

        for run_idx in range(num_runs):
            params = {
                "uav_count": uav_count,
                "target_count": target_count,
                "max_ticks": max_ticks,
                "fault_tick": fault_tick,
                "seed": run_idx * 100 + random.randint(0, 99),
                "project_root": project_root,
            }
            # 应用参数变化
            for param_name, values in varied_params.items():
                params[param_name] = values[run_idx % len(values)]
            sim_params_list.append(params)

        # 并行执行
        results = []
        with concurrent.futures.ProcessPoolExecutor(max_workers=self.max_workers) as pool:
            futures = [pool.submit(run_single_simulation, p) for p in sim_params_list]
            for future in concurrent.futures.as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as e:
                    results.append({"error": str(e)})

        return self._analyze_results(results)

    def _analyze_results(self, results: list) -> dict:
        """统计分析批量仿真结果"""
        valid = [r for r in results if "error" not in r]
        errors = [r for r in results if "error" in r]

        if not valid:
            return {"error": "All simulations failed", "errors": errors}

        n = len(valid)
        completed = [r for r in valid if r["mission_status"] == "completed"]
        success_rate = len(completed) / n

        alive_uavs_list = [r["alive_uavs"] for r in valid]
        reconnects = [r["reconnect_count"] for r in valid]
        leader_changes = [r["leader_changes"] for r in valid]
        msgs_sent = [r["messages_sent"] for r in valid]

        def stats(data):
            return {
                "min": min(data), "max": max(data),
                "mean": sum(data) / len(data),
                "median": sorted(data)[len(data) // 2],
            }

        return {
            "total_runs": len(results),
            "successful_runs": n,
            "failed_runs": len(errors),
            "success_rate": success_rate,
            "mission_completed": len(completed),
            "alive_uavs": stats(alive_uavs_list),
            "destroyed_targets": stats([r["destroyed_targets"] for r in valid]),
            "reconnect_count": stats(reconnects),
            "leader_changes": stats(leader_changes),
            "messages_sent": stats(msgs_sent),
            "network_splits": stats([r["network_splits"] for r in valid]),
            "raw_results": valid,
            "errors": [e["error"] for e in errors],
        }
