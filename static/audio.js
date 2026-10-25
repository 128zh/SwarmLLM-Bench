/**
 * 3D 空间音效引擎 — SwarmAudio
 * =================================
 * 基于 Web Audio API 的实时战场音效系统。
 * 无需任何音频文件，全部声音程序化合成。
 *
 * 音效层次：
 * ┌─────────────────────────────────────────┐
 * │  Layer 1: 环境层 (Ambient)               │
 * │  - 战场风声、低频背景                     │
 * ├─────────────────────────────────────────┤
 * │  Layer 2: 引擎层 (Engine)                │
 * │  - 每架 UAV 独立引擎声 (3D 空间化)        │
 * │  - 距离衰减 + 多普勒效应                  │
 * ├─────────────────────────────────────────┤
 * │  Layer 3: 战斗层 (Combat)                │
 * │  - 武器开火 (噪声爆发)                    │
 * │  - 爆炸 (低频冲击)                        │
 * │  - 命中反馈                              │
 * ├─────────────────────────────────────────┤
 * │  Layer 4: 告警层 (Alert)                 │
 * │  - UAV 受损警告                          │
 * │  - UAV 摧毁音效                          │
 * │  - 任务完成提示                           │
 * ├─────────────────────────────────────────┤
 * │  Layer 5: 通信层 (Comm)                  │
 * │  - 消息收发提示音                         │
 * │  - 网络重组告警                          │
 * └─────────────────────────────────────────┘
 */

class SwarmAudio {
    constructor() {
        this.ctx = null;
        this.masterGain = null;
        this.listener = null;

        // UAV 引擎音源追踪
        this.engineSources = {};    // uav_id -> {osc, gain, panner}
        this.activeUAVs = new Set();

        // 配置
        this.mapScale = 1.0;        // 仿真坐标 → 音频坐标缩放
        this.mapWidth = 10000;
        this.mapHeight = 10000;
        this.audioWorldSize = 200;  // 音频空间尺寸 (Web Audio 推荐范围)

        // 状态追踪 (用于检测变化)
        this.prevState = null;

        // 音量
        this.volumes = {
            master: 0.7,
            ambient: 0.15,
            engine: 0.25,
            weapon: 0.6,
            explosion: 0.8,
            alert: 0.5,
            comm: 0.3,
        };
    }

    // ==================== 初始化 ====================

    init() {
        if (this.ctx) return; // 已初始化

        try {
            this.ctx = new (window.AudioContext || window.webkitAudioContext)();
        } catch (e) {
            console.warn('[SwarmAudio] Web Audio API not available:', e.message);
            return;
        }

        // 主控增益
        this.masterGain = this.ctx.createGain();
        this.masterGain.gain.value = this.volumes.master;
        this.masterGain.connect(this.ctx.destination);

        // 监听器 (默认在原点)
        this.listener = this.ctx.listener;
        this._setListenerPosition(0, 0, 50);

        // 启动子层
        this._initAmbientLayer();
        this._initCombatLayer();
        this._initAlertLayer();
        this._initCommLayer();

        console.log('[SwarmAudio] 3D spatial audio engine initialized');
        console.log('[SwarmAudio] Sample rate:', this.ctx.sampleRate, 'Hz');
        console.log('[SwarmAudio] State:', this.ctx.state);
    }

    /** 用户交互后恢复 AudioContext */
    resume() {
        if (this.ctx && this.ctx.state === 'suspended') {
            this.ctx.resume();
            console.log('[SwarmAudio] AudioContext resumed');
        }
    }

    // ==================== 环境层 ====================

    _initAmbientLayer() {
        // 风声 (滤波噪声)
        const bufferSize = 2 * this.ctx.sampleRate;
        const noiseBuffer = this.ctx.createBuffer(1, bufferSize, this.ctx.sampleRate);
        const data = noiseBuffer.getChannelData(0);
        for (let i = 0; i < bufferSize; i++) {
            // 布朗噪声 (更自然的"风声")
            data[i] = (Math.random() * 2 - 1) * 0.02;
            if (i > 0) {
                data[i] = data[i - 1] * 0.98 + data[i] * 0.02;
            }
        }

        this.ambientNoise = this.ctx.createBufferSource();
        this.ambientNoise.buffer = noiseBuffer;
        this.ambientNoise.loop = true;

        // 低频滤波器模拟风声
        const ambientFilter = this.ctx.createBiquadFilter();
        ambientFilter.type = 'lowpass';
        ambientFilter.frequency.value = 300;
        ambientFilter.Q.value = 0.5;

        this.ambientGain = this.ctx.createGain();
        this.ambientGain.gain.value = this.volumes.ambient;

        this.ambientNoise.connect(ambientFilter);
        ambientFilter.connect(this.ambientGain);
        this.ambientGain.connect(this.masterGain);
        this.ambientNoise.start();

        // 低频背景震动
        this.subBass = this.ctx.createOscillator();
        this.subBass.type = 'sine';
        this.subBass.frequency.value = 28; // 次声波范围

        const subGain = this.ctx.createGain();
        subGain.gain.value = 0.04;

        this.subBass.connect(subGain);
        subGain.connect(this.masterGain);
        this.subBass.start();

        console.log('[SwarmAudio] Ambient layer ready (wind + sub-bass)');
    }

