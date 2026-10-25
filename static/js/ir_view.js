/**
 * 蜂群作战仿真系统 - 红外热成像视图
 *
 * 在战场地图上叠加红外热成像效果：
 * - 目标显示为热源（红色/橙色）
 * - UAV引擎显示为高亮（黄色）
 * - 背景为冷色调（深蓝）
 * - 模拟IR传感器噪声
 */

class IRViewRenderer {
    constructor() {
        this._enabled = false;
        this._offScreenCanvas = null;
        this._offScreenCtx = null;
        this._noiseCanvas = null;
        this._noiseCtx = null;
        this._bindEvents();
    }

    _bindEvents() {
        eventBus.on('state-update', (state) => {
            // IR模式通过 battlefieldRenderer.irMode 控制
            // 此渲染器提供后处理能力
        });
    }

    /**
     * 对Canvas像素应用IR后处理
     * @param {CanvasRenderingContext2D} ctx - 目标Canvas上下文
     * @param {number} w - 宽度
     * @param {number} h - 高度
     */
    applyIRFilter(ctx, w, h) {
        // 读取像素
        const imageData = ctx.getImageData(0, 0, w, h);
        const data = imageData.data;

        // 应用IR热力映射
        for (let i = 0; i < data.length; i += 4) {
            const r = data[i];
            const g = data[i + 1];
            const b = data[i + 2];

            // 亮度计算
            const brightness = 0.299 * r + 0.587 * g + 0.114 * b;

            // IR热力映射：冷色调 -> 暖色调
            // 低亮度：深蓝/黑（冷背景）
            // 中亮度：绿/黄（温暖区域）
            // 高亮度：橙/红/白（热源）
            let irR, irG, irB;

            if (brightness < 64) {
                // 冷区：深蓝
                const t = brightness / 64;
                irR = Math.round(0 + t * 0);
                irG = Math.round(0 + t * 20);
                irB = Math.round(40 + t * 100);
            } else if (brightness < 128) {
                // 温区：绿->黄
                const t = (brightness - 64) / 64;
                irR = Math.round(0 + t * 200);
                irG = Math.round(60 + t * 140);
                irB = Math.round(120 - t * 100);
            } else if (brightness < 192) {
                // 热区：黄->橙->红
                const t = (brightness - 128) / 64;
                irR = Math.round(200 + t * 55);
                irG = Math.round(180 - t * 120);
                irB = Math.round(20 - t * 20);
            } else {
                // 极热区：红->白
                const t = (brightness - 192) / 63;
                irR = Math.round(255);
                irG = Math.round(60 + t * 195);
                irB = Math.round(0 + t * 255);
            }

            // 添加传感器噪声
            const noise = (Math.random() - 0.5) * 15;

            data[i] = Math.max(0, Math.min(255, irR + noise));
            data[i + 1] = Math.max(0, Math.min(255, irG + noise));
            data[i + 2] = Math.max(0, Math.min(255, irB + noise));
            // Alpha不变
        }

        ctx.putImageData(imageData, 0, 0);
    }

    /**
     * 高亮特定区域（目标/UAV热源）
     */
    highlightHotSpot(ctx, x, y, radius, intensity = 1.0) {
        const gradient = ctx.createRadialGradient(x, y, 0, x, y, radius);
        gradient.addColorStop(0, `rgba(255, ${Math.round(100 + 155 * intensity)}, 0, ${0.8 * intensity})`);
        gradient.addColorStop(0.3, `rgba(255, ${Math.round(50 + 100 * intensity)}, 0, ${0.5 * intensity})`);
        gradient.addColorStop(0.7, `rgba(255, 20, 0, ${0.2 * intensity})`);
        gradient.addColorStop(1, 'rgba(0, 0, 100, 0)');
        ctx.fillStyle = gradient;
        ctx.beginPath();
        ctx.arc(x, y, radius, 0, Math.PI * 2);
        ctx.fill();
    }
}

// 全局单例
let irViewRenderer = null;
eventBus.on('init', () => {
    irViewRenderer = new IRViewRenderer();
    window._irViewRenderer = irViewRenderer;
});
