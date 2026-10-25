/**
 * OODA 决策循环可视化面板
 * ========================
 * 战场地图上的Canvas叠加层，实时显示OODA各阶段数据
 * P0: 阶段计时 P1: 威胁窗口 P2: 方案对比 P3: 历史匹配
 */
class OODAPanel {
    constructor() {
        this._lastState = null;
        this._trendData = [];  // OODA时间趋势
        this._bindEvents();
    }

    _bindEvents() {
        eventBus.on('state-update', (state) => {
            this._lastState = state;
            const ooda = state.ooda;
            if (ooda) {
                this._trendData.push({
                    tick: state.tick,
                    advantage: ooda.ooda_advantage,
                    blue_ms: ooda.blue_avg_time_ms,
                    red_ms: ooda.red_avg_time_ms,
                });
                if (this._trendData.length > 60) this._trendData.shift();
            }
        });
    }

    /** 在主战场Canvas上叠加OODA面板 */
    renderOverlay(ctx, w, h) {
        const state = this._lastState;
        if (!state || !state.ooda) return;
        const o = state.ooda;

        const px = 10, py = h - 170, pw = 240, ph = 155;
        ctx.fillStyle = 'rgba(0,0,0,0.78)';
        ctx.fillRect(px, py, pw, ph);
        ctx.strokeStyle = 'rgba(168,85,247,0.5)';
        ctx.lineWidth = 1;
        ctx.strokeRect(px, py, pw, ph);

        // 标题
        ctx.fillStyle = '#a855f7';
        ctx.font = 'bold 11px monospace';
        ctx.textAlign = 'left';
        ctx.fillText('OODA LOOP MONITOR', px+8, py+18);

        // P0: 阶段耗时甘特图
        const gx = px+8, gy = py+28, gw = pw-16, gh = 40;
        ctx.fillStyle = 'rgba(255,255,255,0.04)';
        ctx.fillRect(gx, gy, gw, gh);

        // 查找当前活跃的agent
        const agents = o.agents || {};
        const phaseData = o.phase_breakdown || {};
        let totalTicks = 20; // default
        const firstAgent = Object.keys(agents)[0];
        if (firstAgent && agents[firstAgent]) {
            totalTicks = agents[firstAgent].avg_ticks || 20;
        }

        // 绘制阶段条
        const phases = [
            {name:'OBS', pct:0.20, color:'#00d4ff'},
            {name:'ORI', pct:0.30, color:'#a855f7'},
            {name:'DEC', pct:0.20, color:'#ffd700'},
            {name:'ACT', pct:0.30, color:'#00ff88'},
        ];
        let bx = gx;
        phases.forEach(p => {
            const bw = gw * p.pct;
            ctx.fillStyle = p.color;
            ctx.globalAlpha = 0.7;
            ctx.fillRect(bx, gy, bw-2, gh);
            ctx.globalAlpha = 1;

            // 标签
            ctx.fillStyle = '#fff';
            ctx.font = '8px monospace';
            ctx.textAlign = 'center';
            if (bw > 30) ctx.fillText(p.name, bx+bw/2, gy+gh/2+3);

            // 时间标签
            const ticks = Math.round(totalTicks * p.pct);
            ctx.fillText(`${ticks}t`, bx+bw/2, gy+gh-4);

            bx += bw;
        });

        // P1: 威胁 & 窗口
        const ty = gy + gh + 8;
        const threats = o.latest_threats || {};
        const windows = o.latest_windows || {};

        ctx.fillStyle = '#ff4757';
        ctx.font = '9px monospace';
        ctx.textAlign = 'left';
        const threatCount = Object.values(state.interceptors?.units || {}).filter(u=>u.state==='ENGAGING'||u.state==='PURSUING').length;
        ctx.fillText(`Threats: ${threatCount} active`, px+8, ty+12);

        ctx.fillStyle = '#00ff88';
        ctx.fillText(`Windows: ${windows.length || 0} open`, px+130, ty+12);

        // P2: OODA优势指标
        const ay = ty + 18;
        const adv = o.ooda_advantage || 0;
        const inside = o.inside_enemy_loop;
        ctx.fillStyle = inside ? '#00ff88' : '#ff4757';
        ctx.font = 'bold 10px monospace';
        ctx.fillText(inside ? 'INSIDE ENEMY LOOP' : 'OUTSIDE LOOP', px+8, ay+12);

        // 优势数字
        ctx.fillStyle = adv > 0 ? '#00ff88' : '#ff4757';
        ctx.font = 'bold 14px monospace';
        ctx.textAlign = 'right';
        const advText = `${adv>=0?'+':''}${(adv*100).toFixed(1)}%`;
        ctx.fillText(advText, px+pw-8, ay+12);

        // P3: 蓝红速度对比条
        const sy = ay + 16;
        const blueMs = o.blue_avg_time_ms || 2000;
        const redMs = o.red_avg_time_ms || 2000;
        const maxMs = Math.max(blueMs, redMs, 1000);
        const barW = pw-16;

        ctx.fillStyle = '#8b9bb4';
        ctx.font = '8px monospace';
        ctx.textAlign = 'left';
        ctx.fillText('Blue', px+8, sy+10);
        ctx.fillText(`${blueMs.toFixed(0)}ms`, px+40, sy+10);

        const blueBarW = (blueMs/maxMs) * (barW*0.6);
        ctx.fillStyle = '#00d4ff';
        ctx.globalAlpha = 0.6;
        ctx.fillRect(px+80, sy+3, blueBarW, 8);
        ctx.globalAlpha = 1;

        ctx.fillStyle = '#8b9bb4';
        ctx.fillText('Red', px+8, sy+22);
        ctx.fillText(`${redMs.toFixed(0)}ms`, px+40, sy+22);

        const redBarW = (redMs/maxMs) * (barW*0.6);
        ctx.fillStyle = '#ff4757';
        ctx.globalAlpha = 0.6;
        ctx.fillRect(px+80, sy+15, redBarW, 8);
        ctx.globalAlpha = 1;

        // P3: 评级标签
        const rating = o.advantage_rating || '--';
        ctx.fillStyle = '#a855f7';
        ctx.font = 'bold 13px monospace';
        ctx.textAlign = 'right';
        ctx.fillText(rating.substring(0,15), px+pw-8, py+ph-6);
    }
}

let oodaPanel = null;
eventBus.on('init', () => { oodaPanel = new OODAPanel(); window._oodaPanel = oodaPanel; });
