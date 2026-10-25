"""
任务协调器模块
==============
负责蜂群作战任务的分解、分配与动态重分配：
- 任务分解：将总体任务分解为子任务（侦察、攻击、中继等）
- 任务分配：根据UAV角色能力和位置分配任务
- 动态重分配：UAV故障时自动接管其未完成任务
- 任务状态跟踪：实时跟踪任务进度
- 协同打击：多机协同攻击同一目标
"""
import asyncio
import uuid
from collections import defaultdict
from typing import Optional

from config import MISSION_TARGET_COUNT, MISSION_TARGET_HEALTH, distance
from core.communication import AsyncMessageBroker, MessagePriority, MessageType
from core.uav_agent import UAVAgent, UAVRole, UAVState, Task


class MissionCoordinator:
    """
    任务协调器
    
    任务分配策略：
    1. 侦察任务 → 分配给scout角色（优先距离近的）
    2. 攻击任务 → 分配给striker角色（优先距离近、血量高的）
    3. 中继任务 → 分配给relay角色
    4. 指挥任务 → 由leader负责
    
    动态重分配策略：
    1. UAV故障时，收集其所有未完成任务
    2. 按任务优先级排序
    3. 在剩余存活UAV中找到最合适的接管者
    4. 如果没有同角色UAV，允许跨角色接管（降级执行）
    """

    def __init__(self, broker: AsyncMessageBroker):
        self.broker = broker
        self.tasks: dict[str, Task] = {}           # task_id -> Task
        self.uav_task_map: dict[str, str] = {}     # uav_id -> task_id
        self.targets: dict = {}                     # target_id -> target_info
        self.mission_status = "pending"             # pending / active / completed / failed
        self._task_counter = 0
        
        # 订阅交战结果（跟踪目标血量）
        self.broker.subscribe(MessageType.ENGAGE_RESULT, "mission_coord", self._on_engage_result)
        self.broker.subscribe(MessageType.TARGET_INFO, "mission_coord", self._on_target_info)

    async def create_mission(self, targets: list, uavs: dict[str, UAVAgent], tick: int = 0):
        """
        创建作战任务并分配
        
        Args:
            targets: 目标列表 [{id, position, type, health}]
            uavs: 所有可用UAV
        """
        self.mission_status = "active"
        self.targets = {t["id"]: t for t in targets}

        # 为每个目标创建侦察和攻击子任务
        for target in targets:
            # 侦察任务
            scout_task = Task(
                task_id=f"scout_{target['id']}",
                task_type="scout",
                target_id=target["id"],
                target_pos=dict(target["position"]),
                priority=1,
                created_tick=tick,
            )
            self.tasks[scout_task.task_id] = scout_task

            # 攻击任务
            strike_task = Task(
                task_id=f"strike_{target['id']}",
                task_type="strike",
                target_id=target["id"],
                target_pos=dict(target["position"]),
                priority=1,
                created_tick=tick,
            )
            self.tasks[strike_task.task_id] = strike_task

        # 分配任务
        await self._assign_tasks(uavs, tick)

    async def _assign_tasks(self, uavs: dict[str, UAVAgent], tick: int):
        """
        分配任务给UAV — 含战术自适应

        策略：
        - 按角色匹配任务类型
        - 按距离最近优先分配
        - 高战损时自动切换保守策略（保留预备队、错峰攻击）
        - 一个UAV只分配一个主任务
        """
        # --- 战术态势评估 ---
        alive_count = sum(1 for u in uavs.values() if u.alive)
        total_count = len(uavs)
        loss_rate = 1.0 - (alive_count / max(total_count, 1))
        defensive_mode = loss_rate >= 0.5  # 损失过半→防御模式

        # 按角色分组存活UAV
        available_uavs = {
            uid: uav for uid, uav in uavs.items()
            if uav.alive and uav.state != UAVState.DESTROYED
            and uav.state != UAVState.EVADING   # 规避中的UAV不分配
            and uid not in self.uav_task_map
        }

        # 防御模式: 保留至少1架攻击机作为预备队
        reserve_count = 1 if defensive_mode else 0
        striker_uavs = [u for u in available_uavs.values() if u.role.value == "striker"]
        if reserve_count > 0 and len(striker_uavs) > reserve_count:
            # 保留血量最高的striker
            striker_uavs.sort(key=lambda u: -u.health)
            for u in striker_uavs[:reserve_count]:
                available_uavs.pop(u.id, None)

        # 按角色分类
        by_role: dict[str, list] = defaultdict(list)
        for uid, uav in available_uavs.items():
            by_role[uav.role.value].append(uav)

        # 待分配任务（按优先级排序）
        pending_tasks = sorted(
            [t for t in self.tasks.values() if t.status == "pending"],
            key=lambda t: (t.priority, t.created_tick),
        )

        for task in pending_tasks:
            assigned = await self._try_assign(task, by_role, tick)
            if not assigned:
                # 跨角色分配（降级执行）
                assigned = await self._try_assign_any(task, available_uavs, tick)

    async def _try_assign(self, task: Task, by_role: dict, tick: int) -> bool:
        """尝试按角色匹配分配任务"""
        role_map = {
            "scout": ["scout", "leader"],
            "strike": ["striker", "leader", "scout"],
            "relay": ["relay", "leader"],
            "defend": ["striker", "relay"],
        }
        preferred_roles = role_map.get(task.task_type, ["leader"])

        for role in preferred_roles:
            candidates = by_role.get(role, [])
            if not candidates:
                continue
            # 按距离排序
            if task.target_pos:
                candidates.sort(key=lambda u: distance(u.position, task.target_pos))
            else:
                candidates.sort(key=lambda u: -u.health)  # 没有位置要求时按血量排序

            for uav in candidates:
                if uav.id not in self.uav_task_map:
                    await self._assign_task_to_uav(task, uav, tick)
                    # 从候选列表中移除
                    candidates.remove(uav)
                    return True
        return False

    async def _try_assign_any(self, task: Task, available_uavs: dict, tick: int) -> bool:
        """跨角色分配（降级执行）"""
        candidates = list(available_uavs.values())
        if task.target_pos:
            candidates.sort(key=lambda u: distance(u.position, task.target_pos))
        else:
            candidates.sort(key=lambda u: -u.health)

        for uav in candidates:
            if uav.id not in self.uav_task_map:
                await self._assign_task_to_uav(task, uav, tick)
                return True
        return False

    async def _assign_task_to_uav(self, task: Task, uav: UAVAgent, tick: int):
        """将任务分配给UAV"""
        task.assigned_to = uav.id
        task.status = "active"
        self.uav_task_map[uav.id] = task.task_id

        await self.broker.publish(
            MessageType.TASK_ASSIGN, "mission_coord",
            {
                "task_id": task.task_id,
                "task_type": task.task_type,
                "target_id": task.target_id,
                "target_pos": task.target_pos,
                "priority": task.priority,
            },
            target=uav.id,
            priority=MessagePriority.HIGH,
            tick=tick,
        )

    async def handle_uav_failure(self, failed_id: str, uavs: dict[str, UAVAgent], tick: int):
        """
        处理UAV故障 - 任务重分配
        
        1. 收集故障UAV的未完成任务
        2. 标记任务为pending
        3. 在存活UAV中重新分配
        """
        failed_task_id = self.uav_task_map.pop(failed_id, None)
        reassigned = []

        if failed_task_id and failed_task_id in self.tasks:
            task = self.tasks[failed_task_id]
            if task.status == "active":
                task.status = "pending"
                task.assigned_to = None
                reassigned.append(task.task_id)

        # 如果有其他UAV空闲，重新分配
        if reassigned:
            await self._assign_tasks(uavs, tick)

        return reassigned

    async def _on_engage_result(self, msg_type: str, sender: str, payload: dict, tick: int):
        """处理交战结果 - 更新目标血量"""
        target_id = payload.get("target_id")
        damage = payload.get("damage", 0)
        if target_id and target_id in self.targets:
            target = self.targets[target_id]
            target["health"] = max(0, target.get("health", 100) - damage)
            if target["health"] <= 0:
                target["destroyed"] = True
                # 标记相关任务完成
                for task in self.tasks.values():
                    if task.target_id == target_id and task.status == "active":
                        task.status = "completed"
                        task.completed_tick = tick
                        if task.assigned_to:
                            self.uav_task_map.pop(task.assigned_to, None)

    async def _on_target_info(self, msg_type: str, sender: str, payload: dict, tick: int):
        """处理目标信息 - 更新目标位置"""
        target_id = payload.get("target_id")
        if target_id and target_id in self.targets:
            self.targets[target_id]["position"] = payload.get("pos", self.targets[target_id]["position"])
            self.targets[target_id]["health"] = payload.get("health", self.targets[target_id].get("health", 100))

    def update_task_status(self, tick: int):
        """更新任务状态"""
        all_completed = True
        any_active = False

        for task in self.tasks.values():
            if task.status == "completed":
                continue
            elif task.status == "active":
                any_active = True
                all_completed = False
            else:
                all_completed = False

        if all_completed and self.tasks:
            self.mission_status = "completed"
        elif any_active:
            self.mission_status = "active"

    def check_mission_complete(self) -> bool:
        """检查任务是否完成"""
        if not self.targets:
            return False
        return all(t.get("destroyed", False) for t in self.targets.values())

    def get_mission_status(self) -> dict:
        """获取任务状态摘要"""
        total = len(self.tasks)
        completed = sum(1 for t in self.tasks.values() if t.status == "completed")
        active = sum(1 for t in self.tasks.values() if t.status == "active")
        pending = sum(1 for t in self.tasks.values() if t.status == "pending")

        return {
            "mission_status": self.mission_status,
            "total_tasks": total,
            "completed_tasks": completed,
            "active_tasks": active,
            "pending_tasks": pending,
            "targets": {
                tid: {
                    "position": t.get("position"),
                    "health": round(t.get("health", 100), 1),
                    "destroyed": t.get("destroyed", False),
                    "type": t.get("type", "ground"),
                }
                for tid, t in self.targets.items()
            },
            "tasks": {
                tid: {
                    "type": t.task_type,
                    "target_id": t.target_id,
                    "assigned_to": t.assigned_to,
                    "status": t.status,
                    "priority": t.priority,
                }
                for tid, t in self.tasks.items()
            },
        }
