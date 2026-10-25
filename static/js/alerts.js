/**
 * 蜂群作战仿真系统 - 告警通知系统
 *
 * 提供分级告警：
 * - Critical (红色)：通信中断、UAV摧毁、碰撞警告
 * - Warning (黄色)：低电量<20%、低血量<30%、GPS信号弱
 * - Info (蓝色)：目标发现、任务完成、领导者变更
 */

class AlertSystem {
    constructor() {
        this._container = null;
        this._activeAlerts = [];
        this._alertIdCounter = 0;
        this._prevState = null;
        this._alertCooldowns = {};  // 防止同一告警频繁触发
        this._buildUI();
        this._bindEvents();
    }

    _buildUI() {
        this._container = document.createElement('div');
        this._container.id = 'alert-container';
        this._container.className = 'alert-container';
        document.body.appendChild(this._container);
    }

    _bindEvents() {
        eventBus.on('state-update', (state) => {
            this.check(state);
            this._prevState = state;
        });
    }

    check(state) {
        if (!this._prevState) return;

        // 检测各类告警条件
        this._checkUavDestroyed(state);
        this._checkLowBattery(state);
        this._checkLowHealth(state);
        this._checkCommLoss(state);
        this._checkTargetFound(state);
        this._checkMissionComplete(state);
        this._checkLeaderChange(state);
        this._checkNetworkSplit(state);
    }

    _checkUavDestroyed(state) {
        const prevUavs = this._prevState?.uavs || {};
        Object.values(state.uavs || {}).forEach(uav => {
            const prev = prevUavs[uav.id];
            if (prev && prev.alive && !uav.alive) {
                this._alert(`UAV ${uav.id} (${ROLE_NAMES[uav.role] || uav.role}) 已摧毁！`, 'critical', 8000);
            }
        });
    }

    _checkLowBattery(state) {
        Object.values(state.uavs || {}).forEach(uav => {
            if (!uav.alive) return;
            const prev = this._prevState?.uavs?.[uav.id];
            if (uav.battery <= 20 && prev && prev.battery > 20) {
                this._alert(`UAV ${uav.id} 电量不足 (${Math.round(uav.battery)}%)`, 'warning', 5000);
            }
        });
    }

    _checkLowHealth(state) {
        Object.values(state.uavs || {}).forEach(uav => {
            if (!uav.alive) return;
            const prev = this._prevState?.uavs?.[uav.id];
            if (uav.health <= 30 && prev && prev.health > 30) {
                this._alert(`UAV ${uav.id} 严重受损 (HP:${Math.round(uav.health)})`, 'warning', 5000);
            }
        });
    }

    _checkCommLoss(state) {
        const prevNetStatus = this._prevState?.network || {};
        const netStatus = state.network || {};
        if (!prevNetStatus.network_connected && netStatus.network_connected) {
            // do nothing - was already disconnected
        } else if (prevNetStatus.network_connected === true && netStatus.network_connected === false) {
            this._alert('蜂群网络通信中断！网络已分割', 'critical', 10000);
        }
    }

    _checkTargetFound(state) {
        const prevTargets = this._prevState?.targets || {};
        Object.values(state.targets || {}).forEach(t => {
            const prev = prevTargets[t.id];
            if (prev && !prev.destroyed && t.destroyed) {
                this._alert(`目标 ${t.id} 已被摧毁！`, 'info', 4000);
            }
        });
    }

    _checkMissionComplete(state) {
        const prevStatus = this._prevState?.mission?.mission_status;
        const curStatus = state.mission?.mission_status;
        if (prevStatus !== 'completed' && curStatus === 'completed') {
            this._alert('任务完成！所有目标已摧毁', 'info', 8000);
        }
    }

    _checkLeaderChange(state) {
        const prevLeader = this._prevState?.network?.leader_id;
        const curLeader = state.network?.leader_id;
        if (prevLeader && curLeader && prevLeader !== curLeader) {
            this._alert(`领导者变更: ${prevLeader} → ${curLeader}`, 'info', 4000);
        }
    }

    _checkNetworkSplit(state) {
        const prevSplits = this._prevState?.network?.network_splits || 0;
        const curSplits = state.network?.network_splits || 0;
        if (curSplits > prevSplits) {
            this._alert(`检测到网络分割！(第${curSplits}次)`, 'critical', 8000);
        }
    }

    _alert(message, level = 'info', duration = 5000) {
        // 冷却检查：相同消息5秒内不重复
        const now = Date.now();
        const key = message.substring(0, 30);
        if (this._alertCooldowns[key] && now - this._alertCooldowns[key] < 5000) return;
        this._alertCooldowns[key] = now;

        const id = `alert-${++this._alertIdCounter}`;
        const icons = { critical: '🔴', warning: '⚠️', info: 'ℹ️' };
        const el = document.createElement('div');
        el.className = `alert-toast alert-${level}`;
        el.id = id;
        el.innerHTML = `
            <span class="alert-icon">${icons[level]}</span>
            <span class="alert-msg">${message}</span>
            <button class="alert-close" onclick="this.parentElement.remove()">×</button>
        `;

        this._container.appendChild(el);
        this._activeAlerts.push({ id, el, level });

        // 进场动画
        requestAnimationFrame(() => el.classList.add('alert-visible'));

        // 自动消失（critical级别不自动消失）
        if (level !== 'critical') {
            setTimeout(() => {
                el.classList.remove('alert-visible');
                setTimeout(() => el.remove(), 300);
            }, duration);
        }

        // 限制最多显示5条
        if (this._activeAlerts.length > 5) {
            const oldest = this._activeAlerts.shift();
            if (oldest && oldest.el.parentElement) {
                oldest.el.classList.remove('alert-visible');
                setTimeout(() => oldest.el.remove(), 300);
            }
        }

        // 声音提示（可选）
        this._playSound(level);
    }

    _playSound(level) {
        // 使用 Web Audio API 生成简单提示音
        try {
            const ctx = new (window.AudioContext || window.webkitAudioContext)();
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();

            osc.connect(gain);
            gain.connect(ctx.destination);

            const freqs = { critical: 880, warning: 660, info: 440 };
            osc.frequency.value = freqs[level] || 440;
            osc.type = 'sine';

            gain.gain.setValueAtTime(0.1, ctx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + (level === 'critical' ? 0.4 : 0.15));

            osc.start(ctx.currentTime);
            osc.stop(ctx.currentTime + 0.5);
        } catch (e) {
            // 静默忽略（浏览器可能阻止音频）
        }
    }

    // 手动触发告警（供其他模块使用）
    notify(message, level = 'info', duration = 5000) {
        this._alert(message, level, duration);
    }
}

// 全局单例
let alertSystem = null;
eventBus.on('init', () => {
    alertSystem = new AlertSystem();
});
