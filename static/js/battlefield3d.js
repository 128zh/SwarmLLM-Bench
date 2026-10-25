/**
 * 蜂群作战仿真系统 - 战场态势图渲染器
 *
 * 提供：
 * - 多视角渲染（俯视/跟随/FPV/固定）
 * - 战场地图绘制（UAV、目标、通信链路、攻击效果）
 * - 网络拓扑图绘制
 * - 环境效果叠加
 */

class BattlefieldRenderer {
    constructor(mapCanvasId, topoCanvasId) {
        this.mapCanvas = document.getElementById(mapCanvasId);
        this.mapCtx = this.mapCanvas.getContext('2d');
        this.topoCanvas = document.getElementById(topoCanvasId);
        this.topoCtx = this.topoCanvas.getContext('2d');

        // 视角配置
        this.viewMode = 'topdown';    // topdown / follow / fpv / fixed
        this.followUavId = null;
        this.fpvUavId = null;
        this.irMode = false;

        // 动画
        this.animFrame = 0;
        this.animRunning = true;
        this._lastState = null;

        // 粒子系统（天气效果）
        this._particles = [];
        this._initParticles();

        this._bindEvents();
    }

    _bindEvents() {
        eventBus.on('state-update', (state) => { this._lastState = state; });

        eventBus.on('battlefield-pause', () => { this.animRunning = false; });
        eventBus.on('battlefield-resume', () => {
            this.animRunning = true;
            this._renderLoop();
        });

        // 响应窗口大小变化
        window.addEventListener('resize', () => this._resize());
    }

    start() {
        this.animRunning = true;
        this._resize();
        this._renderLoop();
    }

    _resize() {
        const mapBody = this.mapCanvas.parentElement;
        if (mapBody) {
            this.mapCanvas.width = mapBody.clientWidth;
            this.mapCanvas.height = mapBody.clientHeight;
        }
        const topoBody = this.topoCanvas.parentElement;
        if (topoBody) {
            this.topoCanvas.width = topoBody.clientWidth;
            this.topoCanvas.height = topoBody.clientHeight;
        }
    }

    _renderLoop() {
        if (!this.animRunning) return;
        this.animFrame++;
        if (this._lastState) {
            this.render(this._lastState);
        }
        requestAnimationFrame(() => this._renderLoop());
    }

    render(state) {
        this._drawBattleMap(state);
        this._drawNetworkTopology(state);
    }

