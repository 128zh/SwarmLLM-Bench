"""
混合UAV智能体 —— FSM + LLM 混合决策架构
=========================================

将 DeepAgent (LLM战术推理) 与 UAVAgent (FSM实时执行) 融合。

架构原理：
┌─────────────────────────────────────────┐
│            HybridUAVAgent               │
│                                         │
│  ┌───────────────┐  ┌────────────────┐  │
│  │   DeepAgent   │  │  UAVAgent FSM  │  │
│  │  (LLM推理层)   │  │  (安全执行层)   │  │
│  │               │  │                │  │
│  │ • 异步战术推理  │  │ • 确定性运动    │  │
│  │ • 态势评估     │──│ • 100ms周期    │  │
│  │ • 目标优先级   │  │ • 安全拦截     │  │
│  │ • 协同决策     │  │ • 心跳/通信    │  │
│  └───────┬───────┘  └───────┬────────┘  │
│          │                  │           │
│          └──── 融合层 ──────┘           │
│          LLM决策 → FSM参数更新          │
│          FSM状态 → LLM态势输入          │
└─────────────────────────────────────────┘

关键特性：
1. LLM异步调用——不阻塞仿真tick
2. FSM安全网——LLM超时/异常时自动回退
3. 冷却机制——控制LLM调用频率
4. 白名单校验——阻止危险的LLM决策
5. 向后兼容——LLM_ENABLED=False时完全等同于原UAVAgent

使用示例：
    from core.hybrid_uav import HybridUAVAgent
    from core.llm_client import get_llm_client

    client = get_llm_client()
    uav = HybridUAVAgent(
        uav_id="UAV_01",
        role=UAVRole.LEADER,
        position={"x": 1000, "y": 5000, "z": 200},
        broker=broker,
        llm_client=client,
    )
    # 使用方式与普通UAVAgent完全一致
    await uav.step(tick, env_targets)
    # LLM推理在后台异步执行，结果在后续tick中生效
"""
import math
import asyncio
from typing import Optional

from config import LLM_ENABLED
from core.uav_agent import UAVAgent, UAVRole, UAVState
from core.communication import AsyncMessageBroker
from core.llm_client import LLMClient
from core.llm_agent import DeepAgent, TacticalDecision, DecisionAction


