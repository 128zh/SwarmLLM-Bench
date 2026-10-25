"""
智能红军 (DeepRedForceAgent) — LLM驱动的自适应对手
====================================================

从固定防御规则升级为 LLM 驱动的智能红军指挥官。

核心能力:
1. 自适应战术: 根据蓝方行为动态调整防御策略
2. 欺骗与诱饵: 设置假目标、伪装阵地
3. 电子战模拟: 通信干扰、GPS欺骗
4. 学习能力: 从多轮对抗中积累蓝方行为模式
5. 策略进化: 每轮对抗后调整策略

架构:
┌──────────────────────────────────────────┐
│           DeepRedForceAgent               │
│                                          │
│  ┌────────────────────────────────┐     │
│  │  红军指挥官 (LLM)                │     │
│  │  • 蓝方行为分析                 │     │
│  │  • 防御策略生成                 │     │
│  │  • 资源调配                     │     │
│  └───────────┬────────────────────┘     │
│              │                           │
│  ┌───────────┴────────────────────┐     │
│  │  防御执行层 (规则引擎)           │     │
│  │  • 防御范围攻击                 │     │
│  │  • 火力分配                     │     │
│  │  • 状态更新                     │     │
│  └────────────────────────────────┘     │
│                                          │
│  ┌────────────────────────────────┐     │
│  │  策略记忆 (跨轮次)               │     │
│  │  • 蓝方战术画像                 │     │
│  │  • 历史策略效果                 │     │
│  │  • 自适应参数调整               │     │
│  └────────────────────────────────┘     │
└──────────────────────────────────────────┘
"""
import math
import random
from dataclasses import dataclass, field
from typing import Optional
from collections import defaultdict

from config import MAP_WIDTH, MAP_HEIGHT, distance
from core.llm_client import LLMClient


# ============ 红军数据结构 ============

@dataclass
class DefenseAsset:
    """防御资产"""
    asset_id: str
    asset_type: str          # command_post / radar_station / vehicle_convoy / sam_site
    position: dict
    health: float = 100.0
    max_health: float = 100.0
    defense_range: float = 2000.0
    defense_damage: float = 4.0
    destroyed: bool = False
    is_decoy: bool = False   # 是否诱饵
    jammer_active: bool = False


@dataclass
class BlueForceProfile:
    """蓝方行为画像（跨轮次学习）"""
    preferred_attack_vector: str = "unknown"  # 偏好攻击方向
    avg_approach_altitude: float = 200.0
    scout_aggressiveness: float = 0.5
    coordination_level: float = 0.5           # 协同程度
    uav_loss_tolerance: float = 0.5           # 损失容忍度
    rounds_observed: int = 0
    raw_observations: list = field(default_factory=list)


@dataclass
class RedStrategy:
    """红军策略"""
    strategy_name: str
    defense_positions: dict[str, dict]  # asset_id -> new_position
    jammer_targets: list                # 干扰目标区域
    decoy_positions: list               # 诱饵位置
    focus_fire_target: Optional[str]    # 集火目标
    reasoning: str
    confidence: float = 0.5


# ============ 红军决策 Schema ============

RED_STRATEGY_SCHEMA = {
    "type": "object",
    "properties": {
        "strategy_name": {"type": "string"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [
                            "redeploy_defense", "activate_jammer",
                            "deploy_decoy", "focus_fire",
                            "retreat_asset", "concentrate_defense",
                        ],
                    },
                    "target_asset": {"type": "string"},
                    "target_position": {
                        "type": "object",
                        "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
                    },
                    "target_uav": {"type": "string"},
                    "reasoning": {"type": "string"},
                },
            },
        },
        "overall_reasoning": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["strategy_name", "actions", "overall_reasoning"],
}


