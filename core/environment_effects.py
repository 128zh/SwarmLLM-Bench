"""
动态环境引擎模块
================
模拟真实飞行环境中的动态条件：
- 风力系统：0-9级风，影响UAV移动
- 降水系统：雨/雪，降低传感器探测距离
- 光照系统：白天/黄昏/夜间，影响视觉渲染
- 地形系统：山地/城市/水域/混合，影响GPS信号和飞行安全
"""
import math
import random
from dataclasses import dataclass, field
from typing import Optional

from config import (
    MAP_WIDTH, MAP_HEIGHT,
    ENV_WIND_LEVEL, ENV_PRECIPITATION, ENV_LIGHTING, ENV_TERRAIN_TYPE,
    ENV_WIND_SPEED_MAP, ENV_PRECIP_REDUCTION,
)


@dataclass
class EnvironmentState:
    """环境状态快照"""
    wind_level: int = 0                  # 0-9
    wind_speed: float = 0.0              # m/s
    wind_direction: float = 0.0          # 风向(度)
    precipitation: str = "none"          # none / rain / snow
    precip_intensity: float = 0.0        # 0.0-1.0
    lighting: str = "day"                # day / dusk / night
    ambient_light: float = 1.0           # 0.0-1.0 环境光强度
    terrain_type: str = "mixed"          # mixed / mountain / urban / water
    terrain_grid: list = field(default_factory=list)  # 地形网格摘要(前端渲染用)
    tick: int = 0