    // ============ 战场地图渲染 ============
    _drawBattleMap(state) {
        const ctx = this.mapCtx;
        const w = this.mapCanvas.width, h = this.mapCanvas.height;

        // 清空 + 背景
        ctx.fillStyle = '#0a0e1a';
        ctx.fillRect(0, 0, w, h);

        if (this.viewMode === 'fpv' && this.fpvUavId) {
            this._drawFPV(state);
            return;
        }

        // 绘制地形（如果有环境数据）
        this._drawTerrainOverlay(ctx, w, h, state);

        drawGrid(ctx, w, h);

        const { scale, offsetX, offsetY } = getMapTransform(w, h);
        const toScreen = (pos) => worldToScreen(pos, w, h, scale, offsetX, offsetY);

        // 根据视角调整渲染中心
        let cameraPos = null;
        if (this.viewMode === 'follow' && this.followUavId) {
            const uav = state.uavs?.[this.followUavId];
            if (uav && uav.alive) cameraPos = uav.position;
        }

        // 目标防御范围
        Object.values(state.targets || {}).forEach(t => {
            if (t.destroyed) return;
            const sp = toScreen(t.position);
            const defenseR = (t.defense_range || 2000) * scale;
            ctx.strokeStyle = 'rgba(255, 140, 0, 0.15)';
            ctx.fillStyle = 'rgba(255, 140, 0, 0.03)';
            ctx.beginPath();
            ctx.arc(sp.x, sp.y, defenseR, 0, Math.PI * 2);
            ctx.fill();
            ctx.stroke();
        });

        // 通信链路
        const topology = state.network?.topology || {};
        const drawnLinks = new Set();
        Object.entries(topology).forEach(([uid, neighbors]) => {
            const uav1 = state.uavs?.[uid];
            if (!uav1 || !uav1.alive) return;
            const p1 = toScreen(uav1.position);
            neighbors.forEach(nid => {
                const key = [uid, nid].sort().join('-');
                if (drawnLinks.has(key)) return;
                drawnLinks.add(key);
                const uav2 = state.uavs?.[nid];
                if (!uav2 || !uav2.alive) return;
                const p2 = toScreen(uav2.position);
                ctx.strokeStyle = 'rgba(0, 212, 255, 0.2)';
                ctx.lineWidth = 1;
                ctx.beginPath();
                ctx.moveTo(p1.x, p1.y);
                ctx.lineTo(p2.x, p2.y);
                ctx.stroke();
            });
        });

        // 攻击线
        Object.values(state.uavs || {}).forEach(uav => {
            if (!uav.alive || uav.state !== 'ENGAGING') return;
            const target = Object.values(state.targets || {}).find(t =>
                uav.current_task && uav.current_task.includes(t.id)
            );
            if (target && !target.destroyed) {
                const p1 = toScreen(uav.position);
                const p2 = toScreen(target.position);
                ctx.strokeStyle = 'rgba(255, 71, 87, 0.6)';
                ctx.lineWidth = 1.5;
                ctx.setLineDash([5, 3]);
                ctx.beginPath();
                ctx.moveTo(p1.x, p1.y);
                ctx.lineTo(p2.x, p2.y);
                ctx.stroke();
                ctx.setLineDash([]);
            }
        });

        // 目标
        Object.values(state.targets || {}).forEach(t => {
            const sp = toScreen(t.position);
            const healthPct = t.health / t.max_health;

            if (t.destroyed) {
                ctx.strokeStyle = '#555';
                ctx.lineWidth = 2;
                ctx.beginPath();
                ctx.moveTo(sp.x - 8, sp.y - 8);
                ctx.lineTo(sp.x + 8, sp.y + 8);
                ctx.moveTo(sp.x + 8, sp.y - 8);
                ctx.lineTo(sp.x - 8, sp.y + 8);
                ctx.stroke();
            } else {
                const targetColor = this.irMode ? '#ff3300' : '#ff8c00';
                ctx.fillStyle = `rgba(255, 140, 0, ${0.3 + 0.4 * healthPct})`;
                ctx.strokeStyle = targetColor;
                ctx.lineWidth = 2;
                ctx.beginPath();
                ctx.arc(sp.x, sp.y, 12, 0, Math.PI * 2);
                ctx.fill();
                ctx.stroke();

                // 血量环
                ctx.strokeStyle = healthPct > 0.5 ? '#00ff88' : healthPct > 0.2 ? '#ffd700' : '#ff4757';
                ctx.lineWidth = 2;
                ctx.beginPath();
                ctx.arc(sp.x, sp.y, 16, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * healthPct);
                ctx.stroke();

                ctx.fillStyle = targetColor;
                ctx.font = '10px monospace';
                ctx.textAlign = 'center';
                ctx.fillText(t.id, sp.x, sp.y + 28);
                ctx.fillText(`HP:${Math.round(t.health)}`, sp.x, sp.y + 40);
            }
        });

        // UAV
        Object.values(state.uavs || {}).forEach(uav => {
            const sp = toScreen(uav.position);
            const color = this.irMode ? '#ffcc00' : (ROLE_COLORS[uav.role] || '#888');

            if (!uav.alive) {
                ctx.strokeStyle = '#555';
                ctx.lineWidth = 1.5;
                ctx.beginPath();
                ctx.moveTo(sp.x - 6, sp.y - 6);
                ctx.lineTo(sp.x + 6, sp.y + 6);
                ctx.moveTo(sp.x + 6, sp.y - 6);
                ctx.lineTo(sp.x - 6, sp.y + 6);
                ctx.stroke();
                return;
            }

            // Scout detection range
            if (uav.role === 'scout' && uav.state !== 'DAMAGED') {
                ctx.strokeStyle = `rgba(0, 255, 136, 0.08)`;
                ctx.fillStyle = `rgba(0, 255, 136, 0.02)`;
                ctx.beginPath();
                ctx.arc(sp.x, sp.y, (uav.detection_range || 3000) * scale, 0, Math.PI * 2);
                ctx.fill();
                ctx.stroke();
            }

            // UAV三角形（使用attitude的yaw作为朝向）
            const yawRad = uav.attitude ? uav.attitude.yaw * Math.PI / 180 : Math.atan2(uav.velocity?.y || 0, uav.velocity?.x || 1);
            const angle = yawRad;
            ctx.save();
            ctx.translate(sp.x, sp.y);
            ctx.rotate(angle);
            ctx.fillStyle = color;
            ctx.strokeStyle = uav.is_leader ? '#a855f7' : color;
            ctx.lineWidth = uav.is_leader ? 2 : 1;
            ctx.beginPath();
            ctx.moveTo(8, 0);
            ctx.lineTo(-5, -5);
            ctx.lineTo(-5, 5);
            ctx.closePath();
            ctx.fill();
            ctx.stroke();
            ctx.restore();

            // 领导者光环
            if (uav.is_leader) {
                ctx.strokeStyle = '#a855f7';
                ctx.lineWidth = 1.5;
                ctx.beginPath();
                ctx.arc(sp.x, sp.y, 12, 0, Math.PI * 2);
                ctx.stroke();
                ctx.fillStyle = '#a855f7';
                ctx.font = '10px sans-serif';
                ctx.textAlign = 'center';
                ctx.fillText('★', sp.x, sp.y - 14);
            }

            // 受损闪烁
            if (uav.state === 'DAMAGED') {
                ctx.strokeStyle = '#ffd700';
                ctx.lineWidth = 1;
                ctx.setLineDash([3, 2]);
                ctx.beginPath();
                ctx.arc(sp.x, sp.y, 10, 0, Math.PI * 2);
                ctx.stroke();
                ctx.setLineDash([]);
            }

            // 手动模式标识
            if (uav.state === 'MANUAL') {
                ctx.strokeStyle = '#00d4ff';
                ctx.lineWidth = 1.5;
                ctx.setLineDash([4, 2]);
                ctx.beginPath();
                ctx.arc(sp.x, sp.y, 13, 0, Math.PI * 2);
                ctx.stroke();
                ctx.setLineDash([]);
            }

            // 血量条
            const barW = 20;
            const healthPct = Math.max(0, uav.health) / 100;
            ctx.fillStyle = '#333';
            ctx.fillRect(sp.x - barW / 2, sp.y + 10, barW, 2);
            ctx.fillStyle = healthPct > 0.5 ? '#00ff88' : healthPct > 0.2 ? '#ffd700' : '#ff4757';
            ctx.fillRect(sp.x - barW / 2, sp.y + 10, barW * healthPct, 2);

            // ID标签
            ctx.fillStyle = color;
            ctx.font = '9px monospace';
            ctx.textAlign = 'center';
            ctx.fillText(uav.id, sp.x, sp.y - 10);
        });

        // ====== 绘制拦截器 (Interceptor Swarm) ======
        const interceptors = state.interceptors;
        if (interceptors && interceptors.units) {
            Object.values(interceptors.units).forEach(intr => {
                if (!intr.alive) return;
                const sp = toScreen(intr.position);
                const color = '#ff3333';  // 红色 = 威胁

                // 拦截器探测范围
                if (intr.state === 'PATROL') {
                    ctx.strokeStyle = 'rgba(255, 50, 50, 0.06)';
                    ctx.fillStyle = 'rgba(255, 50, 50, 0.015)';
                } else if (intr.state === 'PURSUING' || intr.state === 'DETECTED') {
                    ctx.strokeStyle = 'rgba(255, 100, 0, 0.12)';
                    ctx.fillStyle = 'rgba(255, 100, 0, 0.03)';
                } else if (intr.state === 'ENGAGING') {
                    ctx.strokeStyle = 'rgba(255, 0, 0, 0.2)';
                    ctx.fillStyle = 'rgba(255, 0, 0, 0.06)';
                }
                ctx.beginPath();
                ctx.arc(sp.x, sp.y, (intr.detection_range || 3500) * scale, 0, Math.PI * 2);
                ctx.fill();
                ctx.stroke();

                // 拦截器图标（菱形 = 威胁）
                ctx.fillStyle = color;
                ctx.strokeStyle = intr.state === 'ENGAGING' ? '#ff0000' : color;
                ctx.lineWidth = intr.state === 'ENGAGING' ? 2 : 1;
                ctx.beginPath();
                ctx.moveTo(sp.x, sp.y - 7);
                ctx.lineTo(sp.x + 5, sp.y);
                ctx.lineTo(sp.x, sp.y + 7);
                ctx.lineTo(sp.x - 5, sp.y);
                ctx.closePath();
                ctx.fill();
                ctx.stroke();

                // 追踪线（锁定目标时）
                if (intr.current_target && state.uavs && state.uavs[intr.current_target]) {
                    const tp = toScreen(state.uavs[intr.current_target].position);
                    ctx.strokeStyle = 'rgba(255, 50, 50, 0.4)';
                    ctx.lineWidth = 1;
                    ctx.setLineDash([4, 3]);
                    ctx.beginPath();
                    ctx.moveTo(sp.x, sp.y);
                    ctx.lineTo(tp.x, tp.y);
                    ctx.stroke();
                    ctx.setLineDash([]);

                    // 威胁连线脉冲动画
                    const pulse = Math.sin(Date.now() / 500) * 0.3 + 0.5;
                    ctx.fillStyle = `rgba(255, 0, 0, ${pulse})`;
                    ctx.beginPath();
                    ctx.arc(tp.x, tp.y, 18, 0, Math.PI * 2);
                    ctx.setLineDash([2, 2]);
                    ctx.strokeStyle = `rgba(255, 0, 0, ${pulse})`;
                    ctx.stroke();
                    ctx.setLineDash([]);
                }

                // 标签
                ctx.fillStyle = color;
                ctx.font = '9px monospace';
                ctx.textAlign = 'center';
                const stateLabels = {
                    PATROL: '巡逻', DETECTED: '发现', PURSUING: '追踪',
                    ENGAGING: '攻击', RETURNING: '返回'
                };
                ctx.fillText(intr.id, sp.x, sp.y + 20);
                ctx.fillText(stateLabels[intr.state] || intr.state, sp.x, sp.y + 32);
            });
        }

        // ====== UAV规避状态标识 ======
        Object.values(state.uavs || {}).forEach(uav => {
            if (!uav.alive || uav.state !== 'EVADING') return;
            const sp = toScreen(uav.position);

            // 规避脉冲光环
            const evadePulse = Math.sin(Date.now() / 300) * 0.4 + 0.6;
            ctx.strokeStyle = `rgba(255, 165, 0, ${evadePulse})`;
            ctx.lineWidth = 2;
            ctx.setLineDash([4, 2]);
            ctx.beginPath();
            ctx.arc(sp.x, sp.y, 16, 0, Math.PI * 2);
            ctx.stroke();
            ctx.setLineDash([]);

            // 规避策略标签
            const stratLabels = { speed: '全速', zigzag: '蛇形', altitude: '变高', split: '分散', terrain: '地形' };
            ctx.fillStyle = '#ffa500';
            ctx.font = '8px monospace';
            ctx.textAlign = 'center';
            const strat = uav.evasion_strategy || 'none';
            if (strat !== 'none') {
                ctx.fillText(stratLabels[strat] || strat, sp.x, sp.y - 16);
            }
        });

        // OODA 监控叠加面板 (左下角)
        if (window._oodaPanel) {
            window._oodaPanel.renderOverlay(ctx, w, h);
        }

        // 视角选择器（叠加在右上角）
        this._drawViewSelector(ctx, w);

        // 图例
        this._drawLegend(ctx, w, h, state);
    }

