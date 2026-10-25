"""
对抗训练与 Self-Play 模块
==========================

实现红蓝双方的自我博弈训练，以持续提升蜂群战术水平。

模式:
1. Self-Play: 蓝方(LLM) vs 红方(LLM) 对抗，双方策略共同进化
2. Curriculum: 难度梯度递增的训练课程
3. Population-Based: 多种策略的种群训练

架构:
┌──────────────────────────────────────────────────┐
│              AdversarialTrainer                   │
│                                                  │
│  ┌──────────┐    对抗     ┌──────────┐          │
│  │ Blue Team │ ◄────────► │ Red Team  │          │
│  │ LLM增强   │   仿真     │ LLM增强   │          │
│  └─────┬────┘            └─────┬─────┘          │
│        │                       │                 │
│        ▼                       ▼                 │
│  ┌──────────┐           ┌──────────┐           │
│  │ 策略评估  │           │ 策略进化  │           │
│  │ Win/Loss │           │ 参数更新  │           │
│  └──────────┘           └──────────┘           │
│                                                  │
│  ┌──────────────────────────────────┐           │
│  │  训练日志 + ELO评分 + 策略库      │           │
│  └──────────────────────────────────┘           │
└──────────────────────────────────────────────────┘
"""
import time
import random
from dataclasses import dataclass, field
from typing import Optional
from collections import defaultdict

from config import MAP_WIDTH, MAP_HEIGHT


# ============ 数据结构 ============

@dataclass
class MatchResult:
    """单场对抗结果"""
    match_id: str
    blue_strategy: str
    red_strategy: str
    winner: str              # blue / red / draw
    blue_score: float        # 蓝方得分
    red_score: float         # 红方得分
    total_ticks: int
    blue_uavs_lost: int
    red_assets_destroyed: int
    blue_llm_calls: int = 0
    red_llm_calls: int = 0
    key_events: list = field(default_factory=list)
    timestamp: float = 0.0


@dataclass
class Strategy:
    """训练策略"""
    name: str
    params: dict
    elo_rating: float = 1000.0
    matches_played: int = 0
    wins: int = 0
    losses: int = 0
    avg_score: float = 0.0

    @property
    def win_rate(self) -> float:
        if self.matches_played == 0:
            return 0.0
        return self.wins / self.matches_played


@dataclass
class CurriculumStage:
    """课程阶段"""
    level: int
    name: str
    description: str
    blue_uavs: int
    target_count: int
    wind_level: int
    precipitation: str
    lighting: str
    red_difficulty: str
    max_ticks: int
    pass_criteria: dict  # 通过条件


