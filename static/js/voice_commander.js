/**
 * 语音指挥与播报系统 (Voice Commander)
 * ========================================
 *
 * 三大功能:
 * 1. 语音命令输入 (STT - Speech-to-Text)
 *    基于 Web Speech API 的 SpeechRecognition
 *    支持中英文命令，实时转写
 *
 * 2. 语音态势播报 (TTS - Text-to-Speech)
 *    基于 Web Speech API 的 SpeechSynthesis
 *    OODA状态 / 威胁告警 / 任务简报 / 战术建议
 *
 * 3. 环境音效 (Web Audio API)
 *    引擎背景音 / 告警音 / 爆炸音 / 锁定音
 *    3D空间化音频 (距UAV越近声音越大)
 *
 * 使用:
 *    const vc = new VoiceCommander();
 *    vc.start();  // 启动语音系统
 *    vc.speak("警告: UAV_03 受到拦截器攻击");  // 语音播报
 *    vc.startListening();  // 开始听命令
 */

class VoiceCommander {
    constructor() {
        // STT
        this.recognition = null;
        this.isListening = false;
        this.sttLang = 'zh-CN';
        this.transcript = '';
        this.interimTranscript = '';

        // TTS
        this.synth = window.speechSynthesis;
        this.speaking = false;
        this.ttsQueue = [];
        this.ttsVoice = null;
        this.ttsVolume = 0.8;
        this.ttsRate = 1.0;
        this.ttsPitch = 1.0;

        // 环境音效
        this.audioCtx = null;
        this.sounds = {};
        this.masterGain = null;
        this.sfxVolume = 0.3;
        this.ambientVolume = 0.08;

        // 状态
        this.enabled = false;
        this.muted = false;
        this.lastAlertTick = 0;
        this.alertCooldownMs = 5000;  // 同类型告警冷却5秒
        this.alertHistory = {};

        // 回调
        this.onCommand = null;   // (text) => void
        this.onStatus = null;    // (status) => void

        this._initRecognition();
        this._initAudio();
        this._initVoices();
        this._bindEvents();
    }

    // ============ 初始化 ============

    _initRecognition() {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognition) {
            console.warn('[Voice] SpeechRecognition not supported in this browser');
            return;
        }

        this.recognition = new SpeechRecognition();
        this.recognition.lang = this.sttLang;
        this.recognition.continuous = true;
        this.recognition.interimResults = true;
        this.recognition.maxAlternatives = 1;

        this.recognition.onresult = (event) => {
            let interim = '';
            let final = '';
            for (let i = event.resultIndex; i < event.results.length; i++) {
                const result = event.results[i];
                if (result.isFinal) {
                    final += result[0].transcript;
                } else {
                    interim += result[0].transcript;
                }
            }
            this.interimTranscript = interim;
            if (final) {
                this.transcript = final.trim();
                this._onVoiceCommand(final.trim());
            }

            // 实时转写回调
            eventBus.emit('voice-transcript', {
                interim: this.interimTranscript,
                final: final.trim(),
            });
        };

        this.recognition.onerror = (event) => {
            console.warn('[Voice] Recognition error:', event.error);
            if (event.error === 'no-speech') {
                // 静默重启
                if (this.isListening) {
                    setTimeout(() => {
                        try { this.recognition.start(); } catch(e) {}
                    }, 500);
                }
            }
        };