    // ============ FPV视图 ============
    _drawFPV(state) {
        const ctx = this.mapCtx;
        const w = this.mapCanvas.width, h = this.mapCanvas.height;
        const uav = state.uavs?.[this.fpvUavId];
        if (!uav || !uav.alive) {
            this.viewMode = 'topdown';
            return;
        }

        // 天空渐变
        const skyGrad = ctx.createLinearGradient(0, 0, 0, h * 0.6);
        skyGrad.addColorStop(0, '#1a3a6e');
        skyGrad.addColorStop(1, '#4a8ab5');
        ctx.fillStyle = skyGrad;
        ctx.fillRect(0, 0, w, h * 0.6);

        // 地面渐变
        const gndGrad = ctx.createLinearGradient(0, h * 0.6, 0, h);
        gndGrad.addColorStop(0, '#3a5a2e');
        gndGrad.addColorStop(1, '#1a2a0e');
        ctx.fillStyle = gndGrad;
        ctx.fillRect(0, h * 0.6, w, h * 0.4);

        // 地平线
        const horizonY = h * 0.6 + (uav.attitude?.pitch || 0) * 3;
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(0, horizonY);
        ctx.lineTo(w, horizonY);
        ctx.stroke();

        // 翻滚指示
        const roll = (uav.attitude?.roll || 0) * Math.PI / 180;
        ctx.save();
        ctx.translate(w / 2, horizonY);
        ctx.rotate(roll);
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(-40, 0);
        ctx.lineTo(-10, 0);
        ctx.moveTo(10, 0);
        ctx.lineTo(40, 0);
        ctx.stroke();
        ctx.restore();

        // HUD数据
        ctx.fillStyle = '#00ff88';
        ctx.font = '14px monospace';
        ctx.textAlign = 'left';
        ctx.fillText(`ALT ${Math.round(uav.position.z || uav.position.y * 0.05)}m`, 20, 40);
        ctx.fillText(`SPD ${Math.round(Math.sqrt((uav.velocity?.x||0)**2 + (uav.velocity?.y||0)**2))}m/s`, 20, 60);
        ctx.fillText(`HDG ${Math.round(uav.attitude?.yaw || 0)}°`, 20, 80);
        ctx.textAlign = 'right';
        ctx.fillText(`BAT ${Math.round(uav.battery)}%`, w - 20, 40);
        ctx.fillText(`GPS ${Math.round(uav.gps_signal || 100)}%`, w - 20, 60);

        // 中心十字线
        ctx.strokeStyle = '#00ff88';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(w / 2 - 20, h / 2);
        ctx.lineTo(w / 2 - 8, h / 2);
        ctx.moveTo(w / 2 + 8, h / 2);
        ctx.lineTo(w / 2 + 20, h / 2);
        ctx.moveTo(w / 2, h / 2 - 20);
        ctx.lineTo(w / 2, h / 2 - 8);
        ctx.moveTo(w / 2, h / 2 + 8);
        ctx.lineTo(w / 2, h / 2 + 20);
        ctx.stroke();
        ctx.strokeStyle = '#00ff88';
        ctx.lineWidth = 0.5;
        ctx.beginPath();
        ctx.arc(w / 2, h / 2, 3, 0, Math.PI * 2);
        ctx.stroke();

        // 在FPV中绘制其他UAV为小点（透视投影）
        Object.values(state.uavs || {}).forEach(other => {
            if (other.id === uav.id || !other.alive) return;
            const dx = other.position.x - uav.position.x;
            const dy = other.position.y - uav.position.y;
            const dist = Math.sqrt(dx * dx + dy * dy);
            if (dist < 100) return; // 太近看不到

            const focal = h * 0.5;
            const relAngle = Math.atan2(dy, dx) - (uav.attitude?.yaw || 0) * Math.PI / 180;
            const sx = w / 2 + Math.sin(relAngle) * focal / (dist * 0.01);
            const sy = h / 2 + ((other.position.z || 200) - (uav.position.z || 200)) * focal / (dist * 0.01);

            if (sx > 0 && sx < w && sy > 0 && sy < h) {
                ctx.fillStyle = ROLE_COLORS[other.role] || '#888';
                ctx.beginPath();
                ctx.arc(sx, sy, Math.max(2, 100 / dist), 0, Math.PI * 2);
                ctx.fill();
                ctx.fillStyle = '#fff';
                ctx.font = '8px monospace';
                ctx.textAlign = 'center';
                ctx.fillText(other.id, sx, sy - 8);
            }
        });

        // 退出FPV按钮
        ctx.fillStyle = 'rgba(0,0,0,0.5)';
        ctx.fillRect(w - 100, h - 35, 95, 30);
        ctx.fillStyle = '#fff';
        ctx.font = '11px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('[ESC] 退出FPV', w - 52, h - 15);
    }