class AdversarialTrainer:
    """
    对抗训练器

    管理红蓝双方的 Self-Play 训练流程。
    """

    def __init__(self, simulation_factory, llm_client=None):
        """
        Args:
            simulation_factory: 创建仿真引擎的工厂函数
            llm_client: LLM客户端（可选）
        """
        self.sim_factory = simulation_factory
        self.llm_client = llm_client

        # 策略库
        self.blue_strategies: dict[str, Strategy] = {}
        self.red_strategies: dict[str, Strategy] = {}

        # 对战记录
        self.match_history: list[MatchResult] = []
        self._match_counter = 0

        # 课程
        self.curriculum = self._build_default_curriculum()
        self.current_stage_index = 0

        # 统计
        self.training_stats = {
            "total_matches": 0,
            "blue_wins": 0,
            "red_wins": 0,
            "draws": 0,
            "total_training_time_s": 0.0,
        }

    # ============ 默认课程 ============

    def _build_default_curriculum(self) -> list[CurriculumStage]:
        """构建默认训练课程（难度递增）"""
        return [
            CurriculumStage(
                level=1, name="基础侦察",
                description="1目标，晴朗天气，4UAV",
                blue_uavs=4, target_count=1,
                wind_level=1, precipitation="none", lighting="day",
                red_difficulty="easy", max_ticks=500,
                pass_criteria={"success_rate": 0.8},
            ),
            CurriculumStage(
                level=2, name="标准打击",
                description="2目标，轻风，6UAV",
                blue_uavs=6, target_count=2,
                wind_level=2, precipitation="none", lighting="day",
                red_difficulty="normal", max_ticks=500,
                pass_criteria={"success_rate": 0.7},
            ),
            CurriculumStage(
                level=3, name="恶劣天气",
                description="2目标，风力5级+雨，8UAV",
                blue_uavs=8, target_count=2,
                wind_level=5, precipitation="rain", lighting="day",
                red_difficulty="normal", max_ticks=600,
                pass_criteria={"success_rate": 0.65},
            ),
            CurriculumStage(
                level=4, name="夜间作战",
                description="3目标，夜间，8UAV",
                blue_uavs=8, target_count=3,
                wind_level=3, precipitation="none", lighting="night",
                red_difficulty="hard", max_ticks=600,
                pass_criteria={"success_rate": 0.6},
            ),
            CurriculumStage(
                level=5, name="蜂群突防",
                description="4目标，强风+雪，12UAV",
                blue_uavs=12, target_count=4,
                wind_level=7, precipitation="snow", lighting="dusk",
                red_difficulty="hard", max_ticks=800,
                pass_criteria={"success_rate": 0.5},
            ),
        ]

    # ============ Self-Play 对战 ============

    async def run_self_play_match(
        self, stage: CurriculumStage, match_name: str = None,
        blue_strategy_name: str = "default",
        red_strategy_name: str = "default",
    ) -> MatchResult:
        """
        运行一场 Self-Play 对抗

        Returns:
            MatchResult 包含详细的对抗结果
        """
        self._match_counter += 1
        match_id = match_name or f"SP_{self._match_counter:04d}"
        start_time = time.time()

        try:
            # 创建仿真引擎
            engine = self.sim_factory(
                uav_count=stage.blue_uavs,
                target_count=stage.target_count,
            )
            engine.initialize()

            # 设置环境
            if engine.environment:
                engine.environment.set_wind(stage.wind_level)
                engine.environment.set_precipitation(stage.precipitation)
                engine.environment.set_lighting(stage.lighting)

            # 运行仿真
            tick = 0
            engine.running = True
            engine.start_time = time.time()

            while engine.running and tick < stage.max_ticks:
                await engine.step()
                tick = engine.tick

                if engine.mission_coord.mission_status == "completed":
                    engine.running = False
                    break

            # 收集结果
            state = engine.get_state()
            summary = state.get("summary", {})

            blue_score = self._calculate_blue_score(state, stage)
            red_score = self._calculate_red_score(state)

            # 判定胜负
            if state.get("mission", {}).get("mission_status") == "completed":
                winner = "blue"
            elif summary.get("alive_uavs", 0) == 0:
                winner = "red"
            else:
                winner = "blue" if blue_score > red_score else "red" if red_score > blue_score else "draw"

            result = MatchResult(
                match_id=match_id,
                blue_strategy=blue_strategy_name,
                red_strategy=red_strategy_name,
                winner=winner,
                blue_score=round(blue_score, 1),
                red_score=round(red_score, 1),
                total_ticks=tick,
                blue_uavs_lost=summary.get("destroyed_uavs", 0),
                red_assets_destroyed=summary.get("destroyed_targets", 0),
                timestamp=time.time(),
            )

            engine.shutdown()

        except Exception as e:
            result = MatchResult(
                match_id=match_id,
                blue_strategy=blue_strategy_name,
                red_strategy=red_strategy_name,
                winner="draw",
                blue_score=0,
                red_score=0,
                total_ticks=0,
                blue_uavs_lost=0,
                red_assets_destroyed=0,
                key_events=[f"ERROR: {str(e)}"],
            )

        # 更新统计
        self.match_history.append(result)
        self.training_stats["total_matches"] += 1
        if result.winner == "blue":
            self.training_stats["blue_wins"] += 1
        elif result.winner == "red":
            self.training_stats["red_wins"] += 1
        else:
            self.training_stats["draws"] += 1
        self.training_stats["total_training_time_s"] += time.time() - start_time

        # 更新 ELO
        self._update_elo(blue_strategy_name, red_strategy_name, result.winner)

        return result

    # ============ 评分函数 ============

    def _calculate_blue_score(self, state: dict, stage: CurriculumStage) -> float:
        """计算蓝方得分"""
        summary = state.get("summary", {})

        # 目标摧毁得分 (主要)
        target_score = (summary.get("destroyed_targets", 0) /
                        max(summary.get("total_targets", 1), 1)) * 100

        # UAV保存得分
        uav_score = (summary.get("alive_uavs", 0) /
                     max(summary.get("total_uavs", 1), 1)) * 50

        # 时间效率得分
        time_score = max(0, 30 - state.get("tick", 500) / 20)

        # 通信效率得分
        comm = state.get("communication", {})
        dropped = comm.get("total_dropped", 0)
        sent = max(comm.get("total_sent", 1), 1)
        comm_score = max(0, 20 * (1 - dropped / sent))

        return target_score + uav_score + time_score + comm_score

    def _calculate_red_score(self, state: dict) -> float:
        """计算红方得分"""
        summary = state.get("summary", {})

        # 摧毁UAV得分
        uav_kill_score = (summary.get("destroyed_uavs", 0) /
                         max(summary.get("total_uavs", 1), 1)) * 100

        # 资产保存得分
        targets_destroyed = summary.get("destroyed_targets", 0)
        total_targets = max(summary.get("total_targets", 1), 1)
        asset_score = (1 - targets_destroyed / total_targets) * 80

        # 拖延时间得分
        time_score = min(40, state.get("tick", 0) / 10)

        return uav_kill_score + asset_score + time_score

    # ============ ELO 评分 ============

    def _update_elo(self, blue_name: str, red_name: str, winner: str):
        """更新 ELO 评分"""
        K = 32  # ELO K-factor

        blue = self.blue_strategies.get(blue_name)
        red = self.red_strategies.get(red_name)

        if not blue:
            blue = Strategy(name=blue_name, params={})
            self.blue_strategies[blue_name] = blue
        if not red:
            red = Strategy(name=red_name, params={})
            self.red_strategies[red_name] = red

        # 预期胜率
        expected_blue = 1 / (1 + 10 ** ((red.elo_rating - blue.elo_rating) / 400))

        # 实际结果
        if winner == "blue":
            actual_blue = 1.0
            blue.wins += 1
            red.losses += 1
        elif winner == "red":
            actual_blue = 0.0
            red.wins += 1
            blue.losses += 1
        else:
            actual_blue = 0.5

        # ELO更新
        blue.elo_rating += K * (actual_blue - expected_blue)
        red.elo_rating += K * ((1 - actual_blue) - (1 - expected_blue))

        blue.matches_played += 1
        red.matches_played += 1

    # ============ 课程训练 ============

    async def run_curriculum(
        self, matches_per_stage: int = 10,
        progress_callback=None,
    ) -> dict:
        """
        运行完整训练课程

        Args:
            matches_per_stage: 每阶段的对抗场次
            progress_callback: 进度回调 fn(stage, match_idx, result)

        Returns:
            训练报告
        """
        report = {"stages": [], "overall": {}}

        for stage_idx, stage in enumerate(self.curriculum):
            self.current_stage_index = stage_idx

            stage_results = []
            stage_wins = 0

            for i in range(matches_per_stage):
                result = await self.run_self_play_match(
                    stage,
                    match_name=f"S{stage.level}_M{i+1:02d}",
                )
                stage_results.append(result)

                if result.winner == "blue":
                    stage_wins += 1

                if progress_callback:
                    progress_callback(stage, i, result)

            success_rate = stage_wins / matches_per_stage
            passed = success_rate >= stage.pass_criteria["success_rate"]

            report["stages"].append({
                "level": stage.level,
                "name": stage.name,
                "success_rate": round(success_rate, 3),
                "passed": passed,
                "blue_avg_score": sum(r.blue_score for r in stage_results) / len(stage_results),
                "red_avg_score": sum(r.red_score for r in stage_results) / len(stage_results),
            })

            # 如果未通过，可以在本阶段重复训练（简化：直接继续）
            if not passed:
                # 可以在此添加重复训练逻辑
                pass

        report["overall"] = {
            "stages_completed": sum(1 for s in report["stages"] if s["passed"]),
            "total_stages": len(self.curriculum),
            "total_matches": self.training_stats["total_matches"],
            "blue_win_rate": (
                self.training_stats["blue_wins"] /
                max(self.training_stats["total_matches"], 1)
            ),
            "total_time_s": self.training_stats["total_training_time_s"],
            "elo_ratings": {
                name: round(s.elo_rating, 1)
                for name, s in self.blue_strategies.items()
            },
        }

        return report

    # ============ 统计 ============

    def get_training_stats(self) -> dict:
        return {
            **self.training_stats,
            "current_stage": self.current_stage_index + 1,
            "total_stages": len(self.curriculum),
            "blue_strategies": len(self.blue_strategies),
            "red_strategies": len(self.red_strategies),
            "recent_results": [
                {
                    "match_id": r.match_id,
                    "winner": r.winner,
                    "blue_score": r.blue_score,
                    "red_score": r.red_score,
                    "ticks": r.total_ticks,
                }
                for r in self.match_history[-10:]
            ],
        }