class EnvironmentEngine:
    """
    动态环境引擎

    每个仿真tick更新环境状态，包括：
    1. 风力矢量：随机波动 + 缓慢变化
    2. 降水强度：随机变化
    3. 光照角度：随时间变化
    4. 地形影响：GPS信号衰减、碰撞检测

    集成方式：
        engine = EnvironmentEngine()
        engine.update(tick)
        wind_drift = engine.get_wind_drift()
        detection_mod = engine.get_detection_modifier()
    """

    def __init__(self, config: dict = None):
        cfg = config or {}
        self.wind_level = cfg.get("wind_level", ENV_WIND_LEVEL)
        self.precipitation = cfg.get("precipitation", ENV_PRECIPITATION)
        self.lighting = cfg.get("lighting", ENV_LIGHTING)
        self.terrain_type = cfg.get("terrain_type", ENV_TERRAIN_TYPE)
        self.wind_effect_enabled = cfg.get("wind_effect_on_uav", True)

        # 风力状态
        self._wind_speed = ENV_WIND_SPEED_MAP[min(self.wind_level, 9)]
        self._wind_direction = random.uniform(0, 360)  # 初始随机风向
        self._wind_gust = 0.0  # 阵风叠加

        # 降水状态
        self._precip_intensity = 0.0
        if self.precipitation != "none":
            self._precip_intensity = random.uniform(0.3, 0.8)

        # 光照状态
        self._ambient_light = 1.0
        self._update_lighting()

        # 地形网格（低分辨率摘要，供前端渲染）
        self._terrain_grid = self._generate_terrain_grid()

        # 统计
        self.tick = 0

    def update(self, tick: int, dt: float = 1.0):
        """每tick更新环境状态"""
        self.tick = tick

        # 风力缓慢变化 + 随机阵风
        if self.wind_level > 0:
            base_speed = ENV_WIND_SPEED_MAP[min(self.wind_level, 9)]
            # 风向缓慢旋转
            self._wind_direction = (self._wind_direction + random.uniform(-3, 3)) % 360
            # 阵风随机叠加
            gust_target = random.uniform(-0.3, 0.3) * base_speed
            self._wind_gust += (gust_target - self._wind_gust) * 0.3
            self._wind_speed = base_speed + self._wind_gust
            self._wind_speed = max(0, self._wind_speed)
        else:
            self._wind_speed = 0.0
            self._wind_gust = 0.0

        # 降水强度随机波动
        if self.precipitation != "none":
            target = random.uniform(0.3, 1.0)
            self._precip_intensity += (target - self._precip_intensity) * 0.1

        # 光照渐变
        self._update_lighting()

    def _update_lighting(self):
        """更新光照状态"""
        if self.lighting == "day":
            self._ambient_light = 1.0
        elif self.lighting == "dusk":
            self._ambient_light = 0.45
        elif self.lighting == "night":
            self._ambient_light = 0.15
        else:
            self._ambient_light = 1.0

    def _generate_terrain_grid(self, resolution: int = 20) -> list:
        """
        生成地形网格摘要

        Returns:
            2D list of terrain cells [{x, y, type, elevation, gps_quality}]
            分辨率为 resolution x resolution
        """
        cell_w = MAP_WIDTH / resolution
        cell_h = MAP_HEIGHT / resolution
        grid = []

        for iy in range(resolution):
            row = []
            for ix in range(resolution):
                cx = (ix + 0.5) * cell_w
                cy = (iy + 0.5) * cell_h
                cell = self._get_terrain_at(cx, cy)
                row.append(cell)
            grid.append(row)

        return grid

    def _get_terrain_at(self, x: float, y: float) -> dict:
        """
        获取指定位置的局部地形信息

        使用程序化地形生成（确定性伪随机），避免存储100x100网格。
        """
        # 基于坐标的确定性哈希
        import hashlib
        key = f"{int(x//500)*500}_{int(y//500)*500}_{self.terrain_type}"
        seed = int(hashlib.md5(key.encode()).hexdigest()[:8], 16) / 0xffffffff
        rng = random.Random(seed)

        if self.terrain_type == "mixed":
            # 地图四象限不同地形
            if x < MAP_WIDTH * 0.5 and y < MAP_HEIGHT * 0.5:
                t = "mountain"
            elif x >= MAP_WIDTH * 0.5 and y < MAP_HEIGHT * 0.5:
                t = "urban"
            elif x < MAP_WIDTH * 0.5 and y >= MAP_HEIGHT * 0.5:
                t = "water"
            else:
                t = "plains"
        else:
            t = self.terrain_type

        elevation = 0.0
        gps_quality = 100.0
        color = "#2d4a1e"

        if t == "mountain":
            elevation = 200 + rng.uniform(0, 300)
            gps_quality = rng.uniform(60, 90)
            color = "#4a6741"
        elif t == "urban":
            elevation = 10 + rng.uniform(0, 50)
            gps_quality = rng.uniform(70, 95)
            color = "#5a5a5a"
        elif t == "water":
            elevation = 0
            gps_quality = rng.uniform(80, 100)
            color = "#1a3a5c"
        elif t == "plains":
            elevation = rng.uniform(0, 30)
            gps_quality = rng.uniform(90, 100)
            color = "#3a5a2e"
        else:
            elevation = rng.uniform(0, 50)
            color = "#4a5a3e"

        return {
            "x": x,
            "y": y,
            "terrain": t,
            "elevation": elevation,
            "gps_quality": gps_quality,
            "color": color,
        }

    def get_wind_drift(self) -> dict:
        """
        获取当前tick的风力偏移矢量（用于UAV移动修正）

        Returns:
            {"dx": float, "dy": float}  每tick的风力偏移量(米)
        """
        if self._wind_speed <= 0 or not self.wind_effect_enabled:
            return {"dx": 0.0, "dy": 0.0}

        wind_rad = math.radians(self._wind_direction)
        # 风力偏移量与风速成正比（仿真tick约等于1秒）
        drift_factor = 0.5  # 风力影响系数（调低以保持可控）
        return {
            "dx": math.cos(wind_rad) * self._wind_speed * drift_factor,
            "dy": math.sin(wind_rad) * self._wind_speed * drift_factor,
        }

    def get_detection_modifier(self) -> float:
        """获取当前探测距离修正系数"""
        modifier = ENV_PRECIP_REDUCTION.get(self.precipitation, 1.0)
        # 夜间进一步降低探测距离
        if self.lighting == "night":
            modifier *= 0.6
        elif self.lighting == "dusk":
            modifier *= 0.8
        return modifier

    def get_gps_quality_at(self, x: float, y: float) -> float:
        """获取指定位置的GPS信号质量(0-100)"""
        terrain = self._get_terrain_at(x, y)
        return terrain.get("gps_quality", 100.0)

    def set_wind(self, level: int):
        """设置风力等级"""
        self.wind_level = max(0, min(9, level))

    def set_precipitation(self, precip_type: str):
        """设置降水类型"""
        if precip_type in ("none", "rain", "snow"):
            self.precipitation = precip_type
            if precip_type == "none":
                self._precip_intensity = 0.0
            else:
                self._precip_intensity = random.uniform(0.3, 0.8)

    def set_lighting(self, lighting: str):
        """设置光照条件"""
        if lighting in ("day", "dusk", "night"):
            self.lighting = lighting
            self._update_lighting()

    def set_terrain(self, terrain_type: str):
        """设置地形类型"""
        if terrain_type in ("mixed", "mountain", "urban", "water"):
            self.terrain_type = terrain_type
            self._terrain_grid = self._generate_terrain_grid()

    def get_state(self) -> dict:
        """获取环境状态快照（供前端渲染）"""
        return {
            "wind": {
                "level": self.wind_level,
                "speed": round(self._wind_speed, 1),
                "direction": round(self._wind_direction, 1),
                "gust": round(self._wind_gust, 1),
            },
            "precipitation": {
                "type": self.precipitation,
                "intensity": round(self._precip_intensity, 2),
            },
            "lighting": {
                "type": self.lighting,
                "ambient": round(self._ambient_light, 2),
            },
            "terrain": {
                "type": self.terrain_type,
                "grid": self._terrain_grid,
            },
            "detection_modifier": round(self.get_detection_modifier(), 2),
            "tick": self.tick,
        }
