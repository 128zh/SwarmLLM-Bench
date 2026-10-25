"""
自然语言指挥接口 (Natural Language Command Interface)
=====================================================

将人类操作员的自然语言指令转换为结构化的仿真命令。

能力:
- 任务理解: "侦察B区" → SCOUT mission in B quadrant
- 协同指挥: "集中火力攻击TARGET_01" → COORDINATE strike
- 态势查询: "汇报当前态势" → 生成自然语言简报
- 战术建议: "有什么建议?" → LLM分析战场并给出建议

架构:
  人类操作员 (NL) → CommandParser (LLM) → MissionPlan (JSON) → SimulationEngine
  仿真状态 (JSON) → ReportGenerator (LLM) → 自然语言简报 → 人类操作员

使用示例:
    from core.nl_command import NLCommandInterface

    nli = NLCommandInterface(llm_client)
    result = await nli.process("侦察东南区域，发现目标后呼叫攻击")
    # result = {"action": "create_mission", "params": {...}}
"""
import json
from dataclasses import dataclass, field
from typing import Optional

from core.llm_client import LLMClient, LLMResponse


# ============ 命令理解 Schema ============

COMMAND_PARSE_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": [
                "create_mission", "adjust_tactics", "query_status",
                "request_advice", "control_uav", "scenario_control",
                "unknown",
            ],
            "description": "识别出的用户意图",
        },
        "mission_params": {
            "type": "object",
            "properties": {
                "task_type": {
                    "type": "string",
                    "enum": ["scout", "strike", "defend", "rescue", "patrol", "relay"],
                },
                "area": {"type": "string", "description": "目标区域描述 (如 'southeast', 'B区')"},
                "area_bounds": {
                    "type": "object",
                    "properties": {
                        "x_min": {"type": "number"}, "x_max": {"type": "number"},
                        "y_min": {"type": "number"}, "y_max": {"type": "number"},
                    },
                },
                "target_description": {"type": "string"},
                "uav_count": {"type": "integer"},
                "uav_roles": {"type": "array", "items": {"type": "string"}},
                "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                "trigger_condition": {"type": "string"},
                "follow_up_action": {"type": "string"},
            },
        },
        "tactics_params": {
            "type": "object",
            "properties": {
                "formation": {"type": "string"},
                "engagement_rules": {"type": "string"},
                "retreat_threshold": {"type": "number"},
                "coordination_mode": {"type": "string"},
            },
        },
        "query_type": {
            "type": "string",
            "enum": ["full_briefing", "uav_status", "target_status", "network_status", "threat_assessment"],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "clarification_needed": {"type": "boolean"},
        "clarification_question": {"type": "string"},
        "explanation": {"type": "string"},
    },
    "required": ["intent", "explanation"],
}


# ============ 态势简报 Schema ============

SITREP_GENERATION_PROMPT = """You are a military briefing officer. Generate a concise situation report based on the drone swarm status provided.

Report structure:
1. OVERALL STATUS: Mission progress, UAV survival, target status
2. THREAT ASSESSMENT: Most dangerous targets, UAVs at risk
3. RECOMMENDATIONS: Tactical suggestions for the commander
4. KEY METRICS: 3-5 bullet points with critical numbers

Tone: Professional, concise, actionable. Use military terminology appropriately."""


@dataclass
class ParsedCommand:
    """解析后的命令"""
    intent: str
    params: dict = field(default_factory=dict)
    confidence: float = 0.5
    needs_clarification: bool = False
    clarification: str = ""
    explanation: str = ""
    raw_text: str = ""


@dataclass
class SituationReport:
    """态势简报"""
    overall: str = ""
    threats: str = ""
    recommendations: list = field(default_factory=list)
    metrics: list = field(default_factory=list)
    raw_response: Optional[LLMResponse] = None


# ============ 区域映射 ============

# 将自然语言区域描述映射到地图坐标 (10000x10000m)
AREA_MAPPING = {
    "north":      (0, 10000, 0, 5000),
    "northeast":  (5000, 10000, 0, 5000),
    "northwest":  (0, 5000, 0, 5000),
    "south":      (0, 10000, 5000, 10000),
    "southeast":  (5000, 10000, 5000, 10000),
    "southwest":  (0, 5000, 5000, 10000),
    "east":       (5000, 10000, 0, 10000),
    "west":       (0, 5000, 0, 10000),
    "center":     (2500, 7500, 2500, 7500),
    "a区":        (0, 5000, 0, 5000),
    "b区":        (5000, 10000, 0, 5000),
    "c区":        (0, 5000, 5000, 10000),
    "d区":        (5000, 10000, 5000, 10000),
    "全图":        (0, 10000, 0, 10000),
}


