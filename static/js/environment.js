/**
 * 蜂群作战仿真系统 - 环境效果渲染器
 *
 * 提供：
 * - 天气粒子效果（雨/雪）叠加在战场地图上
 * - 风力可视化（流线、方向指示）
 * - 光照覆盖（黄昏/夜间半透明遮罩）
 */

class EnvironmentRenderer {
    constructor() {
        this._particles = [];
        this._windParticles = [];
        this._initParticles();
        this._bindEvents();
    }

    _initParticles() {
        // 雨滴粒子
        this._particles = [];
        for (let i = 0; i < 300; i++) {
            this._particles.push({
                x: Math.random() * 10000,
                y: Math.random() * 10000,
                speed: 200 + Math.random() * 500,
                length: 10 + Math.random() * 20,
                opacity: 0.1 + Math.random() * 0.4,
            });
        }
        // 风流线粒子
        this._windParticles = [];
        for (let i = 0; i < 30; i++) {
            this._windParticles.push({
                x: Math.random() * 10000,
                y: Math.random() * 10000,
                life: Math.random() * 100,
                maxLife: 100 + Math.random() * 200,
            });
        }
    }

    _bindEvents() {
        eventBus.on('state-update', (state) => this._lastState = state);
        this._lastState = null;
    }

    /**
     * 在战场Canvas上叠加天气效果
     * @param {CanvasRenderingContext2D} ctx - 战场Canvas上下文
     * @param {number} w - Canvas宽度
     * @param {number} h - Canvas高度
     * @param {object} state - 仿真状态
     * @param {number} scale - 坐标缩放
     * @param {number} offsetX - X偏移
     * @param {number} offsetY - Y偏移
     */
    renderWeather(ctx, w, h, state, scale, offsetX, offsetY) {
        const env = state.environment;
        if (!env) return;

        const precip = env.precipitation || { type: 'none', intensity: 0 };
        const wind = env.wind || { level: 0, speed: 0, direction: 0 };
        const lighting = env.lighting || { type: 'day', ambient: 1.0 };

        // 更新并渲染粒子
        if (precip.type === 'rain' && precip.intensity > 0) {
            this._renderRain(ctx, w, h, scale, offsetX, offsetY, precip.intensity, wind);
        } else if (precip.type === 'snow' && precip.intensity > 0) {
            this._renderSnow(ctx, w, h, scale, offsetX, offsetY, precip.intensity, wind);
        }

        // 风力方向指示器
        if (wind.level > 0) {
            this._renderWindIndicator(ctx, w, h, wind);
        }

        // 光照覆盖
        if (lighting.type === 'dusk') {
            ctx.fillStyle = 'rgba(180, 120, 60, 0.2)';
            ctx.fillRect(0, 0, w, h);
        } else if (lighting.type === 'night') {
            ctx.fillStyle = 'rgba(10, 15, 40, 0.55)';
            ctx.fillRect(0, 0, w, h);
        }
    }

    _renderRain(ctx, w, h, scale, offsetX, offsetY, intensity, wind) {
        const count = Math.floor(intensity * 200);
        const windRad = wind.direction * Math.PI / 180;
        const windDrift = wind.speed * 3;

        ctx.strokeStyle = `rgba(180, 210, 240, ${0.15 + intensity * 0.2})`;
        ctx.lineWidth = 1;

        for (let i = 0; i < count; i++) {
            let p = this._particles[i];
            if (!p) continue;

            // 世界坐标 -> 屏幕坐标
            const sx = offsetX + p.x * scale;
            const sy = offsetY + p.y * scale;

            // 雨滴：向下 + 风偏移
            const ex = sx + Math.cos(windRad) * windDrift * 0.5 + Math.cos(windRad + 0.3) * 2;
            const ey = sy + p.length * (0.5 + intensity);

            if (sx > -10 && sx < w + 10 && sy > -10 && sy < h + 10) {
                ctx.beginPath();
                ctx.moveTo(sx, sy);
                ctx.lineTo(ex, ey);
                ctx.stroke();
            }

            // 更新粒子位置
            p.y += p.speed * 0.05;
            p.x += Math.cos(windRad) * windDrift * 0.02;
            if (p.y > 10000) { p.y = 0; p.x = Math.random() * 10000; }
        }
    }