        this.recognition.onend = () => {
            if (this.isListening) {
                // 自动重启（continuous模式下通常不需要，但做安全处理）
                try { this.recognition.start(); } catch(e) {}
            }
        };
    }

    _initAudio() {
        try {
            this.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            this.masterGain = this.audioCtx.createGain();
            this.masterGain.gain.value = 0.5;
            this.masterGain.connect(this.audioCtx.destination);

            // 预生成音效
            this._createEngineHum();
            this._createAlertSounds();
        } catch(e) {
            console.warn('[Voice] Web Audio not supported');
        }
    }

    _initVoices() {
        // TTS语音列表加载
        const loadVoices = () => {
            const voices = this.synth.getVoices();
            // 优先中文女声
            this.ttsVoice = voices.find(v => v.lang.startsWith('zh') && v.name.includes('Female'))
                         || voices.find(v => v.lang.startsWith('zh'))
                         || voices.find(v => v.lang.startsWith('en') && v.name.includes('Female'))
                         || voices[0];
        };
        loadVoices();
        if (this.synth.onvoiceschanged !== undefined) {
            this.synth.onvoiceschanged = loadVoices;
        }
    }

    _bindEvents() {
        eventBus.on('state-update', (state) => this._onStateUpdate(state));
    }

    // ============ 音效生成 ============

    _createEngineHum() {
        if (!this.audioCtx) return;
        // 持续的低频引擎嗡嗡声
        const osc = this.audioCtx.createOscillator();
        const gain = this.audioCtx.createGain();
        const filter = this.audioCtx.createBiquadFilter();

        osc.type = 'sawtooth';
        osc.frequency.value = 55;  // 低频嗡嗡
        filter.type = 'lowpass';
        filter.frequency.value = 200;
        gain.gain.value = 0;

        osc.connect(filter);
        filter.connect(gain);
        gain.connect(this.masterGain);

        // 存储引用
        this.sounds.engineHum = { osc, gain, filter };
        this._engineHumRunning = false;
    }

    _createAlertSounds() {
        if (!this.audioCtx) return;

        // 生成各种告警音效
        const createBeep = (freq, duration, type = 'sine') => {
            return () => {
                const osc = this.audioCtx.createOscillator();
                const gain = this.audioCtx.createGain();
                osc.type = type;
                osc.frequency.value = freq;
                gain.gain.setValueAtTime(0.3, this.audioCtx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.001, this.audioCtx.currentTime + duration);
                osc.connect(gain);
                gain.connect(this.masterGain);
                osc.start();
                osc.stop(this.audioCtx.currentTime + duration);
            };
        };

        this.sounds.beepCritical = createBeep(880, 0.3, 'square');
        this.sounds.beepWarning = createBeep(660, 0.2, 'sine');
        this.sounds.beepInfo = createBeep(440, 0.15, 'sine');
        this.sounds.beepTarget = createBeep(1200, 0.25, 'triangle');
        this.sounds.beepLock = createBeep(1500, 0.4, 'sawtooth');

        // 爆炸音（噪声+低频脉冲）
        this.sounds.explosion = () => {
            const bufferSize = this.audioCtx.sampleRate * 0.5;
            const buffer = this.audioCtx.createBuffer(1, bufferSize, this.audioCtx.sampleRate);
            const data = buffer.getChannelData(0);
            for (let i = 0; i < bufferSize; i++) {
                data[i] = (Math.random() * 2 - 1) * Math.exp(-i / (this.audioCtx.sampleRate * 0.05));
            }
            const source = this.audioCtx.createBufferSource();
            const gain = this.audioCtx.createGain();
            gain.gain.setValueAtTime(0.5, this.audioCtx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.001, this.audioCtx.currentTime + 0.5);
            source.buffer = buffer;
            source.connect(gain);
            gain.connect(this.masterGain);
            source.start();
        };
    }

    // ============ 公开API ============

    start() {
        this.enabled = true;
        if (this.audioCtx && this.audioCtx.state === 'suspended') {
            this.audioCtx.resume();
        }
        this._startEngineHum();
        this.speak('蜂群仿真语音系统已就绪');
        eventBus.emit('voice-status', { status: 'ready', listening: this.isListening });
    }

    stop() {
        this.enabled = false;
        this.stopListening();
        this._stopEngineHum();
        this.synth.cancel();
        this.ttsQueue = [];
        eventBus.emit('voice-status', { status: 'stopped', listening: false });
    }

    // ============ STT: 语音命令输入 ============

    startListening() {
        if (!this.recognition || this.isListening) return;
        try {
            this.recognition.start();
            this.isListening = true;
            eventBus.emit('voice-status', { status: 'listening', listening: true });
        } catch(e) {
            console.warn('[Voice] Cannot start recognition:', e);
        }
    }

    stopListening() {
        if (!this.recognition || !this.isListening) return;
        try {
            this.recognition.stop();
        } catch(e) {}
        this.isListening = false;
        eventBus.emit('voice-status', { status: 'ready', listening: false });
    }

    toggleListening() {
        if (this.isListening) {
            this.stopListening();
        } else {
            this.startListening();
        }
    }

    setLanguage(lang) {
        this.sttLang = lang;
        if (this.recognition) {
            this.recognition.lang = lang;
        }
    }

    _onVoiceCommand(text) {
        console.log('[Voice] Command:', text);
        eventBus.emit('voice-command', text);

        // 发送到NL命令API
        sendRest('POST', '/api/nl/command', { command: text })
            .then(result => {
                if (result && result.response_text) {
                    this.speak(result.response_text);
                }
                if (result && result.intent && result.intent !== 'unknown') {
                    eventBus.emit('voice-command-parsed', result);
                }
            })
            .catch(() => {
                // API不可用时本地处理
                this._localCommandParse(text);
            });
    }

    _localCommandParse(text) {
        const lower = text.toLowerCase();

        // 中英文命令关键词匹配
        if (/侦察|scout|search/i.test(lower)) {
            sendCommand('scout');
            this.speak('收到，开始侦察');
        } else if (/攻击|strike|engage|attack/i.test(lower)) {
            sendCommand('start');
            this.speak('收到，发起攻击');
        } else if (/撤退|retreat|withdraw|fall back/i.test(lower)) {
            sendCommand('retreat');
            this.speak('收到，执行撤退');
        } else if (/暂停|pause|halt|stop/i.test(lower)) {
            sendCommand('pause');
            this.speak('仿真已暂停');
        } else if (/恢复|resume|continue/i.test(lower)) {
            sendCommand('start');
            this.speak('仿真已恢复');
        } else if (/汇报|report|status|situation/i.test(lower)) {
            this._speakSitrep();
        } else {
            this.speak('收到命令: ' + text.substring(0, 30));
        }
    }

    // ============ TTS: 语音播报 ============

    speak(text, priority = 'normal') {
        if (!this.enabled || this.muted || !this.synth) return;

        // 高优先级直接插队
        if (priority === 'high') {
            this.synth.cancel();
            this.ttsQueue = [];
        }

        this.ttsQueue.push({ text, priority });
        this._processTTSQueue();
    }

    _processTTSQueue() {
        if (this.speaking || this.ttsQueue.length === 0) return;
        if (!this.synth) return;

        const item = this.ttsQueue.shift();
        this.speaking = true;

        const utterance = new SpeechSynthesisUtterance(item.text);
        utterance.voice = this.ttsVoice;
        utterance.volume = this.ttsVolume;
        utterance.rate = this.ttsRate;
        utterance.pitch = this.ttsPitch;
        utterance.lang = this.sttLang;

        utterance.onend = () => {
            this.speaking = false;
            setTimeout(() => this._processTTSQueue(), 100);
        };

        utterance.onerror = () => {
            this.speaking = false;
            setTimeout(() => this._processTTSQueue(), 200);
        };

        this.synth.speak(utterance);
    }

    _speakSitrep() {
        const state = stateStore.current;
        if (!state) {
            this.speak('暂无态势数据');
            return;
        }
        const s = state.summary || {};
        const text = `态势报告。Tick ${state.tick}。`
            + `存活无人机${s.alive_uavs || 0}架，损失${s.destroyed_uavs || 0}架。`
            + `已摧毁目标${s.destroyed_targets || 0}个。`;
        this.speak(text, 'high');
    }

    // ============ 智能语音播报触发器 ============

    _onStateUpdate(state) {
        if (!this.enabled) return;

        const tick = state.tick || 0;

        // OODA状态播报（每50 tick）
        const ooda = state.ooda || {};
        if (ooda.inside_enemy_loop && tick - this._lastOodaAnnounce > 50) {
            this._lastOodaAnnounce = tick;
            if (ooda.ooda_advantage > 0.3) {
                this.speak('已进入敌方OODA循环，战术优势显著');
            }
        }

        // 威胁告警（有冷却）
        const now = Date.now();
        const uavs = Object.values(state.uavs || {});
        const criticalCount = uavs.filter(u => u.threat_level === 'critical').length;
        const highCount = uavs.filter(u => u.threat_level === 'high').length;
        const evadingCount = uavs.filter(u => u.state === 'EVADING').length;

        if (criticalCount > 0 && this._canAlert('critical', now)) {
            this.speak(`警告: ${criticalCount}架无人机处于紧急威胁中`, 'high');
            if (this.sounds.beepCritical) this.sounds.beepCritical();
        } else if (highCount >= 2 && this._canAlert('high', now)) {
            this.speak(`${highCount}架无人机处于高危状态`, 'high');
            if (this.sounds.beepWarning) this.sounds.beepWarning();
        }

        if (evadingCount >= 2 && this._canAlert('evading', now)) {
            this.speak(`${evadingCount}架无人机正在规避拦截`);
        }

        // 目标摧毁播报
        const prevState = stateStore.previous;
        if (prevState) {
            const prevDestroyed = prevState.summary?.destroyed_targets || 0;
            const curDestroyed = state.summary?.destroyed_targets || 0;
            if (curDestroyed > prevDestroyed) {
                this.speak(`目标被摧毁! 已摧毁${curDestroyed}个目标`, 'high');
                if (this.sounds.explosion) this.sounds.explosion();
            }

            // UAV损失
            const prevLost = prevState.summary?.destroyed_uavs || 0;
            const curLost = state.summary?.destroyed_uavs || 0;
            if (curLost > prevLost) {
                this.speak(`警告: 我方无人机被击落`, 'high');
                if (this.sounds.explosion) this.sounds.explosion();
            }

            // 任务完成
            if (prevState.mission?.mission_status !== 'completed' &&
                state.mission?.mission_status === 'completed') {
                this.speak('任务完成! 所有目标已被摧毁', 'high');
                if (this.sounds.beepInfo) this.sounds.beepInfo();
            }

            // 领导者变更
            if (prevState.network?.leader_id !== state.network?.leader_id &&
                state.network?.leader_id) {
                this.speak(`领导者变更: ${state.network.leader_id}`);
            }
        }

        // 拦截器追踪告警
        const interceptors = state.interceptors || {};
        const intrUnits = Object.values(interceptors.units || {});
        const engagingCount = intrUnits.filter(u => u.state === 'ENGAGING').length;
        if (engagingCount > 0 && this._canAlert('engaging', now)) {
            this.speak(`${engagingCount}架拦截器正在攻击我方无人机`);
            if (this.sounds.beepLock) this.sounds.beepLock();
        }
    }

    _canAlert(key, now) {
        const last = this.alertHistory[key] || 0;
        if (now - last < this.alertCooldownMs) return false;
        this.alertHistory[key] = now;
        return true;
    }

    // ============ 环境音效 ============

    _startEngineHum() {
        if (!this.sounds.engineHum || this._engineHumRunning) return;
        const { osc, gain } = this.sounds.engineHum;
        try {
            osc.start();
            gain.gain.setValueAtTime(this.ambientVolume, this.audioCtx.currentTime);
            this._engineHumRunning = true;
        } catch(e) {}
    }

    _stopEngineHum() {
        if (!this.sounds.engineHum || !this._engineHumRunning) return;
        const { gain } = this.sounds.engineHum;
        gain.gain.linearRampToValueAtTime(0, this.audioCtx.currentTime + 0.5);
        this._engineHumRunning = false;
    }

    setSfxVolume(vol) {
        this.sfxVolume = Math.max(0, Math.min(1, vol));
        if (this.masterGain) {
            this.masterGain.gain.value = this.sfxVolume;
        }
    }

    setAmbientVolume(vol) {
        this.ambientVolume = Math.max(0, Math.min(0.2, vol));
        if (this.sounds.engineHum && this._engineHumRunning) {
            this.sounds.engineHum.gain.gain.value = this.ambientVolume;
        }
    }

    mute() {
        this.muted = true;
        this.synth.cancel();
        if (this.masterGain) this.masterGain.gain.value = 0;
    }

    unmute() {
        this.muted = false;
        if (this.masterGain) this.masterGain.gain.value = 0.5;
    }

    toggleMute() {
        if (this.muted) this.unmute();
        else this.mute();
        return this.muted;
    }

    getStatus() {
        return {
            enabled: this.enabled,
            listening: this.isListening,
            speaking: this.speaking,
            muted: this.muted,
            lang: this.sttLang,
            queueLength: this.ttsQueue.length,
            engineHum: this._engineHumRunning,
        };
    }
}

// ============ 全局单例 ============
let voiceCommander = null;

eventBus.on('init', () => {
    voiceCommander = new VoiceCommander();
    window._voiceCommander = voiceCommander;

    // 用户首次点击页面时启动（浏览器要求用户交互后才允许音频）
    const startOnInteraction = () => {
        if (voiceCommander && !voiceCommander.enabled) {
            voiceCommander.start();
        }
        document.removeEventListener('click', startOnInteraction);
        document.removeEventListener('keydown', startOnInteraction);
    };
    document.addEventListener('click', startOnInteraction, { once: true });

    // 键盘快捷键: Ctrl+M 静音, Ctrl+L 开始听令
    document.addEventListener('keydown', (e) => {
        if (!voiceCommander) return;
        if (e.ctrlKey && e.key === 'm') {
            e.preventDefault();
            voiceCommander.toggleMute();
            eventBus.emit('voice-status', voiceCommander.getStatus());
        }
        if (e.ctrlKey && e.key === 'l') {
            e.preventDefault();
            voiceCommander.toggleListening();
            eventBus.emit('voice-status', voiceCommander.getStatus());
        }
    });
});
