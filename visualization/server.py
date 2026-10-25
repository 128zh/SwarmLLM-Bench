"""
Web可视化服务器
================
基于aiohttp的异步Web服务器，提供：
- 静态文件服务（HTML/CSS/JS仪表盘）
- WebSocket实时推送仿真状态
- REST API控制仿真（启动/暂停/故障注入/手动摧毁）
"""
import asyncio
import json
import os
from aiohttp import web, WSMsgType

from config import WEB_HOST, WEB_PORT, WS_BROADCAST_INTERVAL
from core.simulation import SimulationEngine
from core.llm_client import LLMClient, get_llm_client
from core.export_results import TrainingReportExporter
from core.llm_agent import DeepAgent
from core.nl_command import NLCommandInterface
from core.red_force_agent import RedForceAgent


class VisualizationServer:
    """Web可视化服务器"""

    def __init__(self, engine: SimulationEngine, llm_enabled: bool = False):
        self.engine = engine
        self.app = web.Application()
        self.ws_clients: set = set()

        # LLM 集成
        self.llm_enabled = llm_enabled
        self.llm_client: Optional[LLMClient] = None
        self.nl_interface: Optional[NLCommandInterface] = None
        self.red_force: Optional[RedForceAgent] = None

        if llm_enabled:
            try:
                self.llm_client = get_llm_client()
                self.nl_interface = NLCommandInterface(self.llm_client)
                self.red_force = RedForceAgent(
                    self.llm_client, difficulty="normal", use_llm=True
                )
                print(f"[LLM] DeepAgent infrastructure enabled")
                print(f"[LLM]   Provider: {self.llm_client.provider}")
                print(f"[LLM]   Model: {self.llm_client.model}")
            except Exception as e:
                print(f"[LLM] Failed to initialize: {e}")
                self.llm_enabled = False

        self._setup_routes()

        # 注册状态回调
        self.engine.add_state_callback(self._on_state_update)
        self._latest_state = None

    def _setup_routes(self):
        static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
        self.app.router.add_static("/static", static_dir)
        self.app.router.add_get("/", self._index)
        self.app.router.add_get("/ws", self._websocket_handler)
        
        # REST API
        self.app.router.add_post("/api/start", self._api_start)
        self.app.router.add_post("/api/pause", self._api_pause)
        self.app.router.add_post("/api/reset", self._api_reset)
        self.app.router.add_post("/api/step", self._api_step)
        self.app.router.add_post("/api/fault", self._api_inject_fault)
        self.app.router.add_post("/api/destroy", self._api_destroy_uav)
        self.app.router.add_get("/api/state", self._api_get_state)

        # Module 1 - Environment & View
        self.app.router.add_post("/api/environment/set", self._api_env_set)
        self.app.router.add_get("/api/environment/state", self._api_env_state)
        self.app.router.add_post("/api/view/set", self._api_view_set)

        # Phase B: NL Command & Contract Net
        self.app.router.add_post("/api/nl/command", self._api_nl_command)
        self.app.router.add_post("/api/nl/sitrep", self._api_nl_sitrep)
        self.app.router.add_post("/api/nl/advice", self._api_nl_advice)
        self.app.router.add_post("/api/task/contract_net", self._api_contract_net)

        # Phase C: Red Force & Adversarial
        self.app.router.add_get("/api/redforce/status", self._api_redforce_status)
        self.app.router.add_post("/api/redforce/strategy", self._api_redforce_strategy)
        self.app.router.add_post("/api/training/selfplay", self._api_selfplay_match)
        self.app.router.add_get("/api/training/stats", self._api_training_stats)

        # Phase A: LLM Stats
        self.app.router.add_get("/api/llm/stats", self._api_llm_stats)

        # Interceptor Swarm
        self.app.router.add_get("/api/interceptor/state", self._api_interceptor_state)
        self.app.router.add_post("/api/interceptor/config", self._api_interceptor_config)
        self.app.router.add_post("/api/interceptor/spawn", self._api_interceptor_spawn)

        # OODA Engine
        self.app.router.add_get("/api/ooda/state", self._api_ooda_state)

        # Export / Report
        self.app.router.add_post("/api/spawn_uav", self._api_spawn_uav)

        self.app.router.add_get("/api/export/json", self._api_export_json)
        self.app.router.add_get("/api/export/csv", self._api_export_csv)
        self.app.router.add_get("/api/export/html", self._api_export_html)
        self.app.router.add_get("/api/export/markdown", self._api_export_md)
        self.app.router.add_post("/api/export/all", self._api_export_all)

    async def _index(self, request):
        static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
        html_path = os.path.join(static_dir, "index.html")
        with open(html_path, "r", encoding="utf-8") as f:
            return web.Response(text=f.read(), content_type="text/html")

    async def _on_state_update(self, state: dict):
        """状态更新回调"""
        self._latest_state = state
        # 推送给所有WebSocket客户端
        if self.ws_clients:
            msg = json.dumps(state, ensure_ascii=False, default=str)
            dead_clients = set()
            for ws in self.ws_clients:
                try:
                    await ws.send_str(msg)
                except Exception:
                    dead_clients.add(ws)
            self.ws_clients -= dead_clients

    async def _websocket_handler(self, request):
        """WebSocket处理器"""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.ws_clients.add(ws)

        # 发送初始状态
        if self._latest_state:
            await ws.send_str(json.dumps(self._latest_state, ensure_ascii=False, default=str))

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    await self._handle_ws_command(data)
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            self.ws_clients.discard(ws)
        return ws

    async def _handle_ws_command(self, data: dict):
        """处理WebSocket命令"""
        cmd = data.get("cmd")
        if cmd == "start":
            # 如果任务已完成，自动重置后再启动
            if self.engine.mission_coord.mission_status == "completed":
                await self._reset_engine()
            # 避免重复启动
            if not self.engine.running:
                self.engine.running = True
                asyncio.ensure_future(self.engine.run())
        elif cmd == "pause":
            self.engine.running = False
        elif cmd == "step":
            await self.engine.run_steps(1)
        elif cmd == "fault":
            tick = data.get("tick", 0)
            if tick == 0:
                tick = self.engine.tick + 1  # 下一个tick立即触发
            self.engine.enable_fault_injection(tick)
            # 如果仿真未运行，自动执行一步以触发故障
            if not self.engine.running:
                await self.engine.run_steps(1)
        elif cmd == "destroy":
            uav_id = data.get("uav_id")
            if uav_id:
                await self.engine.destroy_uav(uav_id, "api_command")
                # 自动执行一步以反映变化
                if not self.engine.running:
                    await self.engine.run_steps(1)
        elif cmd == "reset":
            await self._reset_engine()

        # Module 1: Environment & View commands
        elif cmd == "set_view":
            # 视角切换由前端处理，后端仅记录
            view = data.get("view", "topdown")
            uav_id = data.get("uav_id", None)
            # 可在此记录日志
        elif cmd == "toggle_ir":
            enabled = data.get("enabled", False)
            # IR模式由前端控制
        elif cmd == "set_environment":
            if self.engine.environment:
                if "wind" in data:
                    self.engine.environment.set_wind(int(data["wind"]))
                if "precip" in data:
                    self.engine.environment.set_precipitation(data["precip"])
                if "lighting" in data:
                    self.engine.environment.set_lighting(data["lighting"])

    async def _reset_engine(self):
        """重置仿真引擎并自动启动"""
        self.engine.running = False
        await asyncio.sleep(0.1)
        # 保存配置
        uav_count = self.engine.uav_count
        target_count = self.engine.target_count
        fault_enabled = self.engine.fault_injection_enabled
        parallel_mode = getattr(self.engine, '_parallel_uavs', False)
        # 重建引擎
        self.engine = SimulationEngine(uav_count, target_count, parallel_uavs=parallel_mode)
        self.engine.add_state_callback(self._on_state_update)
        if fault_enabled:
            self.engine.enable_fault_injection(30)
        self.engine.initialize()
        # 发送初始状态
        await self._on_state_update(self.engine.get_state())
        # 自动启动
        self.engine.running = True
        asyncio.ensure_future(self.engine.run())

    # ============ REST API ============

    async def _api_start(self, request):
        # 如果任务已完成，自动重置后再启动
        if self.engine.mission_coord.mission_status == "completed":
            await self._reset_engine()
        elif not self.engine.uavs:
            self.engine.initialize()
        # 避免重复启动（engine.running=True 表示已有run()在执行）
        if not self.engine.running:
            self.engine.running = True
            asyncio.ensure_future(self.engine.run())
        return web.json_response({"status": "started", "tick": self.engine.tick})

    async def _api_pause(self, request):
        self.engine.running = False
        return web.json_response({"status": "paused"})

    async def _api_reset(self, request):
        await self._reset_engine()
        return web.json_response({"status": "reset"})

    async def _api_step(self, request):
        await self.engine.run_steps(1)
        return web.json_response({"status": "stepped", "tick": self.engine.tick})

    async def _api_inject_fault(self, request):
        data = await request.json()
        tick = data.get("tick", 0)
        if tick == 0:
            tick = self.engine.tick + 1
        self.engine.enable_fault_injection(tick)
        # 如果仿真未运行，自动执行一步以触发故障
        if not self.engine.running:
            await self.engine.run_steps(1)
        return web.json_response({"status": "fault_enabled", "tick": tick})

    async def _api_destroy_uav(self, request):
        data = await request.json()
        uav_id = data.get("uav_id")
        if uav_id:
            await self.engine.destroy_uav(uav_id, "api_command")
            # 自动执行一步以反映变化
            if not self.engine.running:
                await self.engine.run_steps(1)
            return web.json_response({"status": "destroyed", "uav_id": uav_id})
        return web.json_response({"error": "uav_id required"}, status=400)

    async def _api_get_state(self, request):
        return web.json_response(self._latest_state or self.engine.get_state())

    # ============ Module 1: Environment & View APIs ============

    async def _api_env_set(self, request):
        """设置环境参数"""
        data = await request.json()
        if self.engine.environment:
            if "wind_level" in data:
                self.engine.environment.set_wind(int(data["wind_level"]))
            if "precipitation" in data:
                self.engine.environment.set_precipitation(data["precipitation"])
            if "lighting" in data:
                self.engine.environment.set_lighting(data["lighting"])
            if "terrain_type" in data:
                self.engine.environment.set_terrain(data["terrain_type"])
            return web.json_response({"status": "ok", "environment": self.engine.environment.get_state()})
        return web.json_response({"error": "Environment engine not available"}, status=400)

    async def _api_env_state(self, request):
        """获取环境状态"""
        if self.engine.environment:
            return web.json_response(self.engine.environment.get_state())
        return web.json_response({"error": "Environment engine not available"}, status=400)

    async def _api_view_set(self, request):
        """设置视角模式"""
        data = await request.json()
        return web.json_response({"status": "ok", "view": data.get("view", "topdown")})

    # ============ Phase B: NL Command APIs ============

    async def _api_nl_command(self, request):
        """自然语言指挥"""
        if not self.nl_interface:
            return web.json_response({"error": "NL interface not enabled"}, status=400)
        data = await request.json()
        text = data.get("command", "")
        if not text:
            return web.json_response({"error": "command text required"}, status=400)

        state = self._latest_state or self.engine.get_state()
        context = {
            "mission_status": state.get("mission", {}).get("mission_status", "unknown"),
            "alive_uavs": state.get("summary", {}).get("alive_uavs", 0),
            "total_uavs": state.get("summary", {}).get("total_uavs", 0),
            "known_targets": state.get("summary", {}).get("destroyed_targets", 0),
        }

        result = await self.nl_interface.converse(text, state)
        return web.json_response({
            "status": "ok",
            "intent": result.get("command", {}).intent if hasattr(result.get("command", {}), 'intent') else "unknown",
            "response_text": result.get("response_text", ""),
            "confidence": result.get("command", {}).confidence if hasattr(result.get("command", {}), 'confidence') else 0,
            "needs_clarification": result.get("command", {}).needs_clarification if hasattr(result.get("command", {}), 'needs_clarification') else False,
        })

    async def _api_nl_sitrep(self, request):
        """生成态势简报"""
        if not self.nl_interface:
            return web.json_response({"error": "NL interface not enabled"}, status=400)
        data = await request.json()
        query_type = data.get("query_type", "full_briefing")

        state = self._latest_state or self.engine.get_state()
        sitrep = await self.nl_interface.generate_sitrep(state, query_type)

        return web.json_response({
            "overall": sitrep.overall,
            "threats": sitrep.threats,
            "recommendations": sitrep.recommendations,
            "metrics": sitrep.metrics,
        })

    async def _api_nl_advice(self, request):
        """请求战术建议"""
        if not self.nl_interface:
            return web.json_response({"error": "NL interface not enabled"}, status=400)

        state = self._latest_state or self.engine.get_state()
        advice = await self.nl_interface.request_advice(state)
        return web.json_response({"advice": advice})

    async def _api_contract_net(self, request):
        """合同网任务分配"""
        data = await request.json()

        from core.contract_net import AgenticMissionCoordinator
        coordinator = AgenticMissionCoordinator(
            self.engine.broker,
            use_llm=self.llm_enabled,
            llm_client=self.llm_client,
        )

        task_id = await coordinator.create_task(
            task_type=data.get("task_type", "strike"),
            target_id=data.get("target_id"),
            target_pos=data.get("target_pos"),
            priority=data.get("priority", 1),
            tick=self.engine.tick,
            uavs=self.engine.uavs,
        )

        return web.json_response({
            "status": "ok",
            "task_id": task_id,
            "method": "contract_net",
            "stats": coordinator.get_stats(),
        })

    # ============ Phase C: Red Force & Adversarial APIs ============

    async def _api_redforce_status(self, request):
        """红军状态"""
        if not self.red_force:
            return web.json_response({"error": "Red Force not enabled"}, status=400)
        return web.json_response(self.red_force.get_stats())

    async def _api_redforce_strategy(self, request):
        """触发生成红军策略"""
        if not self.red_force:
            return web.json_response({"error": "Red Force not enabled"}, status=400)

        # 注册红军资产
        if not self.red_force.assets:
            self.red_force.register_assets_from_targets(self.engine.targets)

        state = self._latest_state or self.engine.get_state()
        strategy = await self.red_force.decide(state, self.engine.tick)

        if strategy:
            self.red_force.apply_strategy(strategy, self.engine.targets)
            return web.json_response({
                "strategy": strategy.strategy_name,
                "reasoning": strategy.reasoning,
                "jammer_count": len(strategy.jammer_targets),
                "decoy_count": len(strategy.decoy_positions),
                "focus_fire": strategy.focus_fire_target,
            })
        return web.json_response({"strategy": "hold", "reasoning": "Decision cooldown"})

    async def _api_selfplay_match(self, request):
        """触发一场Self-Play对抗"""
        data = await request.json()

        from core.adversarial_training import AdversarialTrainer, CurriculumStage

        stage = CurriculumStage(
            level=data.get("level", 1),
            name=data.get("stage_name", "custom"),
            description="Custom match",
            blue_uavs=data.get("blue_uavs", 8),
            target_count=data.get("target_count", 3),
            wind_level=data.get("wind_level", 0),
            precipitation=data.get("precipitation", "none"),
            lighting=data.get("lighting", "day"),
            red_difficulty=data.get("difficulty", "normal"),
            max_ticks=data.get("max_ticks", 500),
            pass_criteria={"success_rate": 0.0},
        )

        trainer = AdversarialTrainer(
            simulation_factory=lambda **kw: SimulationEngine(**kw),
            llm_client=self.llm_client,
        )

        result = await trainer.run_self_play_match(stage)
        return web.json_response({
            "match_id": result.match_id,
            "winner": result.winner,
            "blue_score": result.blue_score,
            "red_score": result.red_score,
            "total_ticks": result.total_ticks,
            "blue_uavs_lost": result.blue_uavs_lost,
            "red_assets_destroyed": result.red_assets_destroyed,
        })

    async def _api_training_stats(self, request):
        """获取训练统计"""
        return web.json_response({
            "note": "Training stats reset on restart. Use /api/training/selfplay to run matches.",
            "llm_enabled": self.llm_enabled,
        })

    # ============ Phase A: LLM Stats ============

    async def _api_llm_stats(self, request):
        """LLM调用统计"""
        if not self.llm_client:
            return web.json_response({"error": "LLM not enabled"}, status=400)
        return web.json_response(self.llm_client.get_stats())

    # ============ Interceptor Swarm APIs ============

    async def _api_interceptor_state(self, request):
        """获取拦截集群状态"""
        if self.engine.interceptor_swarm:
            return web.json_response(self.engine.interceptor_swarm.get_state())
        return web.json_response({"error": "Interceptor swarm not initialized"}, status=400)

    async def _api_interceptor_config(self, request):
        """配置拦截集群"""
        data = await request.json()
        if not self.engine.interceptor_swarm:
            return web.json_response({"error": "Interceptor swarm not initialized"}, status=400)

        difficulty = data.get("difficulty", "normal")
        unit_count = data.get("unit_count", 4)

        from core.interceptor import InterceptorSwarm
        self.engine.interceptor_swarm = InterceptorSwarm(
            unit_count=unit_count,
            difficulty=difficulty,
            ai_level=data.get("ai_level", "advanced"),
        )
        self.engine.interceptor_swarm.initialize()
        return web.json_response({
            "status": "ok",
            "difficulty": difficulty,
            "unit_count": unit_count,
        })

    async def _api_interceptor_spawn(self, request):
        """手动生成拦截器"""
        data = await request.json()
        if not self.engine.interceptor_swarm:
            from core.interceptor import InterceptorSwarm
            self.engine.interceptor_swarm = InterceptorSwarm(unit_count=1)
            self.engine.interceptor_swarm.initialize()

        pos = data.get("position", {"x": 5000, "y": 5000, "z": 300})
        from core.interceptor import InterceptorUnit
        idx = len(self.engine.interceptor_swarm.units) + 1
        unit = InterceptorUnit(
            unit_id=f"INT_SPAWN_{idx:02d}",
            position=pos,
            max_speed=data.get("max_speed", 90),
            detection_range=data.get("detection_range", 3500),
            weapon_range=data.get("weapon_range", 1500),
            damage_per_hit=data.get("damage", 15),
            patrol_center=dict(pos),
        )
        self.engine.interceptor_swarm.units[unit.unit_id] = unit
        return web.json_response({"status": "spawned", "unit_id": unit.unit_id})

    # ============ OODA API ============

    async def _api_ooda_state(self, request):
        """获取OODA决策循环状态"""
        if self.engine.ooda_engine:
            return web.json_response(self.engine.ooda_engine.get_ooda_state())
        return web.json_response({"error": "OODA engine not available"}, status=400)

    # ============ Export APIs ============

    async def _api_spawn_uav(self, request):
        """动态生成新UAV"""
        from core.uav_agent import UAVAgent, UAVRole, UAVState
        import random, math, asyncio

        data = await request.json()
        role_name = data.get("role", "striker")

        # 计算新ID (找最大编号+1)
        max_n = 0
        for uid in self.engine.uavs:
            try:
                n = int(uid.split("_")[1])
                if n > max_n: max_n = n
            except: pass
        uav_id = f"UAV_{max_n+1:02d}"

        # 默认位置靠近leader
        leader = next((u for u in self.engine.uavs.values() if u.is_leader and u.alive), None)
        if leader:
            angle = random.uniform(0, 2*math.pi)
            pos = {"x":leader.position["x"]+math.cos(angle)*300,"y":leader.position["y"]+math.sin(angle)*300,"z":leader.position.get("z",200)}
        else:
            pos = {"x":1000,"y":5000,"z":200}

        role_map = {"leader":UAVRole.LEADER,"scout":UAVRole.SCOUT,"striker":UAVRole.STRIKER,"relay":UAVRole.RELAY}
        role = role_map.get(role_name, UAVRole.STRIKER)

        uav = UAVAgent(uav_id=uav_id, role=role, position=pos, broker=self.engine.broker)
        uav.state = UAVState.IDLE
        uav._engine = self.engine
        self.engine.uavs[uav_id] = uav
        self.engine.swarm_network.register_uav(uav)

        # 更新任务分配
        if hasattr(self.engine, 'mission_coord'):
            import asyncio
            asyncio.ensure_future(
                self.engine.mission_coord._assign_tasks(self.engine.uavs, self.engine.tick)
            )

        return web.json_response({
            "status": "spawned", "uav_id": uav_id, "role": role_name,
            "total_uavs": len(self.engine.uavs),
        })

    async def _api_export_json(self, request):
        """导出JSON格式训练报告"""
        exporter = TrainingReportExporter(engine=self.engine)
        json_str = exporter.export_json()
        return web.Response(
            body=json_str, content_type="application/json",
            headers={"Content-Disposition": "attachment; filename=swarm_report.json"},
        )

    async def _api_export_csv(self, request):
        """导出CSV格式"""
        exporter = TrainingReportExporter(engine=self.engine)
        csv_str = exporter.export_csv()
        return web.Response(
            body=csv_str.encode("utf-8-sig"), content_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=swarm_report.csv"},
        )

    async def _api_export_html(self, request):
        """导出HTML可视化报告"""
        exporter = TrainingReportExporter(engine=self.engine)
        html_str = exporter.export_html()
        return web.Response(
            body=html_str, content_type="text/html; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=swarm_report.html"},
        )

    async def _api_export_md(self, request):
        """导出Markdown报告"""
        exporter = TrainingReportExporter(engine=self.engine)
        md_str = exporter.export_markdown()
        return web.Response(
            body=md_str, content_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=swarm_report.md"},
        )

    async def _api_export_all(self, request):
        """批量导出所有格式"""
        data = await request.json()
        base_name = data.get("base_name", "swarm_training_report")
        import os
        export_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "exports")
        os.makedirs(export_dir, exist_ok=True)
        base_path = os.path.join(export_dir, base_name)
        exporter = TrainingReportExporter(engine=self.engine)
        results = exporter.export_all(base_path)
        return web.json_response({"status": "ok", "files": results})

    async def start(self):
        """启动Web服务器"""
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, WEB_HOST, WEB_PORT)
        await site.start()
        print(f"Web可视化服务器已启动: http://{WEB_HOST}:{WEB_PORT}")
        return site