    // ==================== 战斗层 ====================

    _initCombatLayer() {
        this._combatNodes = {
            weaponFilter: null,
            explosionFilter: null,
        };
    }

    /** 武器开火音效 — 空间化噪波爆发 */
    playWeaponFire(uavPosition) {
        if (!this.ctx) return;

        const now = this.ctx.currentTime;

        // 噪声爆发
        const bufferSize = Math.floor(this.ctx.sampleRate * 0.08); // 80ms
        const noiseBuffer = this.ctx.createBuffer(1, bufferSize, this.ctx.sampleRate);
        const data = noiseBuffer.getChannelData(0);
        for (let i = 0; i < bufferSize; i++) {
            const t = i / bufferSize;
            data[i] = (Math.random() * 2 - 1) * Math.exp(-t * 20);
        }

        const source = this.ctx.createBufferSource();
        source.buffer = noiseBuffer;

        // 带通滤波器 (枪声特征)
        const filter = this.ctx.createBiquadFilter();
        filter.type = 'bandpass';
        filter.frequency.value = 800 + Math.random() * 400;
        filter.Q.value = 0.8;

        // 空间定位
        const panner = this.ctx.createPanner();
        panner.panningModel = 'HRTF';
        panner.distanceModel = 'inverse';
        panner.refDistance = 5;
        panner.maxDistance = 200;
        panner.rolloffFactor = 1.5;
        this._setPannerPosition(panner, uavPosition);

        // 增益包络
        const gain = this.ctx.createGain();
        gain.gain.setValueAtTime(this.volumes.weapon, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.08);

        source.connect(filter);
        filter.connect(gain);
        gain.connect(panner);
        panner.connect(this.masterGain);

        source.start(now);
        source.stop(now + 0.1);

        // 低频冲击 (增强打击感)
        this._playImpact(now, 60, 0.3, uavPosition);
    }

    /** 爆炸音效 */
    playExplosion(targetPosition, intensity = 1.0) {
        if (!this.ctx) return;

        const now = this.ctx.currentTime;

        // 低频主爆炸
        const duration = 0.6 + intensity * 0.4;
        this._playImpact(now, 35 + intensity * 15, this.volumes.explosion * intensity, targetPosition, duration);

        // 碎片噪声层
        const bufferSize = Math.floor(this.ctx.sampleRate * duration);
        const noiseBuffer = this.ctx.createBuffer(1, bufferSize, this.ctx.sampleRate);
        const data = noiseBuffer.getChannelData(0);
        for (let i = 0; i < bufferSize; i++) {
            const t = i / bufferSize;
            data[i] = (Math.random() * 2 - 1) * Math.exp(-t * (3 + intensity * 2));
        }

        const source = this.ctx.createBufferSource();
        source.buffer = noiseBuffer;

        const filter = this.ctx.createBiquadFilter();
        filter.type = 'lowpass';
        filter.frequency.value = 500 + intensity * 300;

        const panner = this.ctx.createPanner();
        panner.panningModel = 'HRTF';
        panner.distanceModel = 'inverse';
        panner.refDistance = 8;
        panner.maxDistance = 300;
        panner.rolloffFactor = 1.0;
        this._setPannerPosition(panner, targetPosition);

        const gain = this.ctx.createGain();
        gain.gain.setValueAtTime(this.volumes.explosion * 0.6 * intensity, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + duration);

        source.connect(filter);
        filter.connect(gain);
        gain.connect(panner);
        panner.connect(this.masterGain);

        source.start(now);
        source.stop(now + duration + 0.1);
    }

