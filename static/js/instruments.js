/**
 * 蜂群作战仿真系统 - 虚拟飞行仪表盘
 *
 * 提供7个仪表：
 * 1. 姿态仪（人工地平线）
 * 2. 高度表
 * 3. 空速表
 * 4. 航向指示器（罗盘）
 * 5. 电池电量表
 * 6. GPS信号强度
 * 7. 垂直速度表
 */

class InstrumentPanel {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        if (!this.container) return;

        this._selectedUavId = null;
        this._uavs = [];
        this._gauges = {};
        this._buildPanel();
        this._bindEvents();
    }

    _buildPanel() {
        this.container.innerHTML = `
        <div class="instruments-layout">
            <!-- UAV选择器 -->
            <div class="inst-selector">
                <label class="inst-label">选择无人机：</label>
                <select id="inst-uav-select" class="inst-select">
                    <option value="">-- 自动选择 --</option>
                </select>
            </div>

            <!-- 仪表网格 -->
            <div class="inst-grid">
                <!-- 姿态仪（大，占2x2） -->
                <div class="inst-card inst-card-large">
                    <div class="inst-card-header">姿态仪 (AHRS)</div>
                    <canvas id="gauge-attitude" width="280" height="280"></canvas>
                </div>

                <!-- 高度表 + 空速表 并排 -->
                <div class="inst-card">
                    <div class="inst-card-header">高度表 (ALT)</div>
                    <canvas id="gauge-altitude" width="140" height="200"></canvas>
                </div>
                <div class="inst-card">
                    <div class="inst-card-header">空速表 (IAS)</div>
                    <canvas id="gauge-airspeed" width="140" height="200"></canvas>
                </div>

                <!-- 航向指示器 -->
                <div class="inst-card">
                    <div class="inst-card-header">航向指示器 (HDG)</div>
                    <canvas id="gauge-heading" width="200" height="200"></canvas>
                </div>

                <!-- 电池 + GPS + 垂直速度 并排 -->
                <div class="inst-card">
                    <div class="inst-card-header">电池电量</div>
                    <canvas id="gauge-battery" width="130" height="130"></canvas>
                </div>
                <div class="inst-card">
                    <div class="inst-card-header">GPS信号</div>
                    <canvas id="gauge-gps" width="130" height="130"></canvas>
                </div>
                <div class="inst-card">
                    <div class="inst-card-header">垂直速度 (V/S)</div>
                    <canvas id="gauge-vs" width="130" height="200"></canvas>
                </div>
            </div>

            <!-- 补充数据面板 -->
            <div class="inst-telemetry" id="inst-telemetry">
                <div class="telemetry-row">
                    <span class="telem-label">位置:</span>
                    <span class="telem-value" id="telem-position">--</span>
                </div>
                <div class="telemetry-row">
                    <span class="telem-label">Roll/Pitch/Yaw:</span>
                    <span class="telem-value" id="telem-attitude">--</span>
                </div>
                <div class="telemetry-row">
                    <span class="telem-label">任务:</span>
                    <span class="telem-value" id="telem-task">--</span>
                </div>
                <div class="telemetry-row">
                    <span class="telem-label">通信:</span>
                    <span class="telem-value" id="telem-comm">--</span>
                </div>
                <div class="telemetry-row">
                    <span class="telem-label">载荷:</span>
                    <span class="telem-value" id="telem-payload">--</span>
                </div>
            </div>
        </div>`;

        // 获取所有gauge canvas引用
        const gaugeIds = ['attitude', 'altitude', 'airspeed', 'heading', 'battery', 'gps', 'vs'];
        gaugeIds.forEach(id => {
            const canvas = this.container.querySelector(`#gauge-${id}`);
            if (canvas) {
                this._gauges[id] = {
                    canvas,
                    ctx: canvas.getContext('2d'),
                };
            }
        });
    }

    _bindEvents() {
        eventBus.on('state-update', (state) => this.update(state));

        const select = this.container?.querySelector('#inst-uav-select');
        if (select) {
            select.addEventListener('change', () => {
                this._selectedUavId = select.value || null;
            });
        }
    }

    update(state) {
        // 更新UAV选择器
        const uavs = Object.values(state.uavs || {}).sort((a, b) => a.id.localeCompare(b.id));
        this._uavs = uavs;
        this._updateSelect(uavs);

        // 自动选择或使用指定UAV
        let uav = null;
        if (this._selectedUavId) {
            uav = state.uavs?.[this._selectedUavId];
        }
        if (!uav || !uav.alive) {
            // 自动选择：优先leader，否则第一个存活的
            uav = uavs.find(u => u.is_leader && u.alive) || uavs.find(u => u.alive);
        }

        if (!uav) {
            this._clearGauges();
            return;
        }

        // 渲染仪表
        this._drawAttitude(uav);
        this._drawAltitude(uav);
        this._drawAirspeed(uav);
        this._drawHeading(uav);
        this._drawBattery(uav);
        this._drawGPS(uav);
        this._drawVS(uav);

        // 更新遥测面板
        this._updateTelemetry(uav);
    }

    _updateSelect(uavs) {
        const select = this.container?.querySelector('#inst-uav-select');
        if (!select) return;
        const currentVal = select.value;
        select.innerHTML = '<option value="">-- 自动选择 --</option>' +
            uavs.filter(u => u.alive).map(u =>
                `<option value="${u.id}">${u.id} (${ROLE_NAMES[u.role] || u.role})</option>`
            ).join('');
        if (currentVal && uavs.find(u => u.id === currentVal)) {
            select.value = currentVal;
        }
    }

    // ============ 姿态仪（人工地平线） ============
    _drawAttitude(uav) {
        const g = this._gauges.attitude;
        if (!g) return;
        const ctx = g.ctx, w = g.canvas.width, h = g.canvas.height;
        const cx = w / 2, cy = h / 2, r = Math.min(w, h) / 2 - 5;

        ctx.clearRect(0, 0, w, h);
        ctx.save();

        // 裁剪到圆形
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.clip();

        const roll = (uav.attitude?.roll || 0) * Math.PI / 180;
        const pitch = uav.attitude?.pitch || 0;
        const pitchOffset = pitch * 3;  // 每度3像素

        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(-roll);
        ctx.translate(0, pitchOffset);

        // 天空（上半圆）
        const skyGrad = ctx.createLinearGradient(0, -r, 0, 0);
        skyGrad.addColorStop(0, '#1a5fb4');
        skyGrad.addColorStop(1, '#62a0ea');
        ctx.fillStyle = skyGrad;
        ctx.fillRect(-r, -r, r * 2, r);

        // 地面（下半圆）
        const gndGrad = ctx.createLinearGradient(0, 0, 0, r);
        gndGrad.addColorStop(0, '#8b6914');
        gndGrad.addColorStop(1, '#5a3a0a');
        ctx.fillStyle = gndGrad;
        ctx.fillRect(-r, 0, r * 2, r);

        // 地平线
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(-r, 0);
        ctx.lineTo(r, 0);
        ctx.stroke();

        // 俯仰刻度线（10度间隔）
        for (let deg = -30; deg <= 30; deg += 5) {
            const py = deg * 3;  // 与pitchOffset同比例
            const lineW = deg % 10 === 0 ? 40 : 20;
            ctx.strokeStyle = '#fff';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(-lineW / 2, py);
            ctx.lineTo(lineW / 2, py);
            ctx.stroke();
            if (deg % 10 === 0) {
                ctx.fillStyle = '#fff';
                ctx.font = '9px monospace';
                ctx.textAlign = 'left';
                ctx.fillText(`${Math.abs(deg)}°`, lineW / 2 + 3, py + 4);
            }
        }

        ctx.restore();

        // 固定飞机符号
        ctx.strokeStyle = '#ffcc00';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(cx - 30, cy);
        ctx.lineTo(cx - 8, cy);
        ctx.moveTo(cx + 8, cy);
        ctx.lineTo(cx + 30, cy);
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(cx, cy - 5);
        ctx.lineTo(cx, cy - 20);
        ctx.stroke();

        // 中心点
        ctx.fillStyle = '#ffcc00';
        ctx.beginPath();
        ctx.arc(cx, cy, 3, 0, Math.PI * 2);
        ctx.fill();

        // 外圈 + 翻滚刻度
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.strokeStyle = '#444';
        ctx.lineWidth = 2;
        ctx.stroke();

        // 翻滚刻度弧（顶部）
        for (let deg = -60; deg <= 60; deg += 10) {
            const rad = (deg - 90) * Math.PI / 180;
            const outerR = r - 5;
            const innerR = r - (deg % 30 === 0 ? 18 : 12);
            ctx.beginPath();
            ctx.moveTo(cx + Math.cos(rad) * outerR, cy + Math.sin(rad) * outerR);
            ctx.lineTo(cx + Math.cos(rad) * innerR, cy + Math.sin(rad) * innerR);
            ctx.strokeStyle = '#fff';
            ctx.lineWidth = 1;
            ctx.stroke();
        }

        // 翻滚指示三角（始终指向roll角度）
        const rollAngleRad = roll - Math.PI / 2;
        ctx.fillStyle = '#ffcc00';
        ctx.beginPath();
        const tx = cx + Math.cos(rollAngleRad) * (r - 3);
        const ty = cy + Math.sin(rollAngleRad) * (r - 3);
        ctx.moveTo(tx, ty);
        ctx.lineTo(tx - 6, ty - 12);
        ctx.lineTo(tx + 6, ty - 12);
        ctx.closePath();
        ctx.fill();

        ctx.restore();
    }

    // ============ 高度表 ============
    _drawAltitude(uav) {
        const g = this._gauges.altitude;
        if (!g) return;
        const ctx = g.ctx, w = g.canvas.width, h = g.canvas.height;
        const cx = w / 2;

        ctx.clearRect(0, 0, w, h);

        const alt = uav.position?.z || 200;
        const maxAlt = 500;

        // 背景
        ctx.fillStyle = '#0d1520';
        ctx.fillRect(2, 2, w - 4, h - 4);
        ctx.strokeStyle = '#1e2d3f';
        ctx.lineWidth = 1;
        ctx.strokeRect(2, 2, w - 4, h - 4);

        // 刻度尺（从alt-50到alt+50）
        const rangeM = 50;
        const pixelsPerM = (h - 20) / (rangeM * 2);
        const centerY = h / 2;
        const offset = (alt % 10) * pixelsPerM;

        // 刻度线
        for (let val = alt - 50; val <= alt + 50; val += 5) {
            const py = centerY - (val - alt) * pixelsPerM;
            if (py < 5 || py > h - 5) continue;

            if (val % 10 === 0) {
                ctx.strokeStyle = '#fff';
                ctx.lineWidth = 1;
                ctx.beginPath();
                ctx.moveTo(cx - 25, py);
                ctx.lineTo(cx + 25, py);
                ctx.stroke();
                ctx.fillStyle = '#fff';
                ctx.font = '10px monospace';
                ctx.textAlign = 'right';
                ctx.fillText(`${val}m`, cx - 28, py + 4);
            } else {
                ctx.strokeStyle = '#555';
                ctx.lineWidth = 0.5;
                ctx.beginPath();
                ctx.moveTo(cx - 15, py);
                ctx.lineTo(cx + 15, py);
                ctx.stroke();
            }
        }

        // 中心指针区域
        ctx.fillStyle = '#1a2b3c';
        ctx.fillRect(cx - 30, centerY - 12, 60, 24);
        ctx.strokeStyle = '#00d4ff';
        ctx.lineWidth = 1;
        ctx.strokeRect(cx - 30, centerY - 12, 60, 24);

        // 高度读数
        ctx.fillStyle = '#00ff88';
        ctx.font = 'bold 16px monospace';
        ctx.textAlign = 'center';
        ctx.fillText(`${Math.round(alt)}`, cx, centerY + 5);

        // 标签
        ctx.fillStyle = '#8b9bb4';
        ctx.font = '8px sans-serif';
        ctx.fillText('ALT (m)', cx, h - 5);
    }

    // ============ 空速表 ============
    _drawAirspeed(uav) {
        const g = this._gauges.airspeed;
        if (!g) return;
        const ctx = g.ctx, w = g.canvas.width, h = g.canvas.height;
        const cx = w / 2;

        ctx.clearRect(0, 0, w, h);

        const speed = Math.sqrt((uav.velocity?.x || 0) ** 2 + (uav.velocity?.y || 0) ** 2);
        const maxSpeed = uav.max_speed || 80;

        // 类似高度表的垂直带式刻度
        ctx.fillStyle = '#0d1520';
        ctx.fillRect(2, 2, w - 4, h - 4);
        ctx.strokeStyle = '#1e2d3f';
        ctx.strokeRect(2, 2, w - 4, h - 4);

        const rangeMs = 20;
        const pixelsPerMs = (h - 20) / (rangeMs * 2);
        const centerY = h / 2;

        for (let val = speed - 20; val <= speed + 20; val += 5) {
            if (val < 0) continue;
            const py = centerY - (val - speed) * pixelsPerMs;
            if (py < 5 || py > h - 5) continue;

            ctx.strokeStyle = val % 10 === 0 ? '#fff' : '#555';
            ctx.lineWidth = val % 10 === 0 ? 1 : 0.5;
            const lw = val % 10 === 0 ? 25 : 15;
            ctx.beginPath();
            ctx.moveTo(cx - lw, py);
            ctx.lineTo(cx + lw, py);
            ctx.stroke();
            if (val % 10 === 0) {
                ctx.fillStyle = '#fff';
                ctx.font = '10px monospace';
                ctx.textAlign = 'right';
                ctx.fillText(`${val}`, cx - lw - 2, py + 4);
            }
        }

        // 速度色彩带
        const greenLimit = maxSpeed * 0.7;
        const yellowLimit = maxSpeed * 0.9;
        if (speed <= greenLimit) ctx.fillStyle = '#00ff88';
        else if (speed <= yellowLimit) ctx.fillStyle = '#ffd700';
        else ctx.fillStyle = '#ff4757';

        ctx.fillRect(cx - 30, centerY - 12, 60, 24);
        ctx.strokeStyle = '#00d4ff';
        ctx.strokeRect(cx - 30, centerY - 12, 60, 24);

        ctx.fillStyle = speed > yellowLimit ? '#ff4757' : speed > greenLimit ? '#ffd700' : '#00ff88';
        ctx.font = 'bold 16px monospace';
        ctx.textAlign = 'center';
        ctx.fillText(`${Math.round(speed)}`, cx, centerY + 5);

        ctx.fillStyle = '#8b9bb4';
        ctx.font = '8px sans-serif';
        ctx.fillText('m/s', cx, h - 5);
    }

    // ============ 航向指示器（罗盘） ============
    _drawHeading(uav) {
        const g = this._gauges.heading;
        if (!g) return;
        const ctx = g.ctx, w = g.canvas.width, h = g.canvas.height;
        const cx = w / 2, cy = h / 2, r = Math.min(w, h) / 2 - 10;

        ctx.clearRect(0, 0, w, h);

        const heading = uav.attitude?.yaw || 0;

        // 外圈
        ctx.strokeStyle = '#333';
        ctx.lineWidth = 4;
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.stroke();

        // 罗盘刻度（旋转）
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(-heading * Math.PI / 180);

        for (let deg = 0; deg < 360; deg += 5) {
            const rad = deg * Math.PI / 180;
            const outer = r - 8;
            let inner;
            if (deg % 30 === 0) inner = r - 28;  // 主刻度
            else if (deg % 10 === 0) inner = r - 20;  // 次刻度
            else inner = r - 14;  // 小刻度

            ctx.strokeStyle = deg % 30 === 0 ? '#fff' : '#555';
            ctx.lineWidth = deg % 30 === 0 ? 1.5 : 0.5;
            ctx.beginPath();
            ctx.moveTo(Math.cos(rad) * outer, Math.sin(rad) * outer);
            ctx.lineTo(Math.cos(rad) * inner, Math.sin(rad) * inner);
            ctx.stroke();

            if (deg % 30 === 0) {
                const labels = { 0: 'N', 90: 'E', 180: 'S', 270: 'W' };
                const label = labels[deg] || `${deg / 10}`;
                ctx.fillStyle = '#fff';
                ctx.font = '11px monospace';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                const lr = r - 38;
                ctx.fillText(label, Math.cos(rad) * lr, Math.sin(rad) * lr);
            }
        }
        ctx.restore();

        // 固定指北箭头（顶部）
        ctx.fillStyle = '#ff4757';
        ctx.beginPath();
        ctx.moveTo(cx, cy - r + 12);
        ctx.lineTo(cx - 8, cy - r - 5);
        ctx.lineTo(cx + 8, cy - r - 5);
        ctx.closePath();
        ctx.fill();

        // 飞机符号（中心）
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(cx - 25, cy);
        ctx.lineTo(cx - 8, cy);
        ctx.moveTo(cx + 8, cy);
        ctx.lineTo(cx + 25, cy);
        ctx.moveTo(cx, cy);
        ctx.lineTo(cx, cy - 15);
        ctx.stroke();

        // 中心点
        ctx.fillStyle = '#fff';
        ctx.beginPath();
        ctx.arc(cx, cy, 3, 0, Math.PI * 2);
        ctx.fill();

        // 数字航向
        ctx.fillStyle = '#00ff88';
        ctx.font = 'bold 22px monospace';
        ctx.textAlign = 'center';
        ctx.fillText(`${Math.round(heading)}°`, cx, cy + 35);
    }

    // ============ 电池电量表 ============
    _drawBattery(uav) {
        const g = this._gauges.battery;
        if (!g) return;
        const ctx = g.ctx, w = g.canvas.width, h = g.canvas.height;
        const cx = w / 2, cy = h / 2, r = Math.min(w, h) / 2 - 10;

        ctx.clearRect(0, 0, w, h);

        const battery = Math.max(0, uav.battery || 0);

        // 弧线背景
        ctx.strokeStyle = '#333';
        ctx.lineWidth = 12;
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0.75 * Math.PI, 2.25 * Math.PI);
        ctx.stroke();

        // 电量弧线
        const color = battery > 50 ? '#00ff88' : battery > 20 ? '#ffd700' : '#ff4757';
        const sweep = battery / 100 * 1.5 * Math.PI;
        ctx.strokeStyle = color;
        ctx.lineWidth = 12;
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0.75 * Math.PI, 0.75 * Math.PI + sweep);
        ctx.stroke();

        // 中心读数
        ctx.fillStyle = '#fff';
        ctx.font = 'bold 28px monospace';
        ctx.textAlign = 'center';
        ctx.fillText(`${Math.round(battery)}`, cx, cy + 8);

        // 百分比符号
        ctx.fillStyle = '#8b9bb4';
        ctx.font = '14px monospace';
        ctx.fillText('%', cx + 30, cy);

        // 剩余时间估算
        const remainingMin = battery > 0 ? Math.round(battery / 5) : 0;  // 简单估算：每tick 0.05%, 10FPS
        ctx.fillStyle = '#8b9bb4';
        ctx.font = '9px sans-serif';
        ctx.fillText(`≈${remainingMin}min`, cx, cy + 25);
    }

    // ============ GPS信号强度 ============
    _drawGPS(uav) {
        const g = this._gauges.gps;
        if (!g) return;
        const ctx = g.ctx, w = g.canvas.width, h = g.canvas.height;

        ctx.clearRect(0, 0, w, h);

        const gps = uav.gps_signal || 100;
        const cx = w / 2;

        // 信号格（5格）
        const barW = 14, gap = 4, startX = cx - (barW * 5 + gap * 4) / 2, baseY = h - 20;
        const levels = [20, 40, 60, 80, 100];

        levels.forEach((lvl, i) => {
            const barH = (i + 1) * 18;
            const x = startX + i * (barW + gap);
            const y = baseY - barH;

            ctx.fillStyle = gps >= lvl ? '#00ff88' : '#1e2d3f';
            ctx.fillRect(x, y, barW, barH);
            ctx.strokeStyle = '#333';
            ctx.lineWidth = 0.5;
            ctx.strokeRect(x, y, barW, barH);
        });

        // GPS文字
        ctx.fillStyle = gps > 60 ? '#00ff88' : gps > 30 ? '#ffd700' : '#ff4757';
        ctx.font = 'bold 10px monospace';
        ctx.textAlign = 'center';
        ctx.fillText('GPS', cx, 16);

        // 数值
        ctx.fillStyle = '#fff';
        ctx.font = 'bold 16px monospace';
        ctx.fillText(`${Math.round(gps)}%`, cx, h - 25);

        // 卫星图标
        ctx.fillStyle = '#8b9bb4';
        ctx.font = '7px sans-serif';
        ctx.fillText(`SAT: ${Math.round(gps / 10)}`, cx, h - 6);
    }

    // ============ 垂直速度表 (VSI) ============
    _drawVS(uav) {
        const g = this._gauges.vs;
        if (!g) return;
        const ctx = g.ctx, w = g.canvas.width, h = g.canvas.height;
        const cx = w / 2;

        ctx.clearRect(0, 0, w, h);

        const vs = uav.velocity?.z || 0;
        const maxVS = 15;  // ±15 m/s

        // 背景
        ctx.fillStyle = '#0d1520';
        ctx.fillRect(2, 2, w - 4, h - 4);
        ctx.strokeStyle = '#1e2d3f';
        ctx.strokeRect(2, 2, w - 4, h - 4);

        const centerY = h / 2;
        const pixelsPerMs = (h - 30) / (maxVS * 2);

        // 刻度
        for (let val = -maxVS; val <= maxVS; val += 2) {
            const py = centerY - val * pixelsPerMs;
            if (py < 5 || py > h - 5) continue;

            ctx.strokeStyle = val === 0 ? '#fff' : '#555';
            ctx.lineWidth = val === 0 ? 1 : 0.5;
            const lw = val % 5 === 0 ? 20 : 10;
            ctx.beginPath();
            ctx.moveTo(cx - lw, py);
            ctx.lineTo(cx + lw, py);
            ctx.stroke();
            if (val % 5 === 0 && val !== 0) {
                ctx.fillStyle = '#8b9bb4';
                ctx.font = '9px monospace';
                ctx.textAlign = 'left';
                ctx.fillText(`${Math.abs(val)}`, cx + lw + 2, py + 3);
            }
        }

        // 垂直速度指针
        const needleY = centerY - vs * pixelsPerMs;
        const needleY_clamped = Math.max(5, Math.min(h - 5, needleY));

        ctx.fillStyle = vs > 0 ? '#00ff88' : vs < 0 ? '#ff4757' : '#8b9bb4';
        ctx.beginPath();
        ctx.moveTo(cx - 22, needleY_clamped);
        ctx.lineTo(cx + 22, needleY_clamped);
        ctx.lineTo(cx + 22, needleY_clamped + (vs >= 0 ? -8 : 8));
        ctx.lineTo(cx - 22, needleY_clamped + (vs >= 0 ? -8 : 8));
        ctx.closePath();
        ctx.fill();

        // 上升/下降箭头
        if (Math.abs(vs) > 0.3) {
            ctx.fillStyle = vs > 0 ? '#00ff88' : '#ff4757';
            ctx.font = '14px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText(vs > 0 ? '▲' : '▼', cx, vs > 0 ? needleY_clamped - 15 : needleY_clamped + 20);
        }

        ctx.fillStyle = '#fff';
        ctx.font = 'bold 12px monospace';
        ctx.textAlign = 'center';
        ctx.fillText(`${vs > 0 ? '+' : ''}${vs.toFixed(1)}`, cx, h - 5);

        ctx.fillStyle = '#8b9bb4';
        ctx.font = '8px sans-serif';
        ctx.fillText('m/s', cx, 14);
    }

    // ============ 遥测面板 ============
    _updateTelemetry(uav) {
        const setVal = (id, val) => {
            const el = this.container?.querySelector(`#${id}`);
            if (el) el.textContent = val;
        };

        setVal('telem-position', `X:${Math.round(uav.position.x)} Y:${Math.round(uav.position.y)} Z:${Math.round(uav.position.z || 200)}`);
        setVal('telem-attitude',
            `R:${(uav.attitude?.roll || 0).toFixed(1)}° P:${(uav.attitude?.pitch || 0).toFixed(1)}° Y:${(uav.attitude?.yaw || 0).toFixed(1)}°`);
        setVal('telem-task', uav.current_task || '无');
        setVal('telem-comm', `发送:${uav.messages_sent} 接收:${uav.messages_received} 邻居:${(uav.neighbors || []).length}`);
        setVal('telem-payload', `${uav.payload?.type || '无'} ${uav.payload?.deployed ? '(已部署)' : ''}`);
    }

    _clearGauges() {
        Object.values(this._gauges).forEach(g => {
            g.ctx.clearRect(0, 0, g.canvas.width, g.canvas.height);
            g.ctx.fillStyle = '#555';
            g.ctx.font = '14px monospace';
            g.ctx.textAlign = 'center';
            g.ctx.fillText('--', g.canvas.width / 2, g.canvas.height / 2);
        });
    }
}

// 全局单例
let instrumentPanel = null;
eventBus.on('init', () => {
    instrumentPanel = new InstrumentPanel('instruments-container');
});
