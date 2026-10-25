"""
DeepAgent 基类 —— LLM 驱动的深度智能体
=======================================

为无人机蜂群仿真提供 LLM 增强的智能体决策能力。

架构层次：
┌──────────────────────────────────────┐
│           DeepAgent (本模块)          │
│  ┌────────────────────────────────┐  │
│  │  感知层: 态势编码 → Prompt生成   │  │
│  ├────────────────────────────────┤  │
│  │  推理层: LLM异步调用 + 缓存      │  │
│  ├────────────────────────────────┤  │
│  │  行动层: 决策验证 → FSM指令     │  │
│  └────────────────────────────────┘  │
├──────────────────────────────────────┤
│           SimulationEngine            │
│     UAVAgent (FSM执行)                │
└──────────────────────────────────────┘

设计原则：
1. 异步非阻塞 —— LLM调用不阻塞仿真tick
2. FSM安全网 —— LLM异常/超时时自动回退到FSM
3. 冷却机制 —— 控制LLM调用频率，控制成本
4. 决策验证 —— LLM输出必须通过白名单校验

使用示例:
    from core.llm_agent import DeepAgent
    from core.llm_client import get_llm_client

    client = get_llm_client()
    agent = DeepAgent(
        agent_id="UAV_01",
        role="striker",
        llm_client=client,
        prompt_category="striker",
    )

    # 异步调用（不阻塞）
    decision = await agent.reason(state_snapshot)
    if decision:
        uav.apply_decision(decision)
"""
import json
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from config import (
    LLM_ENABLED, LLM_CALL_INTERVAL_TICKS,
    LLM_CALL_INTERVAL_LEADER, LLM_CALL_INTERVAL_STRIKER,
    LLM_CALL_INTERVAL_SCOUT, LLM_TOKEN_BUDGET_PER_TICK,
)
from core.llm_client import LLMClient, LLMResponse


class DecisionAction(str, Enum):
    """LLM 可输出的战术行动"""
    ENGAGE = "engage"               # 攻击指定目标
    SCOUT = "scout"                 # 侦察指定区域
    RETREAT = "retreat"             # 撤退
    HOLD = "hold"                   # 待命/保持当前位置
    SUPPORT = "support"             # 支援友军
    RELAY = "relay"                 # 切换为中继模式
    COORDINATE = "coordinate"       # 请求协同打击
    CHANGE_TARGET = "change_target" # 切换目标
    RTB = "rtb"                     # 返回基地


@dataclass
class TacticalDecision:
    """战术决策输出"""
    action: DecisionAction
    target_id: Optional[str] = None
    target_pos: Optional[dict] = None
    support_uav_id: Optional[str] = None
    reasoning: str = ""
    confidence: float = 0.5
    urgency: str = "normal"  # low / normal / high / critical
    raw_response: Optional[LLMResponse] = None

    def is_valid(self) -> bool:
        """校验决策有效性"""
        if self.action in (DecisionAction.ENGAGE, DecisionAction.CHANGE_TARGET):
            return self.target_id is not None
        if self.action == DecisionAction.SUPPORT:
            return self.support_uav_id is not None
        return True


# ============ JSON Schema 定义 ============

TACTICAL_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": [a.value for a in DecisionAction],
            "description": "选择的战术行动",
        },
        "target_id": {
            "type": "string",
            "description": "目标ID (engage/change_target时必填)",
        },
        "target_pos": {
            "type": "object",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "z": {"type": "number"},
            },
            "description": "目标或移动位置坐标",
        },
        "support_uav_id": {
            "type": "string",
            "description": "支援目标UAV ID (support时必填)",
        },
        "reasoning": {
            "type": "string",
            "description": "决策推理链 (Chain-of-Thought)",
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "决策置信度 (0-1)",
        },
        "urgency": {
            "type": "string",
            "enum": ["low", "normal", "high", "critical"],
            "description": "行动紧急程度",
        },
    },
    "required": ["action", "reasoning", "confidence"],
}