class NLCommandInterface:
    """
    自然语言指挥接口

    处理人类操作员的自然语言指令，转换为仿真命令。
    """

    def __init__(self, llm_client: LLMClient, map_width: float = 10000, map_height: float = 10000):
        self.llm = llm_client
        self.map_w = map_width
        self.map_h = map_height
        self.command_history: list[ParsedCommand] = []
        self.report_history: list[SituationReport] = []

    # ============ 命令解析 ============

    async def process(self, text: str, context: dict = None) -> ParsedCommand:
        """处理自然语言指令"""
        prompt = self._build_parse_prompt(text, context)

        response = await self.llm.generate(
            system_prompt=self._get_command_system_prompt(),
            user_prompt=prompt,
            output_schema=COMMAND_PARSE_SCHEMA,
            temperature=0.2,
        )

        if response.structured:
            cmd = ParsedCommand(
                intent=response.structured.get("intent", "unknown"),
                params=response.structured.get("mission_params", {}) or
                        response.structured.get("tactics_params", {}),
                confidence=response.structured.get("confidence", 0.5),
                needs_clarification=response.structured.get("clarification_needed", False),
                clarification=response.structured.get("clarification_question", ""),
                explanation=response.structured.get("explanation", ""),
                raw_text=text,
            )
        else:
            cmd = ParsedCommand(
                intent="unknown",
                explanation=f"Failed to parse: {response.error}",
                raw_text=text,
            )

        # 解析区域坐标
        if cmd.intent == "create_mission" and cmd.params.get("area"):
            area_str = cmd.params["area"].lower()
            coords = self._resolve_area(area_str)
            if coords:
                cmd.params["area_bounds"] = {
                    "x_min": coords[0], "x_max": coords[1],
                    "y_min": coords[2], "y_max": coords[3],
                }

        self.command_history.append(cmd)
        return cmd

    def _resolve_area(self, area_str: str):
        """将自然语言区域描述解析为坐标"""
        # 先匹配长字符串（如 southeast 优先于 south）
        sorted_keys = sorted(AREA_MAPPING.keys(), key=len, reverse=True)
        for key in sorted_keys:
            if key in area_str:
                return AREA_MAPPING[key]
        return None

    def _build_parse_prompt(self, text: str, context: dict = None) -> str:
        """构建命令解析Prompt"""
        ctx_str = ""
        if context:
            ctx_str = f"""
## Current Context
- Mission Status: {context.get('mission_status', 'unknown')}
- UAVs Alive: {context.get('alive_uavs', 0)}/{context.get('total_uavs', 0)}
- Known Targets: {context.get('known_targets', 0)}
- Map: {self.map_w}x{self.map_h}m
"""

        return f"""{ctx_str}
## User Command
"{text}"

Parse this command into a structured intent.
Map areas: north/south/east/west/northeast/northwest/southeast/southwest/center.
Map areas: A区(NW quadrant), B区(NE quadrant), C区(SW quadrant), D区(SE quadrant).

Output the parsed intent as JSON."""

    def _get_command_system_prompt(self) -> str:
        """命令解析系统提示词"""
        return """You are a military UAV command interpreter. Parse natural language commands into structured operations.

## Available Intents
- create_mission: User wants to create a new mission (scout, strike, defend, rescue, patrol)
- adjust_tactics: User wants to change swarm tactics (formation, engagement rules, retreat threshold)
- query_status: User wants a situation report
- request_advice: User wants tactical recommendations
- control_uav: User wants to manually control a specific UAV
- scenario_control: User wants to control the simulation (start, pause, reset, speed)

## Area Resolution
Map the user's natural language area descriptions to coordinates on a 10000x10000m map.
Use the predefined area mapping.

## Important
- If the command is ambiguous, set clarification_needed=True and provide a clarification question
- Always explain your reasoning in the explanation field
- Set confidence based on how clear the intent is"""

    # ============ 态势简报生成 ============

    async def generate_sitrep(self, state: dict, query_type: str = "full_briefing") -> SituationReport:
        """生成自然语言态势简报"""
        prompt = self._build_sitrep_prompt(state, query_type)

        response = await self.llm.generate(
            system_prompt=SITREP_GENERATION_PROMPT,
            user_prompt=prompt,
            max_tokens=300,
            temperature=0.4,
        )

        report = SituationReport(raw_response=response)

        if response.content:
            # 解析简报各部分
            content = response.content
            sections = {
                "OVERALL STATUS": "",
                "THREAT ASSESSMENT": "",
                "RECOMMENDATIONS": [],
                "KEY METRICS": [],
            }

            current_section = None
            for line in content.split("\n"):
                line = line.strip()
                if not line:
                    continue
                for key in sections:
                    if key in line.upper():
                        current_section = key
                        break
                if current_section and line[0] in ("-", "*", "1", "2", "3", "•"):
                    clean = line.lstrip("-*•123456789. ").strip()
                    if current_section == "RECOMMENDATIONS":
                        sections["RECOMMENDATIONS"].append(clean)
                    elif current_section == "KEY METRICS":
                        sections["KEY METRICS"].append(clean)

            report.overall = sections["OVERALL STATUS"] or content[:200]
            report.threats = sections["THREAT ASSESSMENT"] or ""
            report.recommendations = sections["RECOMMENDATIONS"]
            report.metrics = sections["KEY METRICS"]

        self.report_history.append(report)
        return report

    def _build_sitrep_prompt(self, state: dict, query_type: str) -> str:
        """构建态势简报Prompt"""
        summary = state.get("summary", {})
        uavs = state.get("uavs", {})
        targets = state.get("targets", {})
        network = state.get("network", {})
        env = state.get("environment", {})

        uav_lines = []
        for uid, u in uavs.items():
            pos = u.get("position", {})
            uav_lines.append(
                f"- {uid} ({u.get('role','?')}): "
                f"HP={u.get('health',0):.0f}%, BAT={u.get('battery',0):.0f}%, "
                f"State={u.get('state','?')}, "
                f"Pos=({pos.get('x',0):.0f},{pos.get('y',0):.0f})"
            )

        target_lines = []
        for tid, t in targets.items():
            target_lines.append(
                f"- {tid}: HP={t.get('health',0):.0f}%, "
                f"Type={t.get('type','?')}, "
                f"Destroyed={t.get('destroyed',False)}"
            )

        return f"""## Current Battlefield Status (Tick {state.get('tick', 0)})

### Mission
- Status: {state.get('mission',{}).get('mission_status','unknown')}
- UAVs: {summary.get('alive_uavs',0)}/{summary.get('total_uavs',0)} alive
- Targets: {summary.get('destroyed_targets',0)}/{summary.get('total_targets',0)} destroyed

### UAV Status
{chr(10).join(uav_lines[:10])}

### Targets
{chr(10).join(target_lines)}

### Network
- Connected: {network.get('network_connected', False)}
- Leader: {network.get('leader_id', 'none')}
- Reconnects: {network.get('reconnect_count', 0)}

### Environment
- Wind: Level {env.get('wind',{}).get('level',0)}, Speed {env.get('wind',{}).get('speed',0)}m/s
- Precipitation: {env.get('precipitation',{}).get('type','none')}
- Lighting: {env.get('lighting',{}).get('type','day')}

### Query Type
{query_type}

Generate a situation report."""

    # ============ 战术建议 ============

    async def request_advice(self, state: dict) -> str:
        """请求战术建议"""
        prompt = self._build_sitrep_prompt(state, "request_advice")

        response = await self.llm.generate(
            system_prompt="""You are an expert UAV swarm tactician.
Analyze the battlefield situation and provide 3-5 specific, actionable tactical recommendations.
Consider: target prioritization, UAV positioning, coordination opportunities, threat avoidance, resource conservation.
Be concise and specific. Reference UAV IDs and target IDs.""",
            user_prompt=prompt,
            max_tokens=250,
            temperature=0.5,
        )

        return response.content or "Unable to generate tactical advice."

    # ============ 会话式交互 ============

    async def converse(self, user_message: str, state: dict = None) -> dict:
        """
        会话式交互：一次调用同时完成命令解析+态势更新

        Returns:
            {
                "command": ParsedCommand,        # 解析的命令
                "sitrep": SituationReport,       # 如果需要态势更新
                "advice": str,                   # 如果需要建议
                "response_text": str,            # 自然语言回复
            }
        """
        result = {"response_text": ""}

        # 第一步：解析命令
        cmd = await self.process(user_message, state)
        result["command"] = cmd

        # 第二步：根据意图执行
        if cmd.intent == "query_status":
            query_type = "full_briefing"
            if "query_type" in dir(cmd):
                query_type = getattr(cmd, 'params', {}).get('query_type', 'full_briefing')
            sitrep = await self.generate_sitrep(state, query_type)
            result["sitrep"] = sitrep
            parts = []
            if sitrep.overall:
                parts.append(sitrep.overall)
            if sitrep.recommendations:
                parts.append("\n建议:\n" + "\n".join(f"  - {r}" for r in sitrep.recommendations))
            result["response_text"] = "\n".join(parts)

        elif cmd.intent == "request_advice":
            advice = await self.request_advice(state)
            result["advice"] = advice
            result["response_text"] = advice

        elif cmd.needs_clarification:
            result["response_text"] = f"需要确认：{cmd.clarification}"

        else:
            result["response_text"] = f"理解：{cmd.explanation}\n意图：{cmd.intent}\n置信度：{cmd.confidence:.0%}"

        return result
