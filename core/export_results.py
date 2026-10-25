"""
训练结果导出引擎 (Training Results Export Engine)
==================================================

将仿真训练数据导出为多种格式，支持离线分析和报告生成。

导出格式:
- JSON:     完整结构化数据 (API/程序化分析)
- CSV:      表格数据 (Excel/数据分析)
- HTML:     可视化报告 (浏览器查看/打印)
- Markdown: 文本报告 (文档/分享)

数据模块:
- 总览摘要: 任务成功/失败、UAV存活率、目标摧毁率
- UAV详情:  每架UAV的全程统计数据
- OODA分析: OODA循环速度、优势指数、各阶段质量
- 拦截威胁: 拦截器效能、规避成功率
- 通信统计: 消息吞吐、丢包率、延迟
- 事件日志: 完整事件时间线

使用:
    from core.export_results import TrainingReportExporter
    exporter = TrainingReportExporter(engine)
    exporter.export_json("report.json")
    exporter.export_csv("report.csv")
    exporter.export_html("report.html")
"""
import csv
import io
import json
import time
from dataclasses import dataclass, asdict, field
from typing import Optional
from collections import defaultdict


@dataclass
class ExportBundle:
    """导出数据包"""
    meta: dict
    summary: dict
    uav_details: list
    target_details: list
    ooda_analysis: dict
    interceptor_analysis: dict
    communication_stats: dict
    event_timeline: list = field(default_factory=list)
    environment_log: list = field(default_factory=list)


