"""
拦截集群系统 (Interceptor Swarm System)
========================================

模拟敌方拦截无人机集群，对己方蜂群构成主动威胁。

核心机制:
1. 巡逻 (Patrol): 拦截器在指定区域巡逻搜索
2. 探测 (Detect): 在探测范围内发现己方UAV
3. 追踪 (Pursue): 锁定目标后持续追踪
4. 攻击 (Engage): 进入武器射程后发动攻击
5. 协同 (Coordinate): 多拦截器协同围堵单个UAV

威胁等级:
- LOW:    拦截器距离 > 3000m (安全)
- MEDIUM: 拦截器距离 1000-3000m (警戒)
- HIGH:   拦截器距离 500-1000m (危险)
- CRITICAL: 拦截器距离 < 500m (紧急规避)

规避策略:
- EVADE_SPEED:   全速逃离 (直线加速)
- EVADE_ZIGZAG:  蛇形机动 (随机变向)
- EVADE_ALTITUDE: 高度变化 (俯冲/爬升)
- EVADE_TERRAIN:  地形掩护 (飞向山地/城市)
- EVADE_SUPPORT:  呼叫友军支援

架构:
┌──────────────────────────────────────────┐
│           InterceptorSwarm               │
│                                          │
│  ┌────────────────────────────────┐     │
│  │  Interceptor Units (N个)        │     │
│  │  • 巡逻/探测/追踪/攻击/协同     │     │
│  │  • 3级AI: 基础/高级/LLM        │     │
│  └───────────┬────────────────────┘     │
│              │                           │
│  ┌───────────┴────────────────────┐     │
│  │  ThreatAssessment              │     │
│  │  • 威胁距离计算                 │     │
│  │  • 威胁等级评估                 │     │
│  │  • 规避策略推荐                 │     │
│  └────────────────────────────────┘     │
│                                          │
│  ┌────────────────────────────────┐     │
│  │  EvasionPlanner                │     │
│  │  • 5种规避策略                  │     │
│  │  • 地形感知规避                 │     │
│  │  • 协同规避 (蜂群分散)          │     │
│  └────────────────────────────────┘     │
└──────────────────────────────────────────┘
"""
import math
import random
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from config import (
    MAP_WIDTH, MAP_HEIGHT,
    UAV_MAX_SPEED, UAV_DETECTION_RANGE, UAV_WEAPON_RANGE,
    UAV_HEALTH_MAX, distance, distance_sq,
)


# ============ 枚举定义 ============

class InterceptorState(Enum):
    """拦截器状态"""
    PATROL = auto()       # 巡逻中
    DETECTED = auto()     # 发现目标
    PURSUING = auto()     # 追踪中
    ENGAGING = auto()     # 攻击中
    RETURNING = auto()    # 返回巡逻区
    DESTROYED = auto()    # 被摧毁


class ThreatLevel(Enum):
    """威胁等级"""
    NONE = 0        # 无威胁
    LOW = 1         # 低威胁 (距离>3000m)
    MEDIUM = 2      # 中威胁 (1000-3000m)
    HIGH = 3        # 高威胁 (500-1000m)
    CRITICAL = 4    # 紧急威胁 (<500m)


class EvasionStrategy(Enum):
    """规避策略"""
    NONE = "none"
    EVADE_SPEED = "speed"         # 全速直线逃离
    EVADE_ZIGZAG = "zigzag"       # 蛇形机动
    EVADE_ALTITUDE = "altitude"   # 高度变化
    EVADE_TERRAIN = "terrain"     # 地形掩护
    EVADE_SUPPORT = "support"     # 呼叫支援
    EVADE_SPLIT = "split"         # 蜂群分散


# ============ 数据结构 ============