class HybridUAVAgent(UAVAgent):
    """
    FSM + LLM 混合智能体

    继承原有 UAVAgent 的全部功能，增加 LLM 战术推理能力。
    当 LLM_ENABLED=False 时，完全等同于普通 UAVAgent。
    """

    def __init__(
        self,
        uav_id: str,
        role: UAVRole,
        position: dict,
        broker: AsyncMessageBroker,
        llm_client: Optional[LLMClient] = None,
        config: dict = None,
        llm_enabled: bool = LLM_ENABLED,
    ):
        super().__init__(uav_id, role, position, broker, config)

        # LLM 增强
        self.llm_enabled = llm_enabled
        self.llm_client = llm_client

        if self.llm_enabled and self.llm_client:
            self.deep_agent = DeepAgent(
                agent_id=uav_id,
                role=role.value,
                llm_client=self.llm_client,
                prompt_category=f"{role.value}_" + {
                    "leader": "command",
                    "scout": "search",
                    "striker": "engage",
                    "relay": "position",
                }.get(role.value, "tactical"),
            )
        else:
            self.deep_agent = None

        # 融合层状态
        self._pending_llm_task: Optional[asyncio.Task] = None
        self._llm_decision_ready = False
        self._last_llm_tick = -999
        self._llm_stats = {
            "total_calls": 0,
            "decisions_applied": 0,
            "decisions_rejected": 0,
            "fallback_to_fsm": 0,
        }

    # ============ 核心 step 方法（重写） ============

    async def step(self, tick: int, env_targets: dict, interceptor_swarm=None):
        """
        混合决策的 step 方法

        执行流程：
        1. FSM 正常执行（每 tick）
        2. 如果满足 LLM 调用条件 → 异步触发 LLM 推理
        3. 如果 LLM 结果已就绪 → 应用决策更新 FSM 参数
        """
        # === 1. 应用已就绪的 LLM 决策 ===
        if self._llm_decision_ready and self.deep_agent:
            await self._apply_llm_decision(tick)

        # === 2. FSM 执行（安全网：始终执行） ===
        if self.state != UAVState.MANUAL:
            await super().step(tick, env_targets, interceptor_swarm)
        else:
            # 手动模式：只更新位置，不做AI决策
            self.position["x"] += self.velocity["x"]
            self.position["y"] += self.velocity["y"]
            self.position["z"] = self.position.get("z", 200) + self.velocity.get("z", 0)

        # === 3. 触发 LLM 推理（异步，不阻塞） ===
        if self._should_trigger_llm(tick):
            self._last_llm_tick = tick
            self._pending_llm_task = asyncio.ensure_future(
                self._async_llm_reason(tick, env_targets)
            )

    def _should_trigger_llm(self, tick: int) -> bool:
        """判断是否应触发 LLM 推理"""
        if not self.llm_enabled or not self.deep_agent:
            return False
        if self._pending_llm_task and not self._pending_llm_task.done():
            return False  # 上一次 LLM 调用尚未完成
        if not self.alive or self.state == UAVState.DESTROYED:
            return False
        return self.deep_agent.should_reason(tick)

    # ============ 异步 LLM 推理 ============

    async def _async_llm_reason(self, tick: int, env_targets: dict):
        """异步执行 LLM 推理（后台任务）"""
        try:
            # 构建状态快照
            snapshot = self._build_state_snapshot(env_targets)

            # 调用 DeepAgent 推理
            decision = await self.deep_agent.reason(snapshot, tick)
            self._llm_stats["total_calls"] += 1

            if decision:
                self._llm_decision_ready = True
                # 存储待应用的决策
                self._pending_decision = decision
            else:
                self._llm_stats["fallback_to_fsm"] += 1
        except Exception as e:
            self._llm_stats["fallback_to_fsm"] += 1
            # LLM 异常，静默回退到 FSM
            pass

    # ============ 应用 LLM 决策 ============

    async def _apply_llm_decision(self, tick: int):
        """将 LLM 决策应用到 FSM 参数"""
        if not hasattr(self, '_pending_decision'):
            self._llm_decision_ready = False
            return

        decision = self._pending_decision
        self._llm_decision_ready = False

        if not decision or not decision.is_valid():
            self._llm_stats["decisions_rejected"] += 1
            return

        try:
            applied = False

            if decision.action == DecisionAction.ENGAGE:
                if decision.target_id:
                    self.target_pos = decision.target_pos or self.target_pos
                    self.state = UAVState.ENGAGING
                    applied = True

            elif decision.action == DecisionAction.CHANGE_TARGET:
                if decision.target_pos:
                    self.target_pos = decision.target_pos
                    if self.state in (UAVState.MOVING, UAVState.IDLE, UAVState.SCOUTING):
                        self.state = UAVState.MOVING
                    applied = True

            elif decision.action == DecisionAction.RETREAT:
                # 撤退到地图原点方向
                self.target_pos = {"x": 0, "y": 0, "z": self.position.get("z", 200)}
                self.state = UAVState.RETREATING
                applied = True

            elif decision.action == DecisionAction.HOLD:
                self.target_pos = dict(self.position)
                if self.state not in (UAVState.DAMAGED, UAVState.DESTROYED, UAVState.MANUAL):
                    self.state = UAVState.IDLE
                applied = True

            elif decision.action == DecisionAction.SCOUT:
                if decision.target_pos:
                    self.target_pos = decision.target_pos
                self.state = UAVState.SCOUTING
                applied = True

            elif decision.action == DecisionAction.SUPPORT:
                if decision.support_uav_id and decision.target_pos:
                    self.target_pos = decision.target_pos
                    self.state = UAVState.MOVING
                    applied = True

            elif decision.action == DecisionAction.RELAY:
                self.state = UAVState.RELAYING
                applied = True

            elif decision.action == DecisionAction.RTB:
                self.target_pos = {"x": 0, "y": 0, "z": 200}
                self.state = UAVState.RETREATING
                applied = True

            if applied:
                self._llm_stats["decisions_applied"] += 1
            else:
                self._llm_stats["decisions_rejected"] += 1

        except Exception:
            self._llm_stats["decisions_rejected"] += 1

    # ============ 状态快照构建 ============

    def _build_state_snapshot(self, env_targets: dict) -> dict:
        """构建供 LLM 推理的完整状态快照"""
        # 获取基本状态
        status = self.get_status()

        # 添加环境感知信息
        status["nearby_targets"] = []
        status["nearby_uavs"] = []

        for tid, t in env_targets.items():
            if t.get("destroyed"):
                continue
            dist = self._distance_to(t["position"])
            if dist <= self.detection_range:
                status["nearby_targets"].append({
                    "id": tid,
                    "distance": round(dist, 1),
                    "health": t.get("health", 100),
                    "type": t.get("type", "unknown"),
                })

        # 按距离排序
        status["nearby_targets"].sort(key=lambda x: x["distance"])

        return status

    def _distance_to(self, pos: dict) -> float:
        """计算到指定位置的距离"""
        return math.sqrt(
            (self.position["x"] - pos["x"]) ** 2 +
            (self.position["y"] - pos["y"]) ** 2 +
            (self.position.get("z", 200) - pos.get("z", 0)) ** 2
        )

    # ============ 统计 ============

    def get_stats(self) -> dict:
        """获取混合Agent统计（扩展父类）"""
        base = super().get_stats() if hasattr(super(), 'get_stats') else {}
        base["hybrid"] = {
            "llm_enabled": self.llm_enabled,
            "llm_stats": self._llm_stats,
            "deep_agent_stats": self.deep_agent.get_stats() if self.deep_agent else {},
        }
        return base


# ============ 工厂函数 ============

def create_hybrid_uav(
    uav_id: str,
    role: str,
    position: dict,
    broker: AsyncMessageBroker,
    llm_client: Optional[LLMClient] = None,
    llm_enabled: bool = LLM_ENABLED,
) -> UAVAgent:
    """
    创建 UAV Agent 的工厂函数

    根据 LLM_ENABLED 自动选择：
    - True:  返回 HybridUAVAgent (FSM + LLM)
    - False: 返回 UAVAgent (纯FSM，向后兼容)

    Usage:
        from core.hybrid_uav import create_hybrid_uav
        from core.llm_client import get_llm_client

        client = get_llm_client()
        uav = create_hybrid_uav(
            "UAV_01", "leader", {"x": 1000, "y": 5000, "z": 200},
            broker, llm_client=client, llm_enabled=True,
        )
    """
    from core.uav_agent import UAVRole as _UAVRole

    role_enum = _UAVRole(role) if isinstance(role, str) else role

    if llm_enabled and llm_client:
        return HybridUAVAgent(
            uav_id=uav_id,
            role=role_enum,
            position=position,
            broker=broker,
            llm_client=llm_client,
            llm_enabled=True,
        )
    else:
        return UAVAgent(
            uav_id=uav_id,
            role=role_enum,
            position=position,
            broker=broker,
        )