# ============ Action 白名单（安全约束） ============

# 每角色允许的行动
ROLE_ACTION_WHITELIST = {
    "leader": [DecisionAction.COORDINATE, DecisionAction.CHANGE_TARGET,
               DecisionAction.HOLD, DecisionAction.RETREAT, DecisionAction.SUPPORT],
    "scout": [DecisionAction.SCOUT, DecisionAction.RETREAT,
              DecisionAction.HOLD, DecisionAction.CHANGE_TARGET],
    "striker": [DecisionAction.ENGAGE, DecisionAction.RETREAT,
                DecisionAction.HOLD, DecisionAction.CHANGE_TARGET,
                DecisionAction.SUPPORT],
    "relay": [DecisionAction.RELAY, DecisionAction.RETREAT,
              DecisionAction.HOLD, DecisionAction.CHANGE_TARGET],
}

# 每状态允许的行动
STATE_ACTION_WHITELIST = {
    "IDLE":       [DecisionAction.SCOUT, DecisionAction.ENGAGE, DecisionAction.HOLD,
                   DecisionAction.RELAY],
    "MOVING":     [DecisionAction.ENGAGE, DecisionAction.HOLD, DecisionAction.RETREAT,
                   DecisionAction.CHANGE_TARGET],
    "SCOUTING":   [DecisionAction.HOLD, DecisionAction.RETREAT,
                   DecisionAction.CHANGE_TARGET, DecisionAction.COORDINATE],
    "ENGAGING":   [DecisionAction.ENGAGE, DecisionAction.RETREAT, DecisionAction.HOLD,
                   DecisionAction.SUPPORT, DecisionAction.CHANGE_TARGET],
    "DAMAGED":    [DecisionAction.RETREAT, DecisionAction.HOLD, DecisionAction.RTB],
    "RETREATING": [DecisionAction.HOLD, DecisionAction.RTB],
    "RELAYING":   [DecisionAction.HOLD, DecisionAction.RETREAT],
    "MANUAL":     [],  # 手动模式下不允许AI决策
    "DESTROYED":  [],
}