    // ============ 地形叠加 ============
    _drawTerrainOverlay(ctx, w, h, state) {
        const env = state.environment;
        if (!env || !env.terrain || !env.terrain.grid) return;

        const grid = env.terrain.grid;
        const rows = grid.length;
        if (rows === 0) return;
        const cols = grid[0].length;
        const cellW = w / cols;
        const cellH = h / rows;

        grid.forEach((row, iy) => {
            row.forEach((cell, ix) => {
                ctx.fillStyle = this._terrainColorToRGBA(cell.color || '#2d4a1e', 0.15);
                ctx.fillRect(ix * cellW, iy * cellH, cellW, cellH);

                // 地形标签（仅在足够大时显示）
                if (cellW > 60 && cellH > 60) {
                    ctx.fillStyle = 'rgba(255,255,255,0.2)';
                    ctx.font = '9px sans-serif';
                    ctx.textAlign = 'center';
                    const labels = { mountain: '山', urban: '城', water: '水', plains: '原' };
                    ctx.fillText(labels[cell.terrain] || '', ix * cellW + cellW / 2, iy * cellH + cellH / 2 + 3);
                }
            });
        });
    }

    _terrainColorToRGBA(hex, alpha) {
        const r = parseInt(hex.slice(1, 3), 16);
        const g = parseInt(hex.slice(3, 5), 16);
        const b = parseInt(hex.slice(5, 7), 16);
        return `rgba(${r},${g},${b},${alpha})`;
    }

