"""
合同网协议 (Contract Net Protocol) 任务分配系统
==============================================

将传统的规则引擎任务分配升级为基于合同网协议的多Agent协商机制。

协议流程:
┌─────────────────────────────────────────────────────────┐
│  Phase 1: 任务公告 (Task Announcement)                   │
│  Coordinator ──广播──► 所有UAV: "任务T1: 攻击(5000,3000)" │
│                                                         │
│  Phase 2: 投标 (Bidding)                                 │
│  各UAV评估自身能力 ──提交Bid──► Coordinator                │
│  Bid = {uav_id, estimated_time, success_prob, cost}      │
│                                                         │
│  Phase 3: 仲裁 (Awarding)                                │
│  Coordinator 综合评估 ──选择最优UAV──► 授予合同            │
│                                                         │
│  Phase 4: 执行监控 (Monitoring)                          │
│  Coordinator 跟踪任务进度，超时则重新分配                  │
└─────────────────────────────────────────────────────────┘

参考:
- Smith, R.G. (1980). "The Contract Net Protocol"
- 本项目 [[06_多智能体研究文档]] 第6.2节
"""
import asyncio
import math
import random
from dataclasses import dataclass, field
from typing import Optional
from collections import defaultdict

from config import (
    UAV_MAX_SPEED, UAV_WEAPON_RANGE, UAV_DETECTION_RANGE,
    distance, distance_sq,
)
from core.communication import AsyncMessageBroker, MessagePriority, MessageType
from core.uav_agent import UAVAgent, UAVRole, UAVState, Task


# ============ 合同网数据结构 ============

@dataclass
class TaskAnnouncement:
    """任务公告"""
    task_id: str
    task_type: str          # scout / strike / relay / defend
    target_id: Optional[str] = None
    target_pos: Optional[dict] = None
    priority: int = 1       # 1=高, 2=中, 3=低
    deadline_tick: int = 0   # 投标截止tick
    created_tick: int = 0
    status: str = "open"    # open / awarded / expired

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "target_id": self.target_id,
            "target_pos": self.target_pos,
            "priority": self.priority,
            "deadline_tick": self.deadline_tick,
            "created_tick": self.created_tick,
            "status": self.status,
        }


@dataclass
class Bid:
    """投标"""
    uav_id: str
    task_id: str
    estimated_time: float   # 预计完成时间 (ticks)
    success_probability: float  # 预估成功率 (0-1)
    cost: float             # 执行成本 (0-1, 越低越好)
    current_task_priority: int  # 当前任务优先级 (用于比较)
    reasoning: str = ""     # 投标理由 (LLM生成)

    @property
    def score(self) -> float:
        """综合评分的快捷方式"""
        return 0.0  # 由仲裁器计算


@dataclass
class Contract:
    """合同（任务授予）"""
    task_id: str
    contractor_id: str      # 中标UAV
    bids_received: list = field(default_factory=list)
    awarded_tick: int = 0
    status: str = "active"  # active / completed / failed / timeout


# ============ UAV能力评估 ============