@dataclass
class InterceptorUnit:
    """单个拦截器"""
    unit_id: str
    position: dict
    velocity: dict = field(default_factory=lambda: {"x": 0, "y": 0, "z": 0})

    # 能力参数
    max_speed: float = 90.0          # 拦截器通常比UAV快
    detection_range: float = 3500.0   # 探测范围
    weapon_range: float = 1500.0      # 武器射程
    damage_per_hit: float = 15.0      # 每次攻击伤害

    # 状态
    state: InterceptorState = InterceptorState.PATROL
    health: float = 80.0              # 拦截器血量（低于UAV）
    max_health: float = 80.0
    alive: bool = True

    # 巡逻
    patrol_center: dict = field(default_factory=lambda: {"x": 5000, "y": 5000, "z": 300})
    patrol_radius: float = 3000.0
    patrol_angle: float = 0.0         # 当前巡逻角度

    # 目标
    current_target: Optional[str] = None
    target_position: Optional[dict] = None
    target_locked_ticks: int = 0       # 持续锁定tick数

    # 协同
    wingman_id: Optional[str] = None   # 协同拦截的友军ID
    formation_offset: dict = field(default_factory=lambda: {"x": 200, "y": 0, "z": 0})

    # AI等级
    ai_level: str = "advanced"  # basic / advanced / llm

    # 统计
    targets_detected: int = 0
    targets_engaged: int = 0
    targets_destroyed: int = 0
    distance_flown: float = 0.0


@dataclass
class ThreatAssessment:
    """威胁评估结果"""
    uav_id: str
    threat_level: ThreatLevel
    nearest_interceptor_id: str
    nearest_distance: float
    interceptor_count_nearby: int     # 附近拦截器数量
    time_to_intercept: float          # 预估被拦截时间(tick)
    recommended_evasion: EvasionStrategy
    evasion_direction: dict           # 推荐规避方向
    urgency: str = "normal"           # low/normal/high/critical


# ============ 拦截蜂群管理器 ============