class TrainingReportExporter:
    """训练报告导出器"""

    def __init__(self, engine=None, state: dict = None):
        """
        Args:
            engine: SimulationEngine 实例
            state: 或直接传入 get_state() 的输出
        """
        if engine:
            self.state = engine.get_state()
            self.tick = engine.tick
        else:
            self.state = state or {}
            self.tick = self.state.get("tick", 0)

    # ============ 数据打包 ============

    def build_bundle(self) -> ExportBundle:
        """构建完整导出数据包"""
        s = self.state
        summary = s.get("summary", {})
        uavs = s.get("uavs", {})
        targets = s.get("targets", {})
        network = s.get("network", {})
        comm = s.get("communication", {})
        mission = s.get("mission", {})
        env = s.get("environment", {})
        ooda = s.get("ooda", {})
        interceptors = s.get("interceptors", {})
        events = s.get("events", [])

        return ExportBundle(
            meta={
                "export_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "export_timestamp": time.time(),
                "simulation_tick": self.tick,
                "sim_elapsed_time_s": s.get("elapsed_time", 0),
                "mission_status": mission.get("mission_status", "unknown"),
            },
            summary={
                "total_uavs": summary.get("total_uavs", 0),
                "alive_uavs": summary.get("alive_uavs", 0),
                "destroyed_uavs": summary.get("destroyed_uavs", 0),
                "uav_survival_rate": round(
                    summary.get("alive_uavs", 0) / max(summary.get("total_uavs", 1), 1), 3
                ),
                "total_targets": summary.get("total_targets", 0),
                "destroyed_targets": summary.get("destroyed_targets", 0),
                "target_destruction_rate": round(
                    summary.get("destroyed_targets", 0) / max(summary.get("total_targets", 1), 1), 3
                ),
                "mission_success": mission.get("mission_status") == "completed",
                "reconnect_count": summary.get("reconnect_count", 0),
                "leader_changes": summary.get("leader_changes", 0),
                "leader_id": summary.get("leader_id", ""),
            },
            uav_details=[
                {
                    "id": u.get("id", ""),
                    "role": u.get("role", ""),
                    "state": u.get("state", ""),
                    "alive": u.get("alive", False),
                    "is_leader": u.get("is_leader", False),
                    "health": round(u.get("health", 0), 1),
                    "battery": round(u.get("battery", 0), 1),
                    "position": u.get("position", {}),
                    "velocity": u.get("velocity", {}),
                    "attitude": u.get("attitude", {}),
                    "threat_level": u.get("threat_level", "none"),
                    "evasion_strategy": u.get("evasion_strategy", "none"),
                    "targets_engaged": u.get("targets_engaged", 0),
                    "targets_destroyed": u.get("targets_destroyed", 0),
                    "distance_traveled": round(u.get("distance_traveled", 0), 1),
                    "messages_sent": u.get("messages_sent", 0),
                    "messages_received": u.get("messages_received", 0),
                    "neighbor_count": len(u.get("neighbors", [])),
                    "gps_signal": u.get("gps_signal", 100),
                }
                for u in uavs.values()
            ],
            target_details=[
                {
                    "id": t.get("id", ""),
                    "type": t.get("type", ""),
                    "health": round(t.get("health", 0), 1),
                    "max_health": t.get("max_health", 100),
                    "destroyed": t.get("destroyed", False),
                    "position": t.get("position", {}),
                }
                for t in targets.values()
            ],
            ooda_analysis={
                "total_cycles": ooda.get("total_cycles_completed", 0),
                "ooda_advantage": ooda.get("ooda_advantage", 0),
                "advantage_rating": ooda.get("advantage_rating", ""),
                "blue_avg_time_ms": ooda.get("blue_avg_time_ms", 0),
                "red_avg_time_ms": ooda.get("red_avg_time_ms", 0),
                "inside_enemy_loop": ooda.get("inside_enemy_loop", False),
                "agent_details": ooda.get("agents", {}),
            },
            interceptor_analysis={
                "summary": interceptors.get("summary", {}),
                "units": [
                    {
                        "id": u.get("id", ""),
                        "state": u.get("state", ""),
                        "alive": u.get("alive", False),
                        "health": u.get("health", 100),
                        "current_target": u.get("current_target", ""),
                        "targets_destroyed": u.get("targets_destroyed", 0),
                        "damage_per_hit": u.get("damage_per_hit", 0),
                    }
                    for u in interceptors.get("units", {}).values()
                ],
            },
            communication_stats={
                "total_sent": comm.get("total_sent", 0),
                "total_delivered": comm.get("total_delivered", 0),
                "total_dropped": comm.get("total_dropped", 0),
                "delivery_rate": round(
                    comm.get("total_delivered", 0) / max(comm.get("total_sent", 1), 1), 3
                ),
                "avg_latency_ms": round(comm.get("delivery_latency_ms", 0), 1),
                "peak_queue_size": comm.get("peak_queue_size", 0),
                "by_priority": comm.get("by_priority", {}),
            },
            event_timeline=events[-100:] if events else [],
            environment_log={
                "wind": env.get("wind", {}),
                "precipitation": env.get("precipitation", {}),
                "lighting": env.get("lighting", {}),
                "terrain": env.get("terrain", {}).get("type", "mixed"),
            },
        )

    # ============ JSON 导出 ============

    def export_json(self, filepath: str = None) -> str:
        """导出为 JSON 格式"""
        bundle = self.build_bundle()
        json_str = json.dumps(asdict(bundle), ensure_ascii=False, indent=2, default=str)
        if filepath:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(json_str)
        return json_str

    # ============ CSV 导出 ============

    def export_csv(self, filepath: str = None) -> str:
        """导出为 CSV 格式 (多表格)"""
        bundle = self.build_bundle()
        output = io.StringIO()
        writer = csv.writer(output)
        # 使用 utf-8-sig 保证Excel正确打开中文

        # 表1: 总览
        writer.writerow(["=== 仿真总览 ==="])
        writer.writerow(["指标", "数值"])
        for k, v in bundle.summary.items():
            writer.writerow([k, v])
        writer.writerow([])

        # 表2: UAV详情
        writer.writerow(["=== UAV详情 ==="])
        if bundle.uav_details:
            headers = list(bundle.uav_details[0].keys())
            writer.writerow(headers)
            for u in bundle.uav_details:
                writer.writerow([u.get(h, "") for h in headers])
        writer.writerow([])

        # 表3: 目标详情
        writer.writerow(["=== 目标详情 ==="])
        if bundle.target_details:
            headers = list(bundle.target_details[0].keys())
            writer.writerow(headers)
            for t in bundle.target_details:
                writer.writerow([t.get(h, "") for h in headers])
        writer.writerow([])

        # 表4: 通信统计
        writer.writerow(["=== 通信统计 ==="])
        writer.writerow(["指标", "数值"])
        for k, v in bundle.communication_stats.items():
            if not isinstance(v, dict):
                writer.writerow([k, v])
        writer.writerow([])

        # 表5: 拦截器
        writer.writerow(["=== 拦截器效能 ==="])
        if bundle.interceptor_analysis.get("units"):
            headers = list(bundle.interceptor_analysis["units"][0].keys())
            writer.writerow(headers)
            for u in bundle.interceptor_analysis["units"]:
                writer.writerow([u.get(h, "") for h in headers])
        writer.writerow([])

        # 表6: OODA
        writer.writerow(["=== OODA分析 ==="])
        writer.writerow(["指标", "数值"])
        ooda_flat = {
            "总循环数": bundle.ooda_analysis.get("total_cycles"),
            "优势指数": bundle.ooda_analysis.get("ooda_advantage"),
            "评级": bundle.ooda_analysis.get("advantage_rating"),
            "蓝方平均(ms)": bundle.ooda_analysis.get("blue_avg_time_ms"),
            "红方平均(ms)": bundle.ooda_analysis.get("red_avg_time_ms"),
            "进入敌方循环": bundle.ooda_analysis.get("inside_enemy_loop"),
        }
        for k, v in ooda_flat.items():
            writer.writerow([k, v])
        writer.writerow([])

        # 表7: 事件日志
        writer.writerow(["=== 事件日志 ==="])
        if bundle.event_timeline:
            writer.writerow(["Tick", "类别", "消息"])
            for e in bundle.event_timeline:
                writer.writerow([e.get("tick", ""), e.get("category", ""), e.get("message", "")])

        csv_str = output.getvalue()
        if filepath:
            with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
                f.write(csv_str)
        return csv_str

    # ============ HTML 报告 ============

    def export_html(self, filepath: str = None) -> str:
        """导出为可视化 HTML 报告"""
        bundle = self.build_bundle()
        s = bundle.summary
        o = bundle.ooda_analysis
        c = bundle.communication_stats
        intr = bundle.interceptor_analysis.get("summary", {})
        uavs = bundle.uav_details

        alive = s["alive_uavs"]
        total = s["total_uavs"]
        destroyed_tgt = s["destroyed_targets"]
        total_tgt = s["total_targets"]
        survival = s["uav_survival_rate"]
        destruction = s["target_destruction_rate"]

        # 颜色函数
        def rate_color(r):
            if r >= 0.8: return "#00ff88"
            elif r >= 0.5: return "#ffd700"
            return "#ff4757"

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>蜂群仿真训练报告 - {bundle.meta['export_time']}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI','Microsoft YaHei',sans-serif;background:#0a0e1a;color:#e0e6ed;padding:20px}}
h1{{color:#00d4ff;border-bottom:2px solid #1e2d3f;padding-bottom:10px;margin-bottom:20px}}
h2{{color:#a855f7;margin:20px 0 10px;font-size:16px}}
.meta{{color:#8b9bb4;font-size:12px;margin-bottom:20px}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:20px}}
.card{{background:#111827;border:1px solid #1e2d3f;border-radius:8px;padding:16px;text-align:center}}
.card-label{{font-size:11px;color:#8b9bb4;margin-bottom:6px}}
.card-value{{font-size:28px;font-weight:bold}}
.green{{color:#00ff88}}.yellow{{color:#ffd700}}.red{{color:#ff4757}}.blue{{color:#00d4ff}}
table{{width:100%;border-collapse:collapse;margin:10px 0;font-size:12px}}
th{{background:#111827;color:#00d4ff;padding:8px 10px;text-align:left;border-bottom:2px solid #1e2d3f}}
td{{padding:6px 10px;border-bottom:1px solid #1a2332}}
tr:hover{{background:rgba(0,212,255,0.03)}}
.bar{{height:8px;border-radius:4px;background:#1a2332;overflow:hidden}}
.bar-fill{{height:100%;border-radius:4px;transition:width 0.5s}}
.tag{{display:inline-block;padding:2px 8px;border-radius:3px;font-size:10px;font-weight:bold}}
.tag-success{{background:rgba(0,255,136,0.15);color:#00ff88}}
.tag-danger{{background:rgba(255,71,87,0.15);color:#ff4757}}
.tag-warn{{background:rgba(255,215,0,0.15);color:#ffd700}}
.tag-info{{background:rgba(0,212,255,0.1);color:#00d4ff}}
.footer{{margin-top:30px;padding-top:10px;border-top:1px solid #1e2d3f;color:#555;font-size:10px}}
</style>
</head>
<body>
<h1>蜂群多无人机协同作战仿真 — 训练报告</h1>
<div class="meta">导出时间: {bundle.meta['export_time']} | 仿真Tick: {bundle.meta['simulation_tick']} | 运行时间: {bundle.meta['sim_elapsed_time_s']}s | 任务状态: <span class="tag {'tag-success' if s['mission_success'] else 'tag-danger'}">{'成功' if s['mission_success'] else '未完成'}</span></div>

<h2>总览摘要</h2>
<div class="grid">
    <div class="card"><div class="card-label">存活UAV</div><div class="card-value green">{alive}/{total}</div><div class="bar" style="margin-top:6px"><div class="bar-fill" style="width:{survival*100}%;background:{rate_color(survival)}"></div></div></div>
    <div class="card"><div class="card-label">摧毁目标</div><div class="card-value {'green' if destruction>=0.8 else 'yellow'}">{destroyed_tgt}/{total_tgt}</div><div class="bar" style="margin-top:6px"><div class="bar-fill" style="width:{destruction*100}%;background:{rate_color(destruction)}"></div></div></div>
    <div class="card"><div class="card-label">重组网次数</div><div class="card-value blue">{s['reconnect_count']}</div></div>
    <div class="card"><div class="card-label">领导者变更</div><div class="card-value {'yellow' if s['leader_changes']>0 else 'green'}">{s['leader_changes']}</div></div>
</div>

<h2>OODA 决策循环分析</h2>
<div class="grid">
    <div class="card"><div class="card-label">OODA优势指数</div><div class="card-value {'green' if o['ooda_advantage']>0.1 else 'red'}">{'+' if o['ooda_advantage']>=0 else ''}{o['ooda_advantage']*100:.1f}%</div></div>
    <div class="card"><div class="card-label">优势评级</div><div class="card-value blue">{o['advantage_rating'][:15] if o['advantage_rating'] else '--'}</div></div>
    <div class="card"><div class="card-label">蓝方平均OODA</div><div class="card-value green">{o['blue_avg_time_ms']:.0f}ms</div></div>
    <div class="card"><div class="card-label">红方平均OODA</div><div class="card-value red">{o['red_avg_time_ms']:.0f}ms</div></div>
</div>
<p style="font-size:14px;margin:8px 0">状态: <span class="tag {'tag-success' if o['inside_enemy_loop'] else 'tag-danger'}">{'INSIDE ENEMY OODA LOOP' if o['inside_enemy_loop'] else 'OUTSIDE'}</span> | 总循环: {o['total_cycles']}</p>

<h2>UAV 详细数据</h2>
<table>
<tr><th>ID</th><th>角色</th><th>状态</th><th>存活</th><th>血量</th><th>电量</th><th>交战次数</th><th>摧毁目标</th><th>飞行距离</th><th>威胁等级</th><th>规避策略</th></tr>
"""
        for u in uavs:
            alive_tag = 'tag-success' if u['alive'] else 'tag-danger'
            threat_tags = {'critical':'tag-danger','high':'tag-warn','medium':'tag-info','low':'tag-success','none':''}
            html += f"""<tr>
<td>{u['id']}</td><td>{u['role']}</td><td>{u['state']}</td>
<td><span class="tag {alive_tag}">{'是' if u['alive'] else '否'}</span></td>
<td>{u['health']:.0f}%</td><td>{u['battery']:.0f}%</td>
<td>{u['targets_engaged']}</td><td>{u['targets_destroyed']}</td>
<td>{u['distance_traveled']:.0f}m</td>
<td><span class="tag {threat_tags.get(u['threat_level'],'')}">{u['threat_level']}</span></td>
<td>{u['evasion_strategy']}</td>
</tr>"""

        html += "\n</table>\n"

        # 通信统计
        html += f"""<h2>通信系统统计</h2>
<div class="grid">
    <div class="card"><div class="card-label">发送/投递/丢弃</div><div class="card-value blue">{c['total_sent']}/{c['total_delivered']}/{c['total_dropped']}</div></div>
    <div class="card"><div class="card-label">投递率</div><div class="card-value {'green' if c['delivery_rate']>0.9 else 'yellow'}">{c['delivery_rate']*100:.1f}%</div></div>
    <div class="card"><div class="card-label">平均延迟</div><div class="card-value green">{c['avg_latency_ms']:.1f}ms</div></div>
    <div class="card"><div class="card-label">峰值队列</div><div class="card-value {'yellow' if c['peak_queue_size']>300 else 'green'}">{c['peak_queue_size']}</div></div>
</div>
"""

        # 拦截器摘要
        html += f"""<h2>拦截集群威胁</h2>
<div class="grid">
    <div class="card"><div class="card-label">拦截器存活</div><div class="card-value red">{intr.get('alive_units',0)}/{intr.get('total_units',0)}</div></div>
    <div class="card"><div class="card-label">巡逻/追踪/攻击</div><div class="card-value blue">{intr.get('patrol_count',0)}/{intr.get('pursuing_count',0)}/{intr.get('engaging_count',0)}</div></div>
    <div class="card"><div class="card-label">累计造成伤害</div><div class="card-value red">{intr.get('total_damage_dealt',0):.0f}</div></div>
    <div class="card"><div class="card-label">摧毁UAV数</div><div class="card-value red">{intr.get('total_uavs_destroyed',0)}</div></div>
</div>
"""

        html += f"""<div class="footer">蜂群多无人机协同作战仿真系统 | 自动生成报告 | {bundle.meta['export_time']}</div>
</body></html>"""

        if filepath:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(html)
        return html

    # ============ Markdown 报告 ============

    def export_markdown(self, filepath: str = None) -> str:
        """导出为 Markdown 文本报告"""
        bundle = self.build_bundle()
        s = bundle.summary
        o = bundle.ooda_analysis
        c = bundle.communication_stats

        md = f"""# 蜂群仿真训练报告

> 导出时间: {bundle.meta['export_time']}
> 仿真Tick: {bundle.meta['simulation_tick']} | 运行时间: {bundle.meta['sim_elapsed_time_s']}s
> 任务状态: **{'成功' if s['mission_success'] else '未完成'}**

## 总览

| 指标 | 数值 |
|------|------|
| 存活UAV | {s['alive_uavs']}/{s['total_uavs']} ({s['uav_survival_rate']:.0%}) |
| 摧毁目标 | {s['destroyed_targets']}/{s['total_targets']} ({s['target_destruction_rate']:.0%}) |
| 重组网次数 | {s['reconnect_count']} |
| 领导者变更 | {s['leader_changes']} |

## OODA 决策循环

| 指标 | 数值 |
|------|------|
| OODA优势指数 | {o['ooda_advantage']:+.1%} |
| 评级 | {o['advantage_rating']} |
| 蓝方平均 | {o['blue_avg_time_ms']:.0f}ms |
| 红方平均 | {o['red_avg_time_ms']:.0f}ms |
| 进入敌方循环 | {'是' if o['inside_enemy_loop'] else '否'} |

## UAV 详情

| ID | 角色 | 状态 | 存活 | 血量 | 电量 | 交战 | 摧毁 | 飞行距离 | 威胁 |
|----|------|------|------|------|------|------|------|----------|------|
"""
        for u in bundle.uav_details:
            md += f"| {u['id']} | {u['role']} | {u['state']} | {'是' if u['alive'] else '否'} | {u['health']:.0f}% | {u['battery']:.0f}% | {u['targets_engaged']} | {u['targets_destroyed']} | {u['distance_traveled']:.0f}m | {u['threat_level']} |\n"

        md += f"""
## 通信统计

| 指标 | 数值 |
|------|------|
| 发送/投递/丢弃 | {c['total_sent']}/{c['total_delivered']}/{c['total_dropped']} |
| 投递率 | {c['delivery_rate']:.1%} |
| 平均延迟 | {c['avg_latency_ms']:.1f}ms |

---
*自动生成于 {bundle.meta['export_time']}*
"""

        if filepath:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(md)
        return md

    # ============ 全格式导出 ============

    def export_all(self, base_name: str = "training_report"):
        """一次性导出所有格式"""
        import os
        results = {}

        # JSON
        json_path = f"{base_name}.json"
        self.export_json(json_path)
        results["json"] = os.path.abspath(json_path)

        # CSV
        csv_path = f"{base_name}.csv"
        self.export_csv(csv_path)
        results["csv"] = os.path.abspath(csv_path)

        # HTML
        html_path = f"{base_name}.html"
        self.export_html(html_path)
        results["html"] = os.path.abspath(html_path)

        # Markdown
        md_path = f"{base_name}.md"
        self.export_markdown(md_path)
        results["markdown"] = os.path.abspath(md_path)

        return results