    /** 低频冲击波 (内部辅助) */
    _playImpact(now, freq, vol, position, duration = 0.4) {
        const osc = this.ctx.createOscillator();
        osc.type = 'sine';
        osc.frequency.setValueAtTime(freq, now);
        osc.frequency.exponentialRampToValueAtTime(15, now + duration);

        const panner = this.ctx.createPanner();
        panner.panningModel = 'HRTF';
        panner.distanceModel = 'inverse';
        panner.refDistance = 10;
        panner.maxDistance = 300;
        this._setPannerPosition(panner, position);

        const gain = this.ctx.createGain();
        gain.gain.setValueAtTime(vol, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + duration);

        osc.connect(gain);
        gain.connect(panner);
        panner.connect(this.masterGain);

        osc.start(now);
        osc.stop(now + duration + 0.05);
    }

    // ==================== 告警层 ====================

    _initAlertLayer() {
        this._lastAlertTime = 0;
    }

    /** UAV 受损告警 */
    playDamageAlert(uavPosition) {
        if (!this.ctx) return;
        const now = this.ctx.currentTime;
        if (now - this._lastAlertTime < 0.3) return; // 防抖
        this._lastAlertTime = now;

        // 警告音 (双音交替)
        [800, 600].forEach((freq, i) => {
            const osc = this.ctx.createOscillator();
            osc.type = 'square';
            osc.frequency.value = freq;

            const panner = this.ctx.createPanner();
            panner.panningModel = 'HRTF';
            panner.distanceModel = 'inverse';
            panner.refDistance = 5;
            panner.maxDistance = 200;
            this._setPannerPosition(panner, uavPosition);

            const gain = this.ctx.createGain();
            const t = now + i * 0.08;
            gain.gain.setValueAtTime(0, t);
            gain.gain.setValueAtTime(this.volumes.alert * 0.3, t + 0.005);
            gain.gain.exponentialRampToValueAtTime(0.001, t + 0.08);

            osc.connect(gain);
            gain.connect(panner);
            panner.connect(this.masterGain);

            osc.start(t);
            osc.stop(t + 0.1);
        });
    }

    /** UAV 摧毁音效 */
    playUAVDestroyed(uavPosition) {
        if (!this.ctx) return;
        const now = this.ctx.currentTime;

        // 下降音调 + 爆炸
        const osc = this.ctx.createOscillator();
        osc.type = 'sawtooth';
        osc.frequency.setValueAtTime(400, now);
        osc.frequency.exponentialRampToValueAtTime(40, now + 0.6);

        const panner = this.ctx.createPanner();
        panner.panningModel = 'HRTF';
        panner.distanceModel = 'inverse';
        panner.refDistance = 8;
        panner.maxDistance = 300;
        this._setPannerPosition(panner, uavPosition);

        const gain = this.ctx.createGain();
        gain.gain.setValueAtTime(this.volumes.alert, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.6);

        osc.connect(gain);
        gain.connect(panner);
        panner.connect(this.masterGain);

        osc.start(now);
        osc.stop(now + 0.7);

        // 附带小型爆炸
        this.playExplosion(uavPosition, 0.4);
    }

    /** 任务完成 */
    playMissionComplete() {
        if (!this.ctx) return;
        const now = this.ctx.currentTime;

        // 胜利和弦 (大三和弦: C-E-G)
        [523, 659, 784].forEach((freq, i) => {
            const osc = this.ctx.createOscillator();
            osc.type = 'sine';
            osc.frequency.value = freq;

            const gain = this.ctx.createGain();
            const t = now + i * 0.15;
            gain.gain.setValueAtTime(0, t);
            gain.gain.linearRampToValueAtTime(0.2, t + 0.05);
            gain.gain.exponentialRampToValueAtTime(0.001, t + 0.6);

            osc.connect(gain);
            gain.connect(this.masterGain);

            osc.start(t);
            osc.stop(t + 0.7);
        });
    }

    /** 网络重组告警 */
    playReconnect() {
        if (!this.ctx) return;
        const now = this.ctx.currentTime;

        // 快速上升音
        const osc = this.ctx.createOscillator();
        osc.type = 'triangle';
        osc.frequency.setValueAtTime(200, now);
        osc.frequency.linearRampToValueAtTime(600, now + 0.15);

        const gain = this.ctx.createGain();
        gain.gain.setValueAtTime(0.15, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.2);

        osc.connect(gain);
        gain.connect(this.masterGain);

        osc.start(now);
        osc.stop(now + 0.25);
    }

    // ==================== 通信层 ====================

    _initCommLayer() {
        this._commBeepCount = 0;
        this._lastCommTime = 0;
    }