    // ============ 视角选择器 ============
    _drawViewSelector(ctx, w) {
        const views = [
            { key: 'topdown', label: '俯视' },
            { key: 'follow', label: '跟随' },
            { key: 'fpv', label: 'FPV' },
            { key: 'fixed', label: '固定' },
        ];
        const bx = w - 190, by = 10, bw = 180, bh = 28;

        ctx.fillStyle = 'rgba(0,0,0,0.6)';
        ctx.fillRect(bx, by, bw, bh);

        views.forEach((v, i) => {
            const vx = bx + 5 + i * 43;
            const active = this.viewMode === v.key;
            ctx.fillStyle = active ? 'rgba(0,212,255,0.4)' : 'rgba(255,255,255,0.08)';
            ctx.fillRect(vx, by + 3, 40, bh - 6);
            ctx.fillStyle = active ? '#00d4ff' : '#8b9bb4';
            ctx.font = '10px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText(v.label, vx + 20, by + bh / 2 + 4);
        });

        // IR toggle
        const irX = bx, irY = by + bh + 4;
        ctx.fillStyle = 'rgba(0,0,0,0.6)';
        ctx.fillRect(irX, irY, bw, bh);
        ctx.fillStyle = this.irMode ? 'rgba(255,50,0,0.4)' : 'rgba(255,255,255,0.08)';
        ctx.fillRect(irX + 5, irY + 3, bw - 10, bh - 6);
        ctx.fillStyle = this.irMode ? '#ff3300' : '#8b9bb4';
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(this.irMode ? 'IR 红外模式 [开]' : 'IR 红外模式 [关]', irX + bw / 2, irY + bh / 2 + 4);
    }