class UAVCapabilityEvaluator:
    """
    UAV 能力评估器

    评估 UAV 对特定任务的适合度。
    支持纯规则评估和 LLM 增强评估两种模式。
    """

    def __init__(self, use_llm: bool = False, llm_client=None):
        self.use_llm = use_llm
        self.llm_client = llm_client

    def evaluate(
        self, uav: UAVAgent, announcement: TaskAnnouncement, tick: int
    ) -> Bid:
        """评估 UAV 对任务的适合度"""
        if not uav.alive or uav.state == UAVState.DESTROYED:
            return Bid(
                uav_id=uav.id, task_id=announcement.task_id,
                estimated_time=9999, success_probability=0.0, cost=1.0,
                current_task_priority=99,
                reasoning="UAV不可用",
            )

        # === 规则引擎评估 ===
        task_type = announcement.task_type

        # 1. 计算到达时间
        if announcement.target_pos:
            dist = distance(uav.position, announcement.target_pos)
            eta = dist / (uav.max_speed * 0.7)  # 70%最大速度估算
        else:
            eta = 0  # 无位置要求的任务

        # 2. 角色匹配度
        role_fitness = {
            "scout":  {"scout": 1.0, "strike": 0.2, "relay": 0.3, "defend": 0.1},
            "strike": {"scout": 0.1, "strike": 1.0, "relay": 0.1, "defend": 0.5},
            "relay":  {"scout": 0.2, "strike": 0.1, "relay": 1.0, "defend": 0.2},
            "leader": {"scout": 0.5, "strike": 0.3, "relay": 0.6, "defend": 0.4},
        }
        role_match = role_fitness.get(uav.role.value, {}).get(task_type, 0.3)

        # 3. 成功概率估算
        base_success = role_match * 0.7  # 基础成功率

        # 血量修正
        health_mod = max(0.1, uav.health / 100)
        if uav.health < 30:
            base_success *= 0.5

        # 电量修正
        battery_mod = max(0.1, uav.battery / 100)

        # 距离修正（越远成功率越低）
        if announcement.target_pos:
            dist_mod = max(0.1, 1.0 - eta / 200)  # 200 tick以上成功率极低
        else:
            dist_mod = 1.0

        success_prob = base_success * health_mod * battery_mod * dist_mod

        # 4. 执行成本
        # 包括：时间成本 + 机会成本（放弃当前任务）
        time_cost = min(1.0, eta / 100)
        opportunity_cost = 0.0 if uav.current_task is None else 0.5
        cost = 0.3 * time_cost + 0.7 * opportunity_cost

        # 5. 构建投标
        reasoning_parts = []
        if role_match >= 0.8:
            reasoning_parts.append(f"角色高度匹配({role_match:.0%})")
        if eta < 50:
            reasoning_parts.append(f"距离近(ETA={eta:.0f}tick)")
        if uav.current_task is None:
            reasoning_parts.append("当前空闲")
        else:
            reasoning_parts.append(f"需放弃当前任务")

        return Bid(
            uav_id=uav.id,
            task_id=announcement.task_id,
            estimated_time=round(eta, 1),
            success_probability=round(success_prob, 3),
            cost=round(cost, 3),
            current_task_priority=(
                uav.current_task.priority if uav.current_task else 99
            ),
            reasoning="; ".join(reasoning_parts),
        )

    async def evaluate_llm(
        self, uav: UAVAgent, announcement: TaskAnnouncement, tick: int
    ) -> Bid:
        """LLM增强评估（更智能的投标决策）"""
        if not self.use_llm or not self.llm_client:
            return self.evaluate(uav, announcement, tick)

        # 规则引擎作为baseline
        rule_bid = self.evaluate(uav, announcement, tick)

        # LLM二次评估
        try:
            prompt = self._build_bid_prompt(uav, announcement, rule_bid, tick)
            response = await self.llm_client.generate(
                system_prompt="You evaluate UAV task fitness. Output JSON with adjusted success_probability and reasoning.",
                user_prompt=prompt,
                output_schema={
                    "type": "object",
                    "properties": {
                        "adjusted_success_prob": {"type": "number", "minimum": 0, "maximum": 1},
                        "adjusted_cost": {"type": "number", "minimum": 0, "maximum": 1},
                        "strategic_value": {"type": "string", "enum": ["high", "medium", "low"]},
                        "reasoning": {"type": "string"},
                    },
                    "required": ["adjusted_success_prob", "adjusted_cost", "reasoning"],
                },
                temperature=0.3,
            )

            if response.structured:
                rule_bid.success_probability = response.structured.get(
                    "adjusted_success_prob", rule_bid.success_probability
                )
                rule_bid.cost = response.structured.get(
                    "adjusted_cost", rule_bid.cost
                )
                rule_bid.reasoning += " [LLM: " + response.structured.get("reasoning", "") + "]"
        except Exception:
            pass  # LLM失败时使用规则引擎结果

        return rule_bid

    def _build_bid_prompt(
        self, uav: UAVAgent, announcement: TaskAnnouncement,
        rule_bid: Bid, tick: int
    ) -> str:
        """构建LLM投标评估Prompt"""
        return f"""Evaluate this UAV's fitness for the task:

UAV: {uav.id} (Role: {uav.role.value})
Health: {uav.health:.0f}% | Battery: {uav.battery:.0f}%
Current State: {uav.state.name}
Current Task: {uav.current_task.task_id if uav.current_task else 'none'}
Position: ({uav.position['x']:.0f}, {uav.position['y']:.0f})

Task: {announcement.task_id} (Type: {announcement.task_type})
Target: {announcement.target_id or 'none'} at ({announcement.target_pos['x']:.0f}, {announcement.target_pos['y']:.0f})
Priority: {announcement.priority}

Rule engine baseline:
- ETA: {rule_bid.estimated_time:.0f} ticks
- Success prob: {rule_bid.success_probability:.0%}
- Cost: {rule_bid.cost:.2f}

Adjust the success probability and cost based on tactical judgment.
Consider: swarm composition, strategic value, coordination opportunities."""


# ============ 合同网仲裁器 ============