class DeepAgent:
    """
    LLM 驱动的深度智能体

    为 UAV Agent 提供 LLM 增强的战术推理能力。
    通过 FSM + LLM 混合架构：FSM 保证实时执行，LLM 提供高层战术规划。
    """

    def __init__(
        self,
        agent_id: str,
        role: str,
        llm_client: LLMClient,
        prompt_category: str = "uav_tactical",
        call_interval: Optional[int] = None,
    ):
        self.agent_id = agent_id
        self.role = role
        self.llm = llm_client
        self.prompt_category = prompt_category

        # 调用频率控制
        if call_interval is not None:
            self.call_interval = call_interval
        else:
            interval_map = {
                "leader": LLM_CALL_INTERVAL_LEADER,
                "striker": LLM_CALL_INTERVAL_STRIKER,
                "scout": LLM_CALL_INTERVAL_SCOUT,
            }
            self.call_interval = interval_map.get(role, LLM_CALL_INTERVAL_TICKS)

        # 状态
        self.last_call_tick = -999
        self.last_decision: Optional[TacticalDecision] = None
        self.decision_history: list = []     # 最近N条决策历史
        self._pending_task: Optional[asyncio.Task] = None

        # 统计
        self.total_llm_calls = 0
        self.cached_decisions = 0
        self.failed_decisions = 0

    # ============ 公开接口 ============

    def should_reason(self, tick: int, force: bool = False) -> bool:
        """判断当前tick是否应该触发LLM推理"""
        if not LLM_ENABLED:
            return False
        if force:
            return True
        return (tick - self.last_call_tick) >= self.call_interval

    async def reason(
        self,
        state_snapshot: dict,
        tick: int,
        force: bool = False,
    ) -> Optional[TacticalDecision]:
        """
        执行LLM推理，返回战术决策

        Args:
            state_snapshot: UAV当前状态快照 (get_status()输出)
            tick: 当前仿真tick
            force: 是否强制调用（忽略冷却）

        Returns:
            TacticalDecision 或 None (无需决策/调用失败)
        """
        if not self.should_reason(tick, force):
            return self.last_decision  # 返回上一次决策

        self.last_call_tick = tick

        # 构建 Prompt
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(state_snapshot, tick)

        # 异步调用 LLM
        response = await self.llm.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_schema=TACTICAL_DECISION_SCHEMA,
            temperature=0.3,
        )

        self.total_llm_calls += 1

        if response.error:
            self.failed_decisions += 1
            return self.last_decision  # 失败时返回上次决策

        if response.cached:
            self.cached_decisions += 1

        # 解析 LLM 输出
        decision = self._parse_decision(response)
        if decision and self._validate_decision(decision, state_snapshot):
            self.last_decision = decision
            self.decision_history.append({
                "tick": tick,
                "decision": decision,
            })
            # 保留最近20条
            if len(self.decision_history) > 20:
                self.decision_history = self.decision_history[-20:]
            return decision

        self.failed_decisions += 1
        return self.last_decision

    async def reason_critical(
        self, state_snapshot: dict, tick: int
    ) -> Optional[TacticalDecision]:
        """关键决策（使用更强的模型）"""
        # 强制调用 + 使用关键模型
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(state_snapshot, tick) + "\n[CRITICAL DECISION REQUIRED]"

        response = await self.llm.generate_critical(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_schema=TACTICAL_DECISION_SCHEMA,
        )

        self.total_llm_calls += 1
        decision = self._parse_decision(response)
        if decision and self._validate_decision(decision, state_snapshot):
            self.last_decision = decision
            return decision
        return self.last_decision

    # ============ Prompt 构建 ============

    def _build_system_prompt(self) -> str:
        """构建系统提示词"""
        return f"""You are an autonomous combat UAV tactical AI. Your role is {self.role.upper()}.
Your UAV ID is {self.agent_id}.

## Your Mission
You are part of a drone swarm conducting combat operations. Your goal is to:
1. Complete assigned mission objectives efficiently
2. Minimize UAV losses (your own and teammates)
3. Share target information with the swarm
4. Adapt tactics based on the evolving battlefield situation

## Decision Principles
- Prioritize survival when health < 30%
- Coordinate attacks: multiple UAVs on one target kills faster
- Scouts should prioritize finding new targets
- Strikers should engage the nearest/threat-level targets
- If your leader is destroyed, follow the new leader's commands
- Retreat if isolated (no neighbors) and health < 50%

## Output Format
Always output a JSON decision with: action, target_id (if applicable), reasoning, confidence, urgency.
Base your reasoning on the tactical situation provided.
Your reasoning must be a chain-of-thought: situation → options → choice → expected outcome."""

    def _build_user_prompt(self, state: dict, tick: int) -> str:
        """构建用户提示词（战场态势编码）"""
        position = state.get("position", {})
        velocity = state.get("velocity", {})
        attitude = state.get("attitude", {})
        health = state.get("health", 100)
        battery = state.get("battery", 100)
        current_state = state.get("state", "IDLE")
        current_task = state.get("current_task", "none")
        is_leader = state.get("is_leader", False)
        neighbors = state.get("neighbors", [])
        targets_engaged = state.get("targets_engaged", 0)
        weapon_range = state.get("weapon_range", 2000)

        # 计算速度
        speed = math.sqrt(
            velocity.get("x", 0) ** 2 +
            velocity.get("y", 0) ** 2 +
            velocity.get("z", 0) ** 2
        )

        prompt = f"""## Current Situation (Tick {tick})

### Own Status
- Position: X={position.get('x', 0):.0f}, Y={position.get('y', 0):.0f}, Z={position.get('z', 200):.0f}
- Velocity: {speed:.0f} m/s
- Heading: {attitude.get('yaw', 0):.0f}°
- Health: {health:.0f}% | Battery: {battery:.0f}%
- State: {current_state}
- Role: {self.role} {'(LEADER)' if is_leader else ''}

### Current Task
{current_task or 'No active task'}

### Swarm Status
- Leader: {'SELF' if is_leader else 'Active'}
- Connected Neighbors: {len(neighbors)} ({', '.join(neighbors[:5]) if neighbors else 'none'})
- Targets Engaged So Far: {targets_engaged}

### Capabilities
- Weapon Range: {weapon_range:.0f}m
- Role Strengths: {self._get_role_strengths()}"""

        # 添加最近决策历史
        if self.decision_history:
            recent = self.decision_history[-3:]
            prompt += "\n\n### Recent Decisions\n"
            for d in recent:
                dd = d["decision"]
                prompt += f"- T{d['tick']}: {dd.action.value} (confidence: {dd.confidence:.0%})\n"

        prompt += "\n\nBased on the above, output your tactical decision as JSON."
        return prompt

    def _get_role_strengths(self) -> str:
        """获取角色能力描述"""
        strengths = {
            "leader": "Global coordination, task assignment authority",
            "scout": "Extended detection range, fast speed, target discovery",
            "striker": "High weapon damage, optimized for engagement",
            "relay": "Extended communication range, network relay capability",
        }
        return strengths.get(self.role, "General purpose")

    # ============ 决策解析与验证 ============

    def _parse_decision(self, response: LLMResponse) -> Optional[TacticalDecision]:
        """从 LLM 响应中解析战术决策"""
        data = response.structured
        if not data:
            # 尝试从文本中提取 JSON
            try:
                text = response.content
                # 查找 JSON 块
                if "```json" in text:
                    json_str = text.split("```json")[1].split("```")[0]
                elif "{" in text:
                    json_str = text[text.find("{"):text.rfind("}") + 1]
                else:
                    return None
                data = json.loads(json_str)
            except (json.JSONDecodeError, IndexError):
                return None

        try:
            action = DecisionAction(data.get("action", "hold"))
            return TacticalDecision(
                action=action,
                target_id=data.get("target_id"),
                target_pos=data.get("target_pos"),
                support_uav_id=data.get("support_uav_id"),
                reasoning=data.get("reasoning", ""),
                confidence=float(data.get("confidence", 0.5)),
                urgency=data.get("urgency", "normal"),
                raw_response=response,
            )
        except (ValueError, TypeError):
            return None

    def _validate_decision(
        self, decision: TacticalDecision, state: dict
    ) -> bool:
        """验证决策是否符合安全约束"""
        # 1. 角色白名单检查
        role_allowed = ROLE_ACTION_WHITELIST.get(self.role, [])
        if decision.action not in role_allowed:
            return False

        # 2. 状态白名单检查
        current_state = state.get("state", "IDLE")
        state_allowed = STATE_ACTION_WHITELIST.get(current_state, [])
        if decision.action not in state_allowed:
            return False

        # 3. 置信度检查
        if decision.confidence < 0.3:
            return False  # 低置信度决策不予采纳

        # 4. 特定行动的安全检查
        if decision.action == DecisionAction.ENGAGE:
            health = state.get("health", 100)
            if health < 15:
                return False  # 血量过低不允许攻击（应撤退）

        # 5. RTB仅在低电量/低血量时允许
        if decision.action == DecisionAction.RTB:
            battery = state.get("battery", 100)
            health = state.get("health", 100)
            if battery > 30 and health > 50:
                return False  # 状态良好不应返航

        return True

    # ============ 统计 ============

    def get_stats(self) -> dict:
        """获取 DeepAgent 统计"""
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "total_llm_calls": self.total_llm_calls,
            "cached_decisions": self.cached_decisions,
            "failed_decisions": self.failed_decisions,
            "last_decision": self.last_decision.action.value if self.last_decision else None,
            "decision_history_len": len(self.decision_history),
            "llm_stats": self.llm.get_stats() if self.llm else {},
        }


# 用于 async 调用的 import
import asyncio