class InterceptorSwarm:
    """
    拦截蜂群管理器

    管理敌方拦截无人机集群的生成、行为和协同。
    """

    def __init__(
        self,
        unit_count: int = 4,
        difficulty: str = "normal",
        ai_level: str = "advanced",
        llm_client=None,
    ):
        self.units: dict[str, InterceptorUnit] = {}
        self.unit_count = unit_count
        self.difficulty = difficulty
        self.ai_level = ai_level
        self.llm_client = llm_client

        # 按难度配置参数
        self._diff_config = {
            "easy":   {"speed_mult": 0.7, "detection_mult": 0.7, "damage_mult": 0.33, "count": 2},
            "normal": {"speed_mult": 1.0, "detection_mult": 1.0, "damage_mult": 0.67, "count": 3},
            "hard":   {"speed_mult": 1.3, "detection_mult": 1.3, "damage_mult": 1.0,  "count": 5},
        }
        self._cfg = self._diff_config.get(difficulty, self._diff_config["normal"])
        self.unit_count = self._cfg["count"]

        # 统计
        self.total_damage_dealt = 0.0
        self.total_uavs_destroyed = 0

    # ============ 初始化 ============

    def initialize(self, spawn_zones: list[dict] = None):
        """初始化拦截器集群"""
        speed_mult = self._cfg["speed_mult"]
        detect_mult = self._cfg["detection_mult"]
        damage_mult = self._cfg["damage_mult"]

        if not spawn_zones:
            # 默认生成区域：地图四个象限的边界
            spawn_zones = [
                {"x": MAP_WIDTH * 0.15, "y": MAP_HEIGHT * 0.5, "z": 300},
                {"x": MAP_WIDTH * 0.85, "y": MAP_HEIGHT * 0.3, "z": 280},
                {"x": MAP_WIDTH * 0.5,  "y": MAP_HEIGHT * 0.85, "z": 320},
                {"x": MAP_WIDTH * 0.3,  "y": MAP_HEIGHT * 0.15, "z": 290},
                {"x": MAP_WIDTH * 0.7,  "y": MAP_HEIGHT * 0.7,  "z": 310},
                {"x": MAP_WIDTH * 0.4,  "y": MAP_HEIGHT * 0.6,  "z": 295},
            ]

        for i in range(self.unit_count):
            unit_id = f"INT_{i+1:02d}"
            zone = spawn_zones[i % len(spawn_zones)]

            position = {
                "x": zone["x"] + random.uniform(-500, 500),
                "y": zone["y"] + random.uniform(-500, 500),
                "z": zone.get("z", 300) + random.uniform(-50, 50),
            }

            self.units[unit_id] = InterceptorUnit(
                unit_id=unit_id,
                position=position,
                max_speed=UAV_MAX_SPEED * 1.15 * speed_mult,
                detection_range=UAV_DETECTION_RANGE * 1.2 * detect_mult,
                weapon_range=UAV_WEAPON_RANGE * 0.8,
                damage_per_hit=15.0 * damage_mult,
                patrol_center=dict(position),
                patrol_radius=2500.0 + random.uniform(-500, 1000),
                patrol_angle=random.uniform(0, 2 * math.pi),
                ai_level=self.ai_level,
            )

    # ============ 每tick更新 ============

    def update(self, tick: int, uavs: dict, env_targets: dict = None):
        """更新所有拦截器状态"""
        alive_uavs = {
            uid: u for uid, u in uavs.items()
            if hasattr(u, 'alive') and u.alive
        }

        for uid, unit in self.units.items():
            if not unit.alive:
                continue

            # 状态机更新
            if unit.state == InterceptorState.PATROL:
                self._update_patrol(unit, tick, alive_uavs)
            elif unit.state == InterceptorState.DETECTED:
                self._update_pursuit(unit, tick, alive_uavs)
            elif unit.state == InterceptorState.PURSUING:
                self._update_pursuit(unit, tick, alive_uavs)
            elif unit.state == InterceptorState.ENGAGING:
                self._update_engagement(unit, tick, alive_uavs)
            elif unit.state == InterceptorState.RETURNING:
                self._update_return(unit, tick, alive_uavs)

    # ============ 巡逻逻辑 ============

    def _update_patrol(self, unit: InterceptorUnit, tick: int, uavs: dict):
        """巡逻：沿圆形轨迹移动 + 扫描目标"""
        # 圆形巡逻轨迹
        unit.patrol_angle = (unit.patrol_angle + 0.03) % (2 * math.pi)
        target_x = unit.patrol_center["x"] + math.cos(unit.patrol_angle) * unit.patrol_radius
        target_y = unit.patrol_center["y"] + math.sin(unit.patrol_angle) * unit.patrol_radius

        # 移动到巡逻点
        dx = target_x - unit.position["x"]
        dy = target_y - unit.position["y"]
        dist = math.sqrt(dx * dx + dy * dy)

        if dist > 10:
            speed = unit.max_speed * 0.6  # 巡逻速度60%
            unit.velocity = {
                "x": (dx / dist) * speed,
                "y": (dy / dist) * speed,
                "z": 0,
            }
            unit.position["x"] += unit.velocity["x"]
            unit.position["y"] += unit.velocity["y"]
            unit.distance_flown += speed

        # 扫描附近UAV
        nearest_uav, nearest_dist = self._scan_for_targets(unit, uavs)

        if nearest_uav and nearest_dist < unit.detection_range:
            unit.state = InterceptorState.DETECTED
            unit.current_target = nearest_uav
            if nearest_uav in uavs:
                unit.target_position = dict(uavs[nearest_uav].position)
            unit.targets_detected += 1

    # ============ 追踪逻辑 ============

    def _update_pursuit(self, unit: InterceptorUnit, tick: int, uavs: dict):
        """追踪：锁定目标并高速接近"""
        target_id = unit.current_target

        # 验证目标是否仍然有效
        if not target_id or target_id not in uavs or not uavs[target_id].alive:
            unit.state = InterceptorState.RETURNING
            unit.current_target = None
            unit.target_position = None
            unit.target_locked_ticks = 0
            return

        target = uavs[target_id]
        unit.target_position = dict(target.position)
        unit.target_locked_ticks += 1

        # 计算距离
        dist = distance(unit.position, target.position)

        # 如果在武器射程内 → 切换为攻击
        if dist <= unit.weapon_range:
            unit.state = InterceptorState.ENGAGING
            return

        # 高速追踪
        dx = target.position["x"] - unit.position["x"]
        dy = target.position["y"] - unit.position["y"]
        dz = target.position.get("z", 200) - unit.position.get("z", 300)

        if dist < 1:
            return

        speed = unit.max_speed * 0.9  # 追踪速度90%
        unit.velocity = {
            "x": (dx / dist) * speed,
            "y": (dy / dist) * speed,
            "z": (dz / max(abs(dz), 1)) * speed * 0.3,
        }
        unit.position["x"] += unit.velocity["x"]
        unit.position["y"] += unit.velocity["y"]
        unit.position["z"] = unit.position.get("z", 300) + unit.velocity["z"]
        unit.distance_flown += speed

    # ============ 攻击逻辑 ============

    def _update_engagement(self, unit: InterceptorUnit, tick: int, uavs: dict):
        """攻击：对目标造成伤害"""
        target_id = unit.current_target

        if not target_id or target_id not in uavs or not uavs[target_id].alive:
            unit.state = InterceptorState.RETURNING
            unit.current_target = None
            unit.target_locked_ticks = 0
            return

        target = uavs[target_id]
        dist = distance(unit.position, target.position)

        # 如果目标逃出武器射程 → 回到追踪
        if dist > unit.weapon_range * 1.5:
            unit.state = InterceptorState.PURSUING
            return

        # 造成伤害
        damage = unit.damage_per_hit * (0.8 + random.uniform(0, 0.4))  # 伤害浮动
        target.health -= damage
        self.total_damage_dealt += damage
        unit.targets_engaged += 1

        # 检查是否摧毁目标
        if target.health <= 0:
            if target.alive:
                target.alive = False
                from core.uav_agent import UAVState
                target.state = UAVState.DESTROYED
                unit.targets_destroyed += 1
                self.total_uavs_destroyed += 1
            unit.state = InterceptorState.PATROL
            unit.current_target = None

        # 继续贴近目标
        if dist > 50:
            dx = target.position["x"] - unit.position["x"]
            dy = target.position["y"] - unit.position["y"]
            speed = unit.max_speed * 0.8
            unit.velocity = {
                "x": (dx / dist) * speed,
                "y": (dy / dist) * speed,
                "z": 0,
            }
            unit.position["x"] += unit.velocity["x"]
            unit.position["y"] += unit.velocity["y"]

    # ============ 返回逻辑 ============

    def _update_return(self, unit: InterceptorUnit, tick: int, uavs: dict):
        """返回巡逻区域"""
        dist_to_patrol = distance(unit.position, unit.patrol_center)

        if dist_to_patrol < 100:
            unit.state = InterceptorState.PATROL
            unit.velocity = {"x": 0, "y": 0, "z": 0}
            return

        dx = unit.patrol_center["x"] - unit.position["x"]
        dy = unit.patrol_center["y"] - unit.position["y"]
        speed = unit.max_speed * 0.5
        unit.velocity = {
            "x": (dx / dist_to_patrol) * speed,
            "y": (dy / dist_to_patrol) * speed,
            "z": 0,
        }
        unit.position["x"] += unit.velocity["x"]
        unit.position["y"] += unit.velocity["y"]

        # 途中发现新目标
        nearest_uav, nearest_dist = self._scan_for_targets(unit, uavs)
        if nearest_uav and nearest_dist < unit.detection_range:
            unit.state = InterceptorState.DETECTED
            unit.current_target = nearest_uav

    # ============ 辅助方法 ============

    def _scan_for_targets(self, unit: InterceptorUnit, uavs: dict) -> tuple:
        """扫描附近UAV，返回(最近UAV_ID, 距离)"""
        nearest_id = None
        nearest_dist = float("inf")

        for uid, uav in uavs.items():
            if not uav.alive:
                continue
            # 不追踪已处于撤退/摧毁状态的UAV
            state_name = uav.state.name if hasattr(uav.state, 'name') else str(uav.state)
            if state_name in ("RETREATING", "DESTROYED"):
                continue

            dist = distance(unit.position, uav.position)
            if dist < nearest_dist:
                nearest_dist = dist
                nearest_id = uid

        return nearest_id, nearest_dist

    # ============ 威胁评估 ============

    def assess_threat(self, uav_position: dict, uav_id: str = None) -> ThreatAssessment:
        """评估指定位置的威胁等级"""
        nearest_id = None
        nearest_dist = float("inf")
        nearby_count = 0

        for uid, unit in self.units.items():
            if not unit.alive:
                continue
            dist = distance(uav_position, unit.position)
            if dist < nearest_dist:
                nearest_dist = dist
                nearest_id = uid
            if dist < 3000:
                nearby_count += 1

        # 威胁等级判定
        if nearest_dist > 5000 or nearest_id is None:
            threat = ThreatLevel.NONE
            urgency = "low"
        elif nearest_dist > 3000:
            threat = ThreatLevel.LOW
            urgency = "low"
        elif nearest_dist > 1000:
            threat = ThreatLevel.MEDIUM
            urgency = "normal"
        elif nearest_dist > 500:
            threat = ThreatLevel.HIGH
            urgency = "high"
        else:
            threat = ThreatLevel.CRITICAL
            urgency = "critical"

        # 计算拦截时间
        if nearest_id and nearest_dist < 5000:
            interceptor = self.units[nearest_id]
            speed_diff = interceptor.max_speed - UAV_MAX_SPEED
            if speed_diff > 0:
                time_to_intercept = nearest_dist / speed_diff
            else:
                time_to_intercept = nearest_dist / 10  # 最小逼近速度
        else:
            time_to_intercept = float("inf")

        # 推荐规避策略
        evasion, direction = self._recommend_evasion(
            threat, nearest_dist, nearby_count, uav_position, uav_id
        )

        return ThreatAssessment(
            uav_id=uav_id or "unknown",
            threat_level=threat,
            nearest_interceptor_id=nearest_id or "",
            nearest_distance=round(nearest_dist, 1),
            interceptor_count_nearby=nearby_count,
            time_to_intercept=round(time_to_intercept, 1),
            recommended_evasion=evasion,
            evasion_direction=direction,
            urgency=urgency,
        )

    def _recommend_evasion(
        self, threat: ThreatLevel, dist: float, count: int,
        uav_pos: dict, uav_id: str = None
    ) -> tuple:
        """推荐规避策略和方向"""
        # 计算远离最近拦截器的方向
        direction = {"x": 0, "y": 0, "z": 200}

        if self.units:
            nearest = min(
                (u for u in self.units.values() if u.alive),
                key=lambda u: distance(uav_pos, u.position),
                default=None,
            )
            if nearest:
                dx = uav_pos["x"] - nearest.position["x"]
                dy = uav_pos["y"] - nearest.position["y"]
                mag = math.sqrt(dx * dx + dy * dy) or 1
                direction = {
                    "x": uav_pos["x"] + (dx / mag) * 2000,
                    "y": uav_pos["y"] + (dy / mag) * 2000,
                    "z": uav_pos.get("z", 200) + 150,  # 向上规避
                }

        # 策略选择
        if threat in (ThreatLevel.NONE, ThreatLevel.LOW):
            return EvasionStrategy.NONE, direction

        if threat == ThreatLevel.MEDIUM:
            if count > 1:
                return EvasionStrategy.EVADE_ALTITUDE, direction
            return EvasionStrategy.EVADE_SPEED, direction

        if threat == ThreatLevel.HIGH:
            if count >= 3:
                return EvasionStrategy.EVADE_SPLIT, direction
            elif count >= 2:
                return EvasionStrategy.EVADE_ZIGZAG, direction
            return EvasionStrategy.EVADE_SPEED, direction

        # CRITICAL
        if count >= 3:
            return EvasionStrategy.EVADE_SPLIT, direction
        return EvasionStrategy.EVADE_ZIGZAG, direction

    # ============ 统计 ============

    def get_state(self) -> dict:
        """获取拦截蜂群状态"""
        return {
            "units": {
                uid: {
                    "id": u.unit_id,
                    "position": dict(u.position),
                    "velocity": dict(u.velocity),
                    "state": u.state.name,
                    "health": round(u.health, 1),
                    "alive": u.alive,
                    "current_target": u.current_target,
                    "max_speed": round(u.max_speed, 1),
                    "detection_range": round(u.detection_range, 1),
                    "weapon_range": round(u.weapon_range, 1),
                    "damage_per_hit": round(u.damage_per_hit, 1),
                    "targets_destroyed": u.targets_destroyed,
                    "ai_level": u.ai_level,
                }
                for uid, u in self.units.items()
            },
            "summary": {
                "total_units": len(self.units),
                "alive_units": sum(1 for u in self.units.values() if u.alive),
                "patrol_count": sum(1 for u in self.units.values() if u.state == InterceptorState.PATROL),
                "pursuing_count": sum(1 for u in self.units.values() if u.state in (InterceptorState.PURSUING, InterceptorState.DETECTED)),
                "engaging_count": sum(1 for u in self.units.values() if u.state == InterceptorState.ENGAGING),
                "total_damage_dealt": round(self.total_damage_dealt, 1),
                "total_uavs_destroyed": self.total_uavs_destroyed,
            },
            "difficulty": self.difficulty,
            "ai_level": self.ai_level,
        }


