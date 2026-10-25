/**
 * 蜂群作战仿真系统 - 核心状态管理
 *
 * 提供：
 * - WebSocket 连接管理（自动重连）
 * - 全局状态存储
 * - 事件总线（发布/订阅）
 * - 标签页路由
 * - 共享工具函数和常量
 */

// ============ 全局常量 ============
const MAP_W = 10000, MAP_H = 10000;  // 仿真地图尺寸（米）

const ROLE_COLORS = {
    leader: '#a855f7',
    scout: '#00ff88',
    striker: '#ff4757',
    relay: '#ffd700',
};
const ROLE_NAMES = {
    leader: '指挥机',
    scout: '侦察机',
    striker: '攻击机',
    relay: '中继机',
};

const STATE_NAMES = {
    INIT: '初始化', IDLE: '待命', MOVING: '移动中',
    SCOUTING: '侦察中', ENGAGING: '交战中', RETREATING: '撤退中',
    RELAYING: '中继', DAMAGED: '受损', DESTROYED: '摧毁',
    MANUAL: '手动',
};

const MISSION_STATUS_NAMES = {
    pending: '待命', active: '执行中', completed: '已完成', failed: '失败',
};

// ============ 事件总线 ============
class EventBus {
    constructor() {
        this._listeners = {};
    }

    on(event, callback) {
        if (!this._listeners[event]) this._listeners[event] = [];
        this._listeners[event].push(callback);
    }

    off(event, callback) {
        if (!this._listeners[event]) return;
        this._listeners[event] = this._listeners[event].filter(cb => cb !== callback);
    }

    emit(event, data) {
        if (!this._listeners[event]) return;
        for (const cb of this._listeners[event]) {
            try { cb(data); } catch (e) { console.error('[EventBus]', event, e); }
        }
    }
}

const eventBus = new EventBus();

// ============ 全局状态存储 ============
const stateStore = {
    current: null,
    previous: null,
    connected: false,
    latency_ms: 0,
};

// ============ WebSocket 管理 ============
let ws = null;
let wsReconnectTimer = null;
let wsUrl = null;

function getWsUrl() {
    if (wsUrl) return wsUrl;
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    wsUrl = `${protocol}//${location.host}/ws`;
    return wsUrl;
}

function connectWebSocket() {
    const url = getWsUrl();
    ws = new WebSocket(url);

    ws.onopen = () => {
        stateStore.connected = true;
        eventBus.emit('connection-change', 'connected');
        if (wsReconnectTimer) {
            clearTimeout(wsReconnectTimer);
            wsReconnectTimer = null;
        }
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            stateStore.previous = stateStore.current;
            stateStore.current = data;

            // 调度到所有模块
            eventBus.emit('state-update', data);

            // 调度到活动标签页
            const activeTab = getActiveTab();
            if (activeTab) {
                eventBus.emit(`tab-update:${activeTab}`, data);
            }

            // 始终更新顶部栏
            updateTopBar(data);
        } catch (e) {
            console.error('[WS] Parse error:', e);
        }
    };

    ws.onclose = () => {
        stateStore.connected = false;
        eventBus.emit('connection-change', 'disconnected');
        // 自动重连
        wsReconnectTimer = setTimeout(connectWebSocket, 2000);
    };

    ws.onerror = () => {
        if (ws) ws.close();
    };
}

function sendCommand(cmd, extra = {}) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ cmd, ...extra }));
        return true;
    }
    return false;
}

function sendRest(method, path, body = null) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body) opts.body = JSON.stringify(body);
    return fetch(path, opts).then(r => r.json()).catch(e => console.error('[REST]', path, e));
}

// ============ 标签页路由 ============
function getActiveTab() {
    const active = document.querySelector('.tab-btn.active');
    return active ? active.dataset.tab : null;
}

function switchTab(tabName) {
    // 更新标签按钮
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tabName);
    });
    // 更新内容面板
    document.querySelectorAll('.tab-content').forEach(panel => {
        panel.classList.toggle('active', panel.id === `tab-${tabName}`);
    });
    // 通知
    eventBus.emit('tab-switch', tabName);
    // 如果切换到非战场标签，暂停战场动画以节省资源
    if (tabName !== 'battlefield') {
        eventBus.emit('battlefield-pause');
    } else {
        eventBus.emit('battlefield-resume');
    }
}

function setupTabNavigation() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => switchTab(btn.dataset.tab));
    });
}

// ============ 顶部状态栏（始终可见） ============
function updateTopBar(state) {
    const tickEl = document.getElementById('tick');
    const elapsedEl = document.getElementById('elapsed');
    const missionEl = document.getElementById('mission-status');
    const connEl = document.getElementById('connection-status');

    if (tickEl) tickEl.textContent = state.tick;
    if (elapsedEl) elapsedEl.textContent = (state.elapsed_time || 0) + 's';

    if (missionEl) {
        const status = state.mission?.mission_status || 'pending';
        missionEl.textContent = MISSION_STATUS_NAMES[status] || status;
        missionEl.className = `badge badge-${status}`;
    }

    if (connEl && stateStore.connected) {
        connEl.textContent = '已连接';
        connEl.className = 'status-item connected';
    }
}

// ============ 连接状态变化 ============
eventBus.on('connection-change', (status) => {
    const el = document.getElementById('connection-status');
    if (!el) return;
    if (status === 'connected') {
        el.textContent = '已连接';
        el.className = 'status-item connected';
    } else if (status === 'connecting') {
        el.textContent = '连接中...';
        el.className = 'status-item';
    } else {
        el.textContent = '已断开';
        el.className = 'status-item disconnected';
    }
});

// ============ 坐标转换工具 ============
function worldToScreen(pos, canvasW, canvasH, scale = null, offsetX = null, offsetY = null) {
    if (scale === null) {
        const scaleX = canvasW / MAP_W;
        const scaleY = canvasH / MAP_H;
        scale = Math.min(scaleX, scaleY);
    }
    if (offsetX === null) offsetX = (canvasW - MAP_W * scale) / 2;
    if (offsetY === null) offsetY = (canvasH - MAP_H * scale) / 2;
    return {
        x: offsetX + pos.x * scale,
        y: offsetY + pos.y * scale,
    };
}

function getMapTransform(canvasW, canvasH) {
    const scaleX = canvasW / MAP_W;
    const scaleY = canvasH / MAP_H;
    const scale = Math.min(scaleX, scaleY);
    return {
        scale,
        offsetX: (canvasW - MAP_W * scale) / 2,
        offsetY: (canvasH - MAP_H * scale) / 2,
    };
}

// ============ Canvas 工具 ============
function drawGrid(ctx, w, h, gridSize = 50) {
    ctx.strokeStyle = 'rgba(30, 45, 63, 0.5)';
    ctx.lineWidth = 0.5;
    for (let x = 0; x < w; x += gridSize) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, h);
        ctx.stroke();
    }
    for (let y = 0; y < h; y += gridSize) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
        ctx.stroke();
    }
}

// ============ 初始化入口 ============
window.addEventListener('DOMContentLoaded', () => {
    setupTabNavigation();
    connectWebSocket();
    eventBus.emit('init');
});