class ContractNetArbitrator:
    """
    合同网仲裁器

    综合评估所有投标，选择最优UAV授予合同。
    支持纯规则仲裁和 LLM 增强仲裁。
    """

    def __init__(self, use_llm: bool = False, llm_client=None):
        self.use_llm = use_llm
        self.llm_client = llm_client

        # 仲裁历史
        self.contracts: dict[str, Contract] = {}
        self.award_history: list = []

    def arbitrate(self, announcement: TaskAnnouncement, bids: list[Bid]) -> Optional[str]:
        """
        规则仲裁：选择最优投标

        评分权重:
        - 成功率: 40%
        - 时间效率: 25%
        - 成本: 20%
        - 任务优先级: 15%
        """
        if not bids:
            return None

        # 过滤明显不合格的投标
        viable_bids = [b for b in bids if b.success_probability > 0.2 and b.estimated_time < 500]
        if not viable_bids:
            viable_bids = bids

        def score(bid: Bid) -> float:
            time_score = max(0, 1.0 - bid.estimated_time / 200)  # ETA越短越好
            priority_score = max(0, 1.0 - bid.current_task_priority / 5)  # 当前任务优先级越低越好
            return (
                0.40 * bid.success_probability +
                0.25 * time_score +
                0.20 * (1.0 - bid.cost) +
                0.15 * priority_score
            )

        best_bid = max(viable_bids, key=score)

        # 记录合同
        contract = Contract(
            task_id=announcement.task_id,
            contractor_id=best_bid.uav_id,
            bids_received=bids,
            awarded_tick=announcement.created_tick,
        )
        self.contracts[announcement.task_id] = contract
        self.award_history.append({
            "task_id": announcement.task_id,
            "winner": best_bid.uav_id,
            "num_bids": len(bids),
            "best_score": round(score(best_bid), 3),
        })

        return best_bid.uav_id

    async def arbitrate_llm(
        self, announcement: TaskAnnouncement, bids: list[Bid]
    ) -> Optional[str]:
        """LLM增强仲裁（全局最优视角）"""
        if not self.use_llm or not self.llm_client or len(bids) <= 1:
            return self.arbitrate(announcement, bids)

        # 先让规则引擎筛选 top-3
        rule_winner = self.arbitrate(announcement, bids)
        if len(bids) <= 3:
            return rule_winner

        # LLM从全局视角评估 top-3
        def simple_score(b: Bid) -> float:
            return b.success_probability * 0.6 + (1 - b.cost) * 0.4
        top_bids = sorted(bids, key=simple_score, reverse=True)[:3]

        try:
            prompt = self._build_arbitration_prompt(announcement, top_bids)
            response = await self.llm_client.generate(
                system_prompt="You are a swarm mission coordinator. Select the best UAV for the task considering global swarm efficiency.",
                user_prompt=prompt,
                output_schema={
                    "type": "object",
                    "properties": {
                        "selected_uav": {"type": "string"},
                        "reasoning": {"type": "string"},
                    },
                    "required": ["selected_uav", "reasoning"],
                },
                temperature=0.2,
            )

            if response.structured:
                selected = response.structured.get("selected_uav", "")
                if selected and any(b.uav_id == selected for b in bids):
                    contract = Contract(
                        task_id=announcement.task_id,
                        contractor_id=selected,
                        bids_received=bids,
                        awarded_tick=announcement.created_tick,
                    )
                    self.contracts[announcement.task_id] = contract
                    return selected
        except Exception:
            pass

        return rule_winner

    def _build_arbitration_prompt(
        self, announcement: TaskAnnouncement, top_bids: list[Bid]
    ) -> str:
        """构建LLM仲裁Prompt"""
        bid_table = "\n".join(
            f"- {b.uav_id}: ETA={b.estimated_time:.0f}t, "
            f"Success={b.success_probability:.0%}, Cost={b.cost:.2f}, "
            f"Reasoning: {b.reasoning}"
            for b in top_bids
        )
        return f"""Select the best UAV for this task:

Task: {announcement.task_id} (Type: {announcement.task_type}, Priority: {announcement.priority})
Target: {announcement.target_id or 'none'} at {announcement.target_pos}

Top Candidates:
{bid_table}

Consider global swarm efficiency, not just individual UAV metrics.
Output the selected_uav and reasoning."""

    def get_stats(self) -> dict:
        return {
            "total_contracts": len(self.contracts),
            "active_contracts": sum(1 for c in self.contracts.values() if c.status == "active"),
            "completed_contracts": sum(1 for c in self.contracts.values() if c.status == "completed"),
            "recent_awards": self.award_history[-10:],
        }


# ============ 合同网任务协调器 ============