# ============ 规避执行器 ============

class EvasionExecutor:
    """
    规避执行器

    根据 ThreatAssessment 生成具体的规避机动指令，
    供 UAVAgent 的 EVADING 状态使用。
    """

    def __init__(self):
        self._zigzag_phase = {}     # uav_id -> phase_angle
        self._evasion_cooldown = {}  # uav_id -> tick

    def execute_evasion(
        self, uav, threat: ThreatAssessment, tick: int
    ) -> dict:
        """
        执行规避机动，返回新的 velocity 和 target_pos

        Returns:
            {"velocity": {x,y,z}, "target_pos": {x,y,z}, "strategy": str}
        """
        strategy = threat.recommended_evasion
        evasion_dir = threat.evasion_direction

        if strategy == EvasionStrategy.NONE:
            return {"velocity": {"x": 0, "y": 0, "z": 0}, "strategy": "none"}

        uav_id = getattr(uav, 'id', 'unknown')
        speed = getattr(uav, 'max_speed', UAV_MAX_SPEED)

        if strategy == EvasionStrategy.EVADE_SPEED:
            # 全速直线逃离
            dx = evasion_dir["x"] - uav.position["x"]
            dy = evasion_dir["y"] - uav.position["y"]
            dist = math.sqrt(dx * dx + dy * dy) or 1
            return {
                "velocity": {
                    "x": (dx / dist) * speed * 1.1,
                    "y": (dy / dist) * speed * 1.1,
                    "z": 0,
                },
                "target_pos": dict(evasion_dir),
                "strategy": "speed",
            }

        elif strategy == EvasionStrategy.EVADE_ZIGZAG:
            # 蛇形机动：在逃离方向上叠加正弦波动
            phase = self._zigzag_phase.get(uav_id, random.uniform(0, 2 * math.pi))
            self._zigzag_phase[uav_id] = phase + 0.3

            dx = evasion_dir["x"] - uav.position["x"]
            dy = evasion_dir["y"] - uav.position["y"]
            dist = math.sqrt(dx * dx + dy * dy) or 1

            # 基础方向 + 侧向摆动
            base_vx = (dx / dist) * speed * 0.9
            base_vy = (dy / dist) * speed * 0.9

            # 添加垂直于运动方向的摆动
            perp_x = -dy / dist
            perp_y = dx / dist
            zigzag = math.sin(phase) * speed * 0.4

            return {
                "velocity": {
                    "x": base_vx + perp_x * zigzag,
                    "y": base_vy + perp_y * zigzag,
                    "z": math.cos(phase * 2) * speed * 0.2,
                },
                "target_pos": dict(evasion_dir),
                "strategy": "zigzag",
            }

        elif strategy == EvasionStrategy.EVADE_ALTITUDE:
            # 高度变化：急剧爬升或俯冲
            alt_change = speed * 0.5  # 快速改变高度
            current_z = uav.position.get("z", 200)

            # 爬升（通常向上规避更安全）
            target_z = min(500, current_z + 200)

            dx = evasion_dir["x"] - uav.position["x"]
            dy = evasion_dir["y"] - uav.position["y"]
            dist = math.sqrt(dx * dx + dy * dy) or 1

            return {
                "velocity": {
                    "x": (dx / dist) * speed * 0.7,
                    "y": (dy / dist) * speed * 0.7,
                    "z": alt_change,
                },
                "target_pos": {
                    "x": evasion_dir["x"],
                    "y": evasion_dir["y"],
                    "z": target_z,
                },
                "strategy": "altitude",
            }

        elif strategy == EvasionStrategy.EVADE_TERRAIN:
            # 地形掩护：飞向山地/城市区域
            terrain_pos = self._find_nearest_terrain_cover(uav.position)
            dx = terrain_pos["x"] - uav.position["x"]
            dy = terrain_pos["y"] - uav.position["y"]
            dist = math.sqrt(dx * dx + dy * dy) or 1
            return {
                "velocity": {
                    "x": (dx / dist) * speed,
                    "y": (dy / dist) * speed,
                    "z": 0,
                },
                "target_pos": dict(terrain_pos),
                "strategy": "terrain",
            }

        elif strategy == EvasionStrategy.EVADE_SPLIT:
            # 蜂群分散：不同UAV向不同方向散开
            scatter_angle = random.uniform(0, 2 * math.pi)
            scatter_dist = 3000
            return {
                "velocity": {
                    "x": math.cos(scatter_angle) * speed,
                    "y": math.sin(scatter_angle) * speed,
                    "z": random.uniform(-speed * 0.2, speed * 0.3),
                },
                "target_pos": {
                    "x": uav.position["x"] + math.cos(scatter_angle) * scatter_dist,
                    "y": uav.position["y"] + math.sin(scatter_angle) * scatter_dist,
                    "z": uav.position.get("z", 200) + random.uniform(50, 200),
                },
                "strategy": "split",
            }

        return {"velocity": {"x": 0, "y": 0, "z": 0}, "strategy": "none"}

    def _find_nearest_terrain_cover(self, position: dict) -> dict:
        """找到最近的地形掩护点（模拟）"""
        # 简化为地图四角的"安全区"
        covers = [
            {"x": MAP_WIDTH * 0.1, "y": MAP_HEIGHT * 0.1, "z": 250},
            {"x": MAP_WIDTH * 0.1, "y": MAP_HEIGHT * 0.9, "z": 250},
            {"x": MAP_WIDTH * 0.9, "y": MAP_HEIGHT * 0.1, "z": 250},
            {"x": MAP_WIDTH * 0.9, "y": MAP_HEIGHT * 0.9, "z": 250},
        ]
        return min(covers, key=lambda c: distance(position, c))