    /** 通信活动提示音 (轻微滴答声) */
    playCommBeep(senderRole) {
        if (!this.ctx) return;
        const now = this.ctx.currentTime;
        // 限制频率，避免过于密集
        if (now - this._lastCommTime < 0.05) return;
        this._lastCommTime = now;
        this._commBeepCount++;

        // 每 10 次才播放一次 (防止过密)
        if (this._commBeepCount % 10 !== 0) return;

        const roleFreq = {
            leader: 1200, scout: 900, striker: 600, relay: 1500,
        };
        const freq = roleFreq[senderRole] || 800;

        const osc = this.ctx.createOscillator();
        osc.type = 'sine';
        osc.frequency.value = freq;

        const gain = this.ctx.createGain();
        gain.gain.setValueAtTime(0, now);
        gain.gain.setValueAtTime(this.volumes.comm * 0.3, now + 0.002);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.025);

        osc.connect(gain);
        gain.connect(this.masterGain);

        osc.start(now);
        osc.stop(now + 0.03);
    }

    // ==================== UAV 引擎层 (3D 空间化) ====================

    /**
     * 更新所有 UAV 的引擎音效
     * 每帧调用，根据 UAV 位置动态调整 3D 音源
     */
    updateEngineSounds(state) {
        if (!this.ctx) return;

        const uavs = Object.values(state.uavs || {});
        const currentIDs = new Set();

        uavs.forEach(uav => {
            if (!uav.alive) return;
            currentIDs.add(uav.id);

            if (!this.engineSources[uav.id]) {
                this._createEngineSource(uav);
            }
            this._updateEngineSource(uav);
        });

        // 移除已摧毁 UAV 的引擎声
        Object.keys(this.engineSources).forEach(id => {
            if (!currentIDs.has(id)) {
                this._removeEngineSource(id);
            }
        });

        this.activeUAVs = currentIDs;

        // 同步更新监听器到地图中心上方
        this._setListenerPosition(
            this.mapWidth / 2 * this.mapScale,
            this.mapHeight / 2 * this.mapScale,
            100
        );
    }

    /** 为一架 UAV 创建引擎音源 */
    _createEngineSource(uav) {
        const ctx = this.ctx;

        // 主引擎 — 低频振荡器
        const osc1 = ctx.createOscillator();
        osc1.type = 'sawtooth';
        osc1.frequency.value = 55; // 基础频率

        // 次谐波
        const osc2 = ctx.createOscillator();
        osc2.type = 'triangle';
        osc2.frequency.value = 110;

        // 噪声 (气流声)
        const bufferSize = ctx.sampleRate * 2;
        const noiseBuffer = ctx.createBuffer(1, bufferSize, ctx.sampleRate);
        const data = noiseBuffer.getChannelData(0);
        for (let i = 0; i < bufferSize; i++) {
            data[i] = (Math.random() * 2 - 1) * 0.1;
        }
        const noiseSource = ctx.createBufferSource();
        noiseSource.buffer = noiseBuffer;
        noiseSource.loop = true;

        // 低通滤波 (模拟引擎沉闷感)
        const filter = ctx.createBiquadFilter();
        filter.type = 'lowpass';
        filter.frequency.value = 200;
        filter.Q.value = 1.0;

        // 3D 空间定位
        const panner = ctx.createPanner();
        panner.panningModel = 'HRTF';
        panner.distanceModel = 'inverse';
        panner.refDistance = 10;
        panner.maxDistance = 200;
        panner.rolloffFactor = 1.2;

        // 增益
        const gain = ctx.createGain();
        gain.gain.value = this.volumes.engine;

        // 连接
        osc1.connect(filter);
        osc2.connect(filter);
        noiseSource.connect(filter);
        filter.connect(gain);
        gain.connect(panner);
        panner.connect(this.masterGain);

        osc1.start();
        osc2.start();
        noiseSource.start();

        this.engineSources[uav.id] = {
            osc1, osc2, noise: noiseSource,
            filter, gain, panner,
        };

        // 基于角色调整音色
        const roleFreqShift = { leader: 1.1, scout: 1.3, striker: 0.9, relay: 0.8 };
        const shift = roleFreqShift[uav.role] || 1.0;
        osc1.frequency.value = 55 * shift;
        osc2.frequency.value = 110 * shift;
    }

    /** 更新一架 UAV 的引擎音源位置和状态 */
    _updateEngineSource(uav) {
        const src = this.engineSources[uav.id];
        if (!src) return;

        const ctx = this.ctx;
        const pos = uav.position;

        // 更新 3D 位置
        this._setPannerPosition(src.panner, pos);

        // 速度影响音调 (多普勒效果)
        const speed = Math.sqrt(
            (uav.velocity.x || 0) ** 2 + (uav.velocity.y || 0) ** 2
        );
        const speedNorm = Math.min(speed / 80, 1); // 归一化
        src.osc1.frequency.setTargetAtTime(
            55 * (1 + speedNorm * 0.3) * (uav.role === 'scout' ? 1.3 : 1.0),
            ctx.currentTime, 0.1
        );

        // 受损时引擎声不稳定
        if (uav.state === 'DAMAGED') {
            src.osc1.frequency.setTargetAtTime(
                55 * (1 + Math.random() * 0.1),
                ctx.currentTime, 0.05
            );
            src.filter.frequency.setTargetAtTime(120, ctx.currentTime, 0.05);
            src.gain.gain.setTargetAtTime(this.volumes.engine * 0.6, ctx.currentTime, 0.1);
        } else {
            src.filter.frequency.setTargetAtTime(200, ctx.currentTime, 0.1);
            src.gain.gain.setTargetAtTime(this.volumes.engine, ctx.currentTime, 0.1);
        }

        // 交战状态 — 引擎加速
        if (uav.state === 'ENGAGING') {
            src.osc1.frequency.setTargetAtTime(70, ctx.currentTime, 0.05);
            src.gain.gain.setTargetAtTime(this.volumes.engine * 1.3, ctx.currentTime, 0.05);
        }
    }

    /** 移除 UAV 引擎音源 */
    _removeEngineSource(uavId) {
        const src = this.engineSources[uavId];
        if (!src) return;

        // 渐变静音
        const now = this.ctx.currentTime;
        src.gain.gain.exponentialRampToValueAtTime(0.001, now + 0.5);

        setTimeout(() => {
            try {
                src.osc1.stop();
                src.osc2.stop();
                src.noise.stop();
            } catch (e) { /* 可能已停止 */ }
        }, 600);

        delete this.engineSources[uavId];
    }

    // ==================== 辅助方法 ====================

    /** 将仿真坐标映射为音频空间坐标 */
    _mapToAudioSpace(simPos) {
        const scale = this.audioWorldSize / Math.max(this.mapWidth, this.mapHeight);
        return {
            x: (simPos.x || 0) * scale - this.audioWorldSize / 2,
            y: (simPos.y || 0) * scale - this.audioWorldSize / 2,
            z: Math.max(0.5, (simPos.z || 100) * scale * 0.5),
        };
    }

    /** 设置 PannerNode 的 3D 位置 */
    _setPannerPosition(panner, simPos) {
        if (!simPos) return;
        const ap = this._mapToAudioSpace(simPos);
        if (panner.positionX) {
            panner.positionX.value = ap.x;
            panner.positionY.value = ap.z; // Y-up in audio
            panner.positionZ.value = ap.y;
        } else {
            panner.setPosition(ap.x, ap.z, ap.y);
        }
    }

    /** 设置监听器位置 */
    _setListenerPosition(x, y, z) {
        if (!this.listener) return;
        const ap = this._mapToAudioSpace({ x, y, z });
        if (this.listener.positionX) {
            this.listener.positionX.value = ap.x;
            this.listener.positionY.value = ap.z;
            this.listener.positionZ.value = ap.y;
            this.listener.forwardX.value = 0;
            this.listener.forwardY.value = -1;
            this.listener.forwardZ.value = 0;
            this.listener.upX.value = 0;
            this.listener.upY.value = 0;
            this.listener.upZ.value = 1;
        } else {
            this.listener.setPosition(ap.x, ap.z, ap.y);
            this.listener.setOrientation(0, -1, 0, 0, 0, 1);
        }
    }

    /** 静音总开关 */
    setMasterVolume(vol) {
        this.volumes.master = Math.max(0, Math.min(1, vol));
        if (this.masterGain) {
            this.masterGain.gain.setTargetAtTime(this.volumes.master, this.ctx.currentTime, 0.1);
        }
    }

    /** 静音/取消静音 */
    toggleMute() {
        if (this._muted) {
            this.setMasterVolume(this._preMuteVolume || 0.7);
            this._muted = false;
        } else {
            this._preMuteVolume = this.volumes.master;
            this.setMasterVolume(0);
            this._muted = true;
        }
        return this._muted;
    }

    /** 销毁所有音源 */
    destroy() {
        Object.keys(this.engineSources).forEach(id => this._removeEngineSource(id));

        if (this.ambientNoise) {
            try { this.ambientNoise.stop(); } catch (e) {}
        }
        if (this.subBass) {
            try { this.subBass.stop(); } catch (e) {}
        }

        if (this.ctx) {
            this.ctx.close();
            this.ctx = null;
        }
        console.log('[SwarmAudio] Engine destroyed');
    }
}


// ============ 全局实例 ============
const swarmAudio = new SwarmAudio();