    // ============ 图例 ============
    _drawLegend(ctx, w, h, state) {
        ctx.fillStyle = 'rgba(139, 155, 180, 0.5)';
        ctx.font = '10px monospace';
        ctx.textAlign = 'left';

        const env = state.environment;
        let infoY = h - 10;
        ctx.fillText(`地图: ${MAP_W}m x ${MAP_H}m`, 10, infoY);

        if (env && env.wind) {
            ctx.fillText(`风: ${env.wind.level}级 ${env.wind.speed}m/s ${env.wind.direction}°`, 10, infoY - 14);
            infoY -= 14;
        }
        if (env && env.precipitation && env.precipitation.type !== 'none') {
            ctx.fillText(`${env.precipitation.type === 'rain' ? '雨' : '雪'}: ${Math.round(env.precipitation.intensity * 100)}%`, 10, infoY - 14);
            infoY -= 14;
        }

        ctx.textAlign = 'right';
        ctx.fillText(`Tick: ${state.tick}`, w - 10, h - 10);
    }

    // ============ 网络拓扑图 ============
    _drawNetworkTopology(state) {
        const ctx = this.topoCtx;
        const w = this.topoCanvas.width, h = this.topoCanvas.height;

        ctx.fillStyle = '#0a0e1a';
        ctx.fillRect(0, 0, w, h);

        const uavs = Object.values(state.uavs || {});
        const topology = state.network?.topology || {};

        if (uavs.length === 0) return;

        // 环形布局
        const cx = w / 2, cy = h / 2;
        const radius = Math.min(w, h) / 2 - 40;
        const positions = {};

        uavs.forEach((uav, i) => {
            const angle = (i / uavs.length) * Math.PI * 2 - Math.PI / 2;
            positions[uav.id] = {
                x: cx + Math.cos(angle) * radius,
                y: cy + Math.sin(angle) * radius,
            };
        });

        // 连接线
        const drawnLinks = new Set();
        Object.entries(topology).forEach(([uid, neighbors]) => {
            if (!positions[uid]) return;
            neighbors.forEach(nid => {
                const key = [uid, nid].sort().join('-');
                if (drawnLinks.has(key)) return;
                drawnLinks.add(key);
                if (!positions[nid]) return;

                const p1 = positions[uid], p2 = positions[nid];
                const uav1 = state.uavs?.[uid], uav2 = state.uavs?.[nid];

                ctx.strokeStyle = (uav1 && uav2 && uav1.alive && uav2.alive)
                    ? 'rgba(0, 212, 255, 0.3)' : 'rgba(100, 100, 100, 0.1)';
                ctx.lineWidth = 0.5;
                ctx.beginPath();
                ctx.moveTo(p1.x, p1.y);
                ctx.lineTo(p2.x, p2.y);
                ctx.stroke();
            });
        });

        // 节点
        uavs.forEach(uav => {
            const pos = positions[uav.id];
            if (!pos) return;
            const color = ROLE_COLORS[uav.role] || '#888';

            if (!uav.alive) {
                ctx.fillStyle = '#333';
                ctx.strokeStyle = '#555';
                ctx.lineWidth = 1;
                ctx.beginPath();
                ctx.arc(pos.x, pos.y, 6, 0, Math.PI * 2);
                ctx.fill();
                ctx.stroke();
                ctx.strokeStyle = '#ff4757';
                ctx.beginPath();
                ctx.moveTo(pos.x - 4, pos.y - 4);
                ctx.lineTo(pos.x + 4, pos.y + 4);
                ctx.moveTo(pos.x + 4, pos.y - 4);
                ctx.lineTo(pos.x - 4, pos.y + 4);
                ctx.stroke();
            } else {
                ctx.fillStyle = color;
                ctx.strokeStyle = uav.is_leader ? '#fff' : color;
                ctx.lineWidth = uav.is_leader ? 2 : 1;
                ctx.beginPath();
                ctx.arc(pos.x, pos.y, uav.is_leader ? 10 : 7, 0, Math.PI * 2);
                ctx.fill();
                ctx.stroke();

                if (uav.is_leader) {
                    ctx.strokeStyle = 'rgba(168, 85, 247, 0.4)';
                    ctx.lineWidth = 1;
                    ctx.beginPath();
                    ctx.arc(pos.x, pos.y, 14, 0, Math.PI * 2);
                    ctx.stroke();
                }
                if (uav.state === 'DAMAGED') {
                    ctx.strokeStyle = '#ffd700';
                    ctx.setLineDash([2, 2]);
                    ctx.beginPath();
                    ctx.arc(pos.x, pos.y, 11, 0, Math.PI * 2);
                    ctx.stroke();
                    ctx.setLineDash([]);
                }
            }

            ctx.fillStyle = uav.alive ? color : '#555';
            ctx.font = '9px monospace';
            ctx.textAlign = 'center';
            ctx.fillText(uav.id, pos.x, pos.y + 20);
        });

        // 网络状态信息
        const netStatus = state.network || {};
        ctx.fillStyle = 'rgba(139, 155, 180, 0.6)';
        ctx.font = '10px monospace';
        ctx.textAlign = 'left';
        ctx.fillText(`节点: ${netStatus.alive_nodes || 0}/${netStatus.total_nodes || 0}`, 10, 15);
        ctx.fillText(`链路: ${netStatus.topology_edges || 0}`, 10, 30);
        ctx.fillText(`连通: ${netStatus.network_connected ? '是' : '否'}`, 10, 45);
        ctx.textAlign = 'right';
        ctx.fillText(`重组网: ${netStatus.reconnect_count || 0}次`, w - 10, 15);
    }

    // ============ 粒子系统（天气） ============
    _initParticles() {
        this._particles = [];
        for (let i = 0; i < 200; i++) {
            this._particles.push({
                x: Math.random() * 10000,
                y: Math.random() * 10000,
                speed: 2 + Math.random() * 8,
                length: 3 + Math.random() * 8,
                opacity: 0.1 + Math.random() * 0.4,
            });
        }
    }
}

// 键盘事件：ESC 退出 FPV
window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        if (window._battlefieldRenderer && window._battlefieldRenderer.viewMode === 'fpv') {
            window._battlefieldRenderer.viewMode = 'topdown';
            window._battlefieldRenderer.fpvUavId = null;
        }
    }
});