class RedForceAgent:
    """
    LLM驱动的红军指挥官

    在每个决策周期（每30 tick）评估战场态势并调整防御策略。
    """

    def __init__(
        self,
        llm_client: LLMClient,
        difficulty: str = "normal",
        use_llm: bool = False,
    ):
        self.llm = llm_client
        self.difficulty = difficulty
        self.use_llm = use_llm

        # 防御资产
        self.assets: dict[str, DefenseAsset] = {}

        # 蓝方画像
        self.blue_profile = BlueForceProfile()

        # 策略历史
        self.strategy_history: list[RedStrategy] = []
        self._last_decision_tick = -999
        self._decision_interval = 30  # 每30 tick决策一次

        # 电子战状态
        self.jammer_active: dict[str, bool] = {}
        self.decoys_deployed: list = []

        # 按难度调整参数
        self._difficulty_params = {
            "easy":   {"decision_interval": 50, "llm_temperature": 0.7, "defense_mult": 0.7},
            "normal": {"decision_interval": 30, "llm_temperature": 0.4, "defense_mult": 1.0},
            "hard":   {"decision_interval": 20, "llm_temperature": 0.2, "defense_mult": 1.3},
        }
        self.diff_params = self._difficulty_params.get(difficulty, self._difficulty_params["normal"])
        self._decision_interval = self.diff_params["decision_interval"]

    # ============ 资产初始化 ============

    def register_asset(self, asset_id: str, asset_type: str, position: dict,
                       defense_range: float = 2000.0, defense_damage: float = 4.0):
        """注册防御资产"""
        mult = self.diff_params["defense_mult"]
        self.assets[asset_id] = DefenseAsset(
            asset_id=asset_id,
            asset_type=asset_type,
            position=dict(position),
            defense_range=defense_range * mult,
            defense_damage=defense_damage * mult,
        )

    def register_assets_from_targets(self, targets: dict):
        """从环境目标注册防御资产"""
        for tid, t in targets.items():
            if t.get("destroyed"):
                continue
            self.register_asset(
                asset_id=tid,
                asset_type=t.get("type", "command_post"),
                position=t["position"],
                defense_range=t.get("defense_range", 2000),
                defense_damage=t.get("defense_damage", 4.0),
            )

    # ============ 决策循环 ============

    def should_decide(self, tick: int, force: bool = False) -> bool:
        """是否应该进行策略决策"""
        if force:
            return True
        return (tick - self._last_decision_tick) >= self._decision_interval

    async def decide(self, blue_state: dict, tick: int) -> Optional[RedStrategy]:
        """
        红军策略决策

        Args:
            blue_state: 蓝方状态 (simulation.get_state())
            tick: 当前tick

        Returns:
            RedStrategy 或 None (无需调整)
        """
        if not self.should_decide(tick):
            return None

        self._last_decision_tick = tick

        # 更新蓝方画像
        self._update_blue_profile(blue_state, tick)

        if self.use_llm:
            return await self._decide_llm(blue_state, tick)
        else:
            return self._decide_rules(blue_state, tick)

    async def _decide_llm(self, blue_state: dict, tick: int) -> RedStrategy:
        """LLM驱动的策略决策"""
        prompt = self._build_strategy_prompt(blue_state, tick)

        response = await self.llm.generate(
            system_prompt=self._get_red_commander_prompt(),
            user_prompt=prompt,
            output_schema=RED_STRATEGY_SCHEMA,
            temperature=self.diff_params["llm_temperature"],
        )

        if response.structured:
            strategy = RedStrategy(
                strategy_name=response.structured.get("strategy_name", "default"),
                defense_positions={},
                jammer_targets=[],
                decoy_positions=[],
                focus_fire_target=None,
                reasoning=response.structured.get("overall_reasoning", ""),
                confidence=response.structured.get("confidence", 0.5),
            )

            for action in response.structured.get("actions", []):
                atype = action.get("type")
                if atype == "redeploy_defense" and action.get("target_position"):
                    asset_id = action.get("target_asset", "")
                    strategy.defense_positions[asset_id] = action["target_position"]
                elif atype == "activate_jammer" and action.get("target_position"):
                    strategy.jammer_targets.append(action["target_position"])
                elif atype == "deploy_decoy" and action.get("target_position"):
                    strategy.decoy_positions.append(action["target_position"])
                elif atype == "focus_fire" and action.get("target_uav"):
                    strategy.focus_fire_target = action["target_uav"]
        else:
            # LLM失败 → 规则引擎回退
            strategy = self._decide_rules(blue_state, tick)

        self.strategy_history.append(strategy)
        if len(self.strategy_history) > 50:
            self.strategy_history = self.strategy_history[-50:]

        return strategy

    def _decide_rules(self, blue_state: dict, tick: int) -> RedStrategy:
        """规则引擎策略（LLM不可用时的回退）"""
        strategy = RedStrategy(
            strategy_name="rule_based_adaptive",
            defense_positions={},
            jammer_targets=[],
            decoy_positions=[],
            focus_fire_target=None,
            reasoning="Rule engine fallback",
        )

        # 找到最近的蓝方UAV
        nearest_uav_id = None
        nearest_dist = float("inf")
        for uid, uav in blue_state.get("uavs", {}).items():
            if not uav.get("alive"):
                continue
            for aid, asset in self.assets.items():
                if asset.destroyed or asset.is_decoy:
                    continue
                dist = distance(uav["position"], asset.position)
                if dist < nearest_dist:
                    nearest_dist = dist
                    nearest_uav_id = uid

        # 策略1: 集火最近的UAV
        if nearest_uav_id:
            strategy.focus_fire_target = nearest_uav_id
            strategy.reasoning += f" Focusing fire on {nearest_uav_id} (dist={nearest_dist:.0f}m)."

        # 策略2: 如果某个资产血量<30%，在附近部署诱饵
        for aid, asset in self.assets.items():
            if asset.destroyed:
                continue
            if asset.health < 30:
                decoy_pos = {
                    "x": asset.position["x"] + random.uniform(-500, 500),
                    "y": asset.position["y"] + random.uniform(-500, 500),
                }
                strategy.decoy_positions.append(decoy_pos)
                strategy.reasoning += f" Deploying decoy near {aid} (HP={asset.health:.0f}%)."

        return strategy

    # ============ 策略执行 ============

    def apply_strategy(self, strategy: RedStrategy, targets: dict):
        """将策略应用到仿真环境"""
        # 重新部署防御
        for asset_id, new_pos in strategy.defense_positions.items():
            if asset_id in self.assets:
                self.assets[asset_id].position = new_pos
            if asset_id in targets:
                targets[asset_id]["position"] = new_pos

        # 激活干扰器
        for jam_pos in strategy.jammer_targets:
            jammer_id = f"JAMMER_{len(self.jammer_active)}"
            self.jammer_active[jammer_id] = True

        # 部署诱饵
        for decoy_pos in strategy.decoy_positions:
            decoy_id = f"DECOY_{len(self.decoys_deployed)}"
            self.decoys_deployed.append(decoy_id)
            self.assets[decoy_id] = DefenseAsset(
                asset_id=decoy_id,
                asset_type="decoy",
                position=decoy_pos,
                health=50.0,
                max_health=50.0,
                defense_range=500.0,
                defense_damage=0.5,
                is_decoy=True,
            )
            if targets is not None:
                targets[decoy_id] = {
                    "id": decoy_id,
                    "position": decoy_pos,
                    "health": 50.0,
                    "max_health": 50.0,
                    "type": "decoy",
                    "destroyed": False,
                    "defense_range": 500.0,
                    "defense_damage": 0.5,
                }

    # ============ 蓝方画像更新 ============

    def _update_blue_profile(self, state: dict, tick: int):
        """从当前状态更新蓝方行为画像"""
        uavs = state.get("uavs", {})

        self.blue_profile.rounds_observed += 1

        # 分析攻击方向
        attack_positions = []
        for uid, uav in uavs.items():
            if not uav.get("alive") or uav.get("state") != "ENGAGING":
                continue
            attack_positions.append(uav["position"])

        if attack_positions:
            avg_x = sum(p["x"] for p in attack_positions) / len(attack_positions)
            avg_y = sum(p["y"] for p in attack_positions) / len(attack_positions)

            # 确定攻击方向
            if avg_x < MAP_WIDTH * 0.3:
                self.blue_profile.preferred_attack_vector = "west"
            elif avg_x > MAP_WIDTH * 0.7:
                self.blue_profile.preferred_attack_vector = "east"
            else:
                self.blue_profile.preferred_attack_vector = "center"

        # 分析协同程度 (同时处于ENGAGING的UAV数量)
        engaging_count = sum(1 for u in uavs.values()
                            if u.get("alive") and u.get("state") == "ENGAGING")
        total_alive = sum(1 for u in uavs.values() if u.get("alive"))
        if total_alive > 0:
            self.blue_profile.coordination_level = (
                0.7 * self.blue_profile.coordination_level +
                0.3 * (engaging_count / total_alive)
            )

    # ============ Prompt 构建 ============

    def _get_red_commander_prompt(self) -> str:
        return f"""You command defensive forces against an attacking drone swarm.

## Your Assets ({len(self.assets)} units)
{chr(10).join(f"- {a.asset_id} ({a.asset_type}): HP={a.health:.0f}%, Range={a.defense_range:.0f}m, Pos=({a.position['x']:.0f},{a.position['y']:.0f})" for a in self.assets.values() if not a.destroyed and not a.is_decoy)}

## Difficulty: {self.difficulty.upper()}

## Available Actions
- redeploy_defense: Move a defense unit to a better position
- activate_jammer: Jam enemy communications in an area (reduces UAV coordination)
- deploy_decoy: Create a decoy target to waste enemy fire
- focus_fire: Concentrate all defenses on one high-value target
- retreat_asset: Move a threatened asset to safety
- concentrate_defense: Bring defenses closer together for mutual support

## Blue Force Intelligence (from {self.blue_profile.rounds_observed} rounds of observation)
- Preferred Attack Vector: {self.blue_profile.preferred_attack_vector}
- Coordination Level: {self.blue_profile.coordination_level:.0%}

## Tactical Principles
- Protect your most valuable assets first (command posts > radar > vehicles)
- Use decoys when assets are below 50% health
- Jam communications when enemy coordination is high
- Focus fire on the most threatening UAV (closest to key assets)"""

    def _build_strategy_prompt(self, blue_state: dict, tick: int) -> str:
        """构建策略决策Prompt"""
        uavs = blue_state.get("uavs", {})

        threat_lines = []
        for uid, uav in uavs.items():
            if not uav.get("alive"):
                continue
            pos = uav.get("position", {})
            # 找到最近的受威胁资产
            min_dist = float("inf")
            threatened_asset = None
            for aid, asset in self.assets.items():
                if asset.destroyed or asset.is_decoy:
                    continue
                dist = distance(pos, asset.position)
                if dist < min_dist:
                    min_dist = dist
                    threatened_asset = aid

            threat_lines.append(
                f"- {uid} ({uav.get('role','?')}): "
                f"HP={uav.get('health',0):.0f}%, "
                f"Dist to {threatened_asset}={min_dist:.0f}m, "
                f"State={uav.get('state','?')}"
            )

        asset_lines = []
        for aid, asset in self.assets.items():
            if asset.is_decoy:
                continue
            asset_lines.append(
                f"- {aid} ({asset.asset_type}): "
                f"HP={asset.health:.0f}%/{asset.max_health:.0f}%, "
                f"Destroyed={asset.destroyed}"
            )

        return f"""## Battlefield Snapshot (Tick {tick})

### Blue Force Threats
{chr(10).join(threat_lines[:10])}

### Defense Asset Status
{chr(10).join(asset_lines)}

### Environment
- Wind: Level {blue_state.get('environment',{}).get('wind',{}).get('level',0)}
- Precipitation: {blue_state.get('environment',{}).get('precipitation',{}).get('type','none')}
- Lighting: {blue_state.get('environment',{}).get('lighting',{}).get('type','day')}

### Decision Required
Based on the above, what defensive adjustments should be made?
Output your strategy as JSON."""

    # ============ 统计 ============

    def get_stats(self) -> dict:
        return {
            "total_assets": len(self.assets),
            "destroyed_assets": sum(1 for a in self.assets.values() if a.destroyed),
            "decoys_deployed": len(self.decoys_deployed),
            "jammers_active": sum(1 for j in self.jammer_active.values() if j),
            "strategies_generated": len(self.strategy_history),
            "blue_profile": {
                "attack_vector": self.blue_profile.preferred_attack_vector,
                "coordination": round(self.blue_profile.coordination_level, 2),
                "rounds_observed": self.blue_profile.rounds_observed,
            },
            "difficulty": self.difficulty,
            "use_llm": self.use_llm,
        }