    _renderSnow(ctx, w, h, scale, offsetX, offsetY, intensity, wind) {
        const count = Math.floor(intensity * 150);
        const windRad = wind.direction * Math.PI / 180;
        const windDrift = wind.speed * 1.5;

        ctx.fillStyle = `rgba(255, 255, 255, ${0.2 + intensity * 0.3})`;

        for (let i = 0; i < count; i++) {
            let p = this._particles[i];
            if (!p) continue;

            const sx = offsetX + p.x * scale;
            const sy = offsetY + p.y * scale;

            if (sx > -5 && sx < w + 5 && sy > -5 && sy < h + 5) {
                const size = 1.5 + p.opacity * 2;
                ctx.beginPath();
                ctx.arc(sx, sy, size, 0, Math.PI * 2);
                ctx.fill();
            }

            // 雪花飘落 + 摇摆
            p.y += p.speed * 0.02;
            p.x += Math.cos(windRad) * windDrift * 0.01 + Math.sin(p.y * 0.01) * 0.5;
            if (p.y > 10000) { p.y = 0; p.x = Math.random() * 10000; }
        }
    }

    _renderWindIndicator(ctx, w, h, wind) {
        const ix = w - 60, iy = 50;

        // 背景圆
        ctx.fillStyle = 'rgba(0,0,0,0.5)';
        ctx.beginPath();
        ctx.arc(ix, iy, 30, 0, Math.PI * 2);
        ctx.fill();

        // 风向箭头（旋转）
        const windRad = (wind.direction - 90) * Math.PI / 180;
        ctx.save();
        ctx.translate(ix, iy);
        ctx.rotate(windRad);

        ctx.strokeStyle = '#00d4ff';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(0, -20);
        ctx.lineTo(0, 15);
        ctx.stroke();
        // 箭头
        ctx.fillStyle = '#00d4ff';
        ctx.beginPath();
        ctx.moveTo(0, -22);
        ctx.lineTo(-6, -14);
        ctx.lineTo(6, -14);
        ctx.closePath();
        ctx.fill();

        ctx.restore();

        // 风力等级文字
        ctx.fillStyle = '#fff';
        ctx.font = 'bold 12px monospace';
        ctx.textAlign = 'center';
        ctx.fillText(`${wind.level}级`, ix, iy + 5);
        ctx.fillText(`${wind.speed.toFixed(1)}m/s`, ix, iy + 45);
    }

    /**
     * 渲染地形叠加（在地图Canvas上）
     */
    renderTerrain(ctx, w, h, state, scale, offsetX, offsetY) {
        const env = state.environment;
        if (!env || !env.terrain || !env.terrain.grid) return;

        const grid = env.terrain.grid;
        const rows = grid.length;
        if (rows === 0) return;
        const cols = grid[0].length;
        const cellW = w / cols;
        const cellH = h / rows;

        const terrainAlpha = { mountain: 0.12, urban: 0.08, water: 0.1, plains: 0.05 };

        grid.forEach((row, iy) => {
            row.forEach((cell, ix) => {
                const color = cell.color || '#2d4a1e';
                const alpha = terrainAlpha[cell.terrain] || 0.05;
                const r = parseInt(color.slice(1, 3), 16);
                const g = parseInt(color.slice(3, 5), 16);
                const b = parseInt(color.slice(5, 7), 16);
                ctx.fillStyle = `rgba(${r},${g},${b},${alpha})`;
                ctx.fillRect(ix * cellW, iy * cellH, cellW + 1, cellH + 1);
            });
        });
    }
}

// 全局单例
let environmentRenderer = null;
eventBus.on('init', () => {
    environmentRenderer = new EnvironmentRenderer();
});