class AgenticMissionCoordinator:
    """
    基于合同网协议的任务协调器

    替代原有的 MissionCoordinator，引入：
    1. 合同网协议：UAV自主投标而不是被动分配
    2. 多维度评估：不只是角色+距离
    3. LLM增强：全局最优仲裁
    4. 动态监控：超时重分配
    """

    def __init__(self, broker: AsyncMessageBroker, use_llm: bool = False, llm_client=None):
        self.broker = broker
        self.use_llm = use_llm
        self.llm_client = llm_client

        self.evaluator = UAVCapabilityEvaluator(use_llm=use_llm, llm_client=llm_client)
        self.arbitrator = ContractNetArbitrator(use_llm=use_llm, llm_client=llm_client)

        self.tasks: dict[str, Task] = {}
        self.announcements: dict[str, TaskAnnouncement] = {}
        self.uav_task_map: dict[str, str] = {}  # uav_id -> task_id
        self.targets: dict = {}

        # 监控
        self._task_timeouts: dict[str, int] = {}  # task_id -> ticks since awarded
        self._task_max_timeout = 200  # 超时tick数

    async def create_task(
        self, task_type: str, target_id: str = None,
        target_pos: dict = None, priority: int = 1, tick: int = 0,
        uavs: dict[str, UAVAgent] = None,
    ) -> str:
        """创建任务并启动合同网流程"""
        task_id = f"CN_{task_type}_{tick}_{random.randint(100, 999)}"

        task = Task(
            task_id=task_id,
            task_type=task_type,
            target_id=target_id,
            target_pos=target_pos,
            priority=priority,
            created_tick=tick,
            status="pending",
        )
        self.tasks[task_id] = task

        # 创建公告
        announcement = TaskAnnouncement(
            task_id=task_id,
            task_type=task_type,
            target_id=target_id,
            target_pos=target_pos,
            priority=priority,
            deadline_tick=tick + 20,  # 20 tick投标窗口
            created_tick=tick,
        )
        self.announcements[task_id] = announcement

        # 收集投标
        if uavs:
            winner_id = await self._run_contract_net(announcement, uavs, tick)
            if winner_id:
                task.assigned_to = winner_id
                task.status = "active"
                self.uav_task_map[winner_id] = task_id
                self._task_timeouts[task_id] = 0

                # 通知中标UAV
                await self.broker.publish(
                    MessageType.TASK_ASSIGN, "cn_coordinator",
                    {
                        "task_id": task_id,
                        "task_type": task_type,
                        "target_id": target_id,
                        "target_pos": target_pos,
                        "priority": priority,
                        "method": "contract_net",
                    },
                    target=winner_id,
                    priority=MessagePriority.HIGH,
                    tick=tick,
                )

        return task_id

    async def _run_contract_net(
        self, announcement: TaskAnnouncement,
        uavs: dict[str, UAVAgent], tick: int
    ) -> Optional[str]:
        """执行一轮合同网流程"""
        # Phase 1: 收集投标
        bids = []
        for uid, uav in uavs.items():
            if not uav.alive or uav.state == UAVState.DESTROYED:
                continue
            if uid in self.uav_task_map:
                continue  # 已有任务

            if self.use_llm:
                bid = await self.evaluator.evaluate_llm(uav, announcement, tick)
            else:
                bid = self.evaluator.evaluate(uav, announcement, tick)
            bids.append(bid)

        # Phase 2: 仲裁
        if self.use_llm:
            winner = await self.arbitrator.arbitrate_llm(announcement, bids)
        else:
            winner = self.arbitrator.arbitrate(announcement, bids)

        return winner

    async def monitor_tasks(self, tick: int, uavs: dict[str, UAVAgent]):
        """监控任务执行，超时重新分配"""
        for task_id, task in list(self.tasks.items()):
            if task.status != "active":
                continue

            self._task_timeouts[task_id] = self._task_timeouts.get(task_id, 0) + 1

            # 检查超时
            if self._task_timeouts[task_id] > self._task_max_timeout:
                # 超时！重新分配
                task.status = "pending"
                task.assigned_to = None
                if task_id in self.uav_task_map:
                    old_uav = self.uav_task_map.pop(task_id)

                announcement = self.announcements.get(task_id)
                if announcement:
                    announcement.status = "open"
                    announcement.deadline_tick = tick + 20

                winner = await self._run_contract_net(announcement, uavs, tick) if announcement else None
                if winner:
                    task.assigned_to = winner
                    task.status = "active"
                    self.uav_task_map[winner] = task_id
                    self._task_timeouts[task_id] = 0

    def get_stats(self) -> dict:
        base = self.arbitrator.get_stats()
        base["pending_tasks"] = sum(1 for t in self.tasks.values() if t.status == "pending")
        base["active_tasks"] = sum(1 for t in self.tasks.values() if t.status == "active")
        base["completed_tasks"] = sum(1 for t in self.tasks.values() if t.status == "completed")
        base["using_llm"] = self.use_llm
        return base
