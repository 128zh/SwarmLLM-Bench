/**
 * 蜂群作战仿真系统 - 应用编排器
 *
 * 职责：
 * 1. 初始化所有模块渲染器
 * 2. 订阅状态更新并委托到活动模块
 * 3. 管理战场和旧版UI更新（保持向后兼容）
 * 4. 设置控制按钮和环境控件
 */

// ============ 全局模块实例 ============
let battlefieldRenderer = null;
let _audioInitialized = false;
let _prevAudioState = null;

function _initAudioOnInteraction() {
    if (_audioInitialized) return;
    // 任意点击/按键时初始化 AudioContext (浏览器策略要求)
    document.addEventListener('click', () => { swarmAudio.resume(); swarmAudio.init(); }, { once: true });
    document.addEventListener('keydown', () => { swarmAudio.resume(); swarmAudio.init(); }, { once: true });
    _audioInitialized = true;
}

// ============ 初始化 ============
eventBus.on('init', () => {
    // 初始化战场渲染器（始终需要，跨标签共享数据）
    battlefieldRenderer = new BattlefieldRenderer('battle-map', 'network-topology');
    battlefieldRenderer.start();
    window._battlefieldRenderer = battlefieldRenderer;

    // 初始化 3D 空间音效引擎
    _initAudioOnInteraction();

    // 设置环境控件
    setupEnvironmentControls();

    // 设置控制按钮
    setupControlButtons();

    // 设置旧版UI更新（战场标签的侧边栏）
    setupLegacyUI();

    // 标签切换时的事件处理
    eventBus.on('tab-switch', (tab) => {
        if (tab === 'battlefield') {
            battlefieldRenderer.animRunning = true;
        } else {
            // 非战场标签中可暂停渲染以节省资源
        }
    });

    // 监测状态变化以更新旧版UI + 触发3D音效
    eventBus.on('state-update', (state) => {
        updateLegacyUI(state);
        _updateAudio(state);
    });

    // 语音控制按钮
    setupVoiceControls();

    // 设置视角切换（点击地图上的视角选择器）
    setupViewModeClicks();

    // 设置环境设置立即生效
    eventBus.on('tab-update:battlefield', (state) => {
        // 确保战场标签中的旧版UI持续更新
        updateLegacyUI(state);
    });
});

// ============ 语音控制 ============
function setupVoiceControls() {
    const listenBtn = document.getElementById('voice-listen-btn');
    const muteBtn = document.getElementById('voice-mute-btn');
    const transcriptEl = document.getElementById('voice-transcript-text');

    if (listenBtn) {
        listenBtn.onclick = () => {
            if (voiceCommander) {
                voiceCommander.toggleListening();
                listenBtn.classList.toggle('active', voiceCommander.isListening);
            }
        };
    }

    if (muteBtn) {
        muteBtn.onclick = () => {
            if (voiceCommander) {
                const muted = voiceCommander.toggleMute();
                muteBtn.classList.toggle('muted', muted);
                muteBtn.textContent = muted ? '🔇' : '🔊';
            }
        };
    }

    // 语音转写实时显示
    eventBus.on('voice-transcript', (data) => {
        if (transcriptEl) {
            transcriptEl.textContent = data.interim || data.final || '';
        }
    });

    // 语音命令识别后清空转写
    eventBus.on('voice-command', () => {
        if (transcriptEl) {
            setTimeout(() => { transcriptEl.textContent = ''; }, 2000);
        }
    });

    // 语音状态变化
    eventBus.on('voice-status', (status) => {
        if (listenBtn) {
            listenBtn.classList.toggle('active', status.listening);
        }
    });
}

// ============ 视角模式点击检测 ============
function setupViewModeClicks() {
    const mapCanvas = document.getElementById('battle-map');
    if (!mapCanvas) return;

    mapCanvas.addEventListener('click', (e) => {
        if (!battlefieldRenderer) return;
        const rect = mapCanvas.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;
        const w = mapCanvas.width, h = mapCanvas.height;

        // 视角选择器区域（右上角）
        const vsX = w - 190, vsY = 10, vsW = 180, vsH = 28;
        if (mx >= vsX && mx <= vsX + vsW && my >= vsY && my <= vsY + vsH) {
            const views = ['topdown', 'follow', 'fpv', 'fixed'];
            const idx = Math.floor((mx - vsX - 5) / 43);
            if (idx >= 0 && idx < views.length) {
                battlefieldRenderer.viewMode = views[idx];
                if (views[idx] === 'fpv') {
                    // 自动选择leader或第一个存活UAV
                    const state = stateStore.current;
                    if (state) {
                        const uavs = Object.values(state.uavs || {});
                        const fpv = uavs.find(u => u.is_leader && u.alive) || uavs.find(u => u.alive);
                        if (fpv) battlefieldRenderer.fpvUavId = fpv.id;
                    }
                } else {
                    battlefieldRenderer.fpvUavId = null;
                }
                if (views[idx] === 'follow') {
                    const state = stateStore.current;
                    if (state) {
                        const uavs = Object.values(state.uavs || {});
                        const follow = uavs.find(u => u.is_leader && u.alive) || uavs.find(u => u.alive);
                        if (follow) battlefieldRenderer.followUavId = follow.id;
                    }
                }
            }
            return;
        }

        // IR切换区域
        const irY = vsY + vsH + 4, irH = 28;
        if (mx >= vsX && mx <= vsX + vsW && my >= irY && my <= irY + irH) {
            battlefieldRenderer.irMode = !battlefieldRenderer.irMode;
            return;
        }
    });
}

// ============ 环境控件 ============
function setupEnvironmentControls() {
    const windSlider = document.getElementById('env-wind');
    const windVal = document.getElementById('env-wind-val');
    const precipSelect = document.getElementById('env-precip');
    const lightingSelect = document.getElementById('env-lighting');

    if (windSlider && windVal) {
        windSlider.addEventListener('input', () => {
            const val = windSlider.value;
            windVal.textContent = val + '级';
            sendRest('POST', '/api/environment/set', { wind_level: parseInt(val) });
        });
    }

    if (precipSelect) {
        precipSelect.addEventListener('change', () => {
            sendRest('POST', '/api/environment/set', { precipitation: precipSelect.value });
        });
    }

    if (lightingSelect) {
        lightingSelect.addEventListener('change', () => {
            sendRest('POST', '/api/environment/set', { lighting: lightingSelect.value });
        });
    }
}

// ============ 控制按钮 ============
function setupControlButtons() {
    const btnStart = document.getElementById('btn-start');
    const btnPause = document.getElementById('btn-pause');
    const btnStep = document.getElementById('btn-step');
    const btnReset = document.getElementById('btn-reset');
    const btnFault = document.getElementById('btn-fault');
    const btnDestroy = document.getElementById('btn-destroy');

    // Spawn UAV
    const btnSpawn = document.getElementById('btn-spawn-uav');
    if (btnSpawn) {
        btnSpawn.onclick = () => {
            const role = document.getElementById('spawn-role')?.value || 'striker';
            sendRest('POST', '/api/spawn_uav', { role: role })
                .then(r => { if (r && r.uav_id) alert(`已生成: ${r.uav_id} (${r.role}) 总计${r.total_uavs}架`); });
        };
    }

    // Export buttons
    const exportBtns = {
        'btn-export-json': '/api/export/json',
        'btn-export-csv': '/api/export/csv',
        'btn-export-html': '/api/export/html',
    };
    Object.entries(exportBtns).forEach(([id, url]) => {
        const btn = document.getElementById(id);
        if (btn) btn.onclick = () => window.open(url, '_blank');
    });
    const btnExportAll = document.getElementById('btn-export-all');
    if (btnExportAll) {
        btnExportAll.onclick = () => {
            sendRest('POST', '/api/export/all', { base_name: 'swarm_training_' + Date.now() })
                .then(r => alert('导出完成:\n' + Object.values(r.files||{}).join('\n')));
        };
    }

    // Interceptor controls
    const btnIntConfig = document.getElementById('btn-int-config');
    const btnIntSpawn = document.getElementById('btn-int-spawn');
    if (btnIntConfig) {
        btnIntConfig.onclick = () => {
            const diff = document.getElementById('int-difficulty')?.value || 'normal';
            sendRest('POST', '/api/interceptor/config', { difficulty: diff });
            alert('拦截集群已重新配置: ' + diff);
        };
    }
    if (btnIntSpawn) {
        btnIntSpawn.onclick = () => {
            const state = stateStore.current;
            if (state) {
                const uavs = Object.values(state.uavs || {}).filter(u => u.alive);
                if (uavs.length > 0) {
                    const target = uavs[0].position;
                    sendRest('POST', '/api/interceptor/spawn', {
                        position: { x: target.x + 1000, y: target.y, z: 300 }
                    });
                }
            }
        };
    }

    if (btnStart) btnStart.onclick = () => sendCommand('start');
    if (btnPause) btnPause.onclick = () => sendCommand('pause');
    if (btnStep) btnStep.onclick = () => sendCommand('step');
    if (btnReset) btnReset.onclick = () => sendCommand('reset');
    if (btnFault) btnFault.onclick = () => sendCommand('fault', { tick: 0 });

    if (btnDestroy) {
        btnDestroy.onclick = () => {
            const select = document.getElementById('destroy-select');
            const uavId = select?.value;
            if (uavId) sendCommand('destroy', { uav_id: uavId });
        };
    }

    // 3D 音效静音切换
    const btnAudioMute = document.getElementById('btn-audio-mute');
    if (btnAudioMute) {
        btnAudioMute.onclick = () => {
            const muted = swarmAudio.toggleMute();
            btnAudioMute.textContent = muted ? '🔇 3D音效: 关' : '🔊 3D音效: 开';
        };
    }
}

// ============ 旧版UI更新（战场标签侧边栏） ============
function setupLegacyUI() {
    // 注册旧版UI的更新函数（从原始app.js移植）
    window._legacyUI = {
        updateStats: updateLegacyStats,
        updateCommStats: updateLegacyCommStats,
        updateUAVList: updateLegacyUAVList,
        updateEventLog: updateLegacyEventLog,
        updateDestroySelect: updateLegacyDestroySelect,
    };
}

function updateLegacyUI(state) {
    updateLegacyStats(state);
    updateLegacyCommStats(state);
    updateLegacyUAVList(state);
    updateLegacyEventLog(state);
    updateLegacyDestroySelect(state);
    updateOODAPanel(state);
}

function updateOODAPanel(state) {
    const ooda = state.ooda;
    if (!ooda) return;

    const setVal = (id, val, cls) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.textContent = val;
        if (cls) el.className = cls;
    };

    setVal('ooda-advantage', (ooda.ooda_advantage >= 0 ? '+' : '') + (ooda.ooda_advantage * 100).toFixed(1) + '%');
    setVal('ooda-rating', ooda.advantage_rating || '--');
    setVal('ooda-blue', (ooda.blue_avg_time_ms || 0).toFixed(0));
    setVal('ooda-red', (ooda.red_avg_time_ms || 0).toFixed(0));

    const statusEl = document.getElementById('ooda-status');
    if (statusEl) {
        if (ooda.inside_enemy_loop) {
            statusEl.textContent = 'INSIDE ENEMY LOOP';
            statusEl.className = 'ooda-stat ooda-inside';
        } else {
            statusEl.textContent = 'OUTSIDE';
            statusEl.className = 'ooda-stat ooda-outside';
        }
    }
}

function updateLegacyStats(state) {
    const s = state.summary || {};
    const setVal = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    setVal('alive-uavs', s.alive_uavs || 0);
    setVal('destroyed-uavs', s.destroyed_uavs || 0);
    setVal('destroyed-targets', s.destroyed_targets || 0);
    setVal('remaining-targets', (s.total_targets || 0) - (s.destroyed_targets || 0));
    setVal('reconnect-count', s.reconnect_count || 0);
    setVal('leader-changes', s.leader_changes || 0);
}

function updateLegacyCommStats(state) {
    const c = state.communication || {};
    const setVal = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    setVal('comm-sent', c.total_sent || 0);
    setVal('comm-delivered', c.total_delivered || 0);
    setVal('comm-dropped', c.total_dropped || 0);
    setVal('comm-queue', c.current_queue_size || 0);
    setVal('comm-peak', c.peak_queue_size || 0);
    setVal('comm-latency', Math.round(c.delivery_latency_ms || 0) + 'ms');

    const bp = c.by_priority || {};
    const high = (bp.HIGH?.sent || 0);
    const medium = (bp.MEDIUM?.sent || 0);
    const low = (bp.LOW?.sent || 0);
    const total = Math.max(high + medium + low, 1);
    const barHigh = document.getElementById('bar-high');
    const barMedium = document.getElementById('bar-medium');
    const barLow = document.getElementById('bar-low');
    if (barHigh) barHigh.style.width = (high / total * 100) + '%';
    if (barMedium) barMedium.style.width = (medium / total * 100) + '%';
    if (barLow) barLow.style.width = (low / total * 100) + '%';
}

function updateLegacyUAVList(state) {
    const list = document.getElementById('uav-list');
    if (!list) return;
    const uavs = Object.values(state.uavs || {}).sort((a, b) => a.id.localeCompare(b.id));

    list.innerHTML = uavs.map(u => {
        const healthPct = Math.max(0, u.health);
        const batteryPct = Math.max(0, u.battery);
        const healthClass = healthPct < 30 ? 'low' : healthPct < 60 ? 'mid' : '';
        const batteryClass = batteryPct < 20 ? 'low' : '';
        const stateColor = u.alive ? '' : 'color: var(--accent-red);';

        return `
        <div class="uav-card ${!u.alive ? 'destroyed' : ''} ${u.is_leader ? 'is-leader' : ''}">
            <div class="uav-card-header">
                <span class="uav-card-id">${u.id}</span>
                <span class="uav-role-tag role-${u.role}">${ROLE_NAMES[u.role] || u.role}</span>
            </div>
            <div class="uav-card-body">
                <span>状态: <strong style="${stateColor}">${u.alive ? (STATE_NAMES[u.state] || u.state) : 'DESTROYED'}</strong>${u.threat_level && u.threat_level !== 'none' ? ` <span class='threat-badge threat-${u.threat_level}'>${u.threat_level.toUpperCase()}</span>` : ''}</span>
                <span>血量: <strong>${Math.round(u.health)}</strong></span>
                <span>电量: <strong>${Math.round(u.battery)}%</strong></span>
                <span>邻居: <strong>${(u.neighbors || []).length}</strong></span>
                <span>交战: <strong>${u.targets_engaged}</strong></span>
                <span>${u.is_leader ? '★ 指挥' : '跟随'}</span>
            </div>
            <div class="health-bar"><div class="health-bar-bg"><div class="health-bar-fill ${healthClass}" style="width:${healthPct}%"></div></div></div>
            <div class="battery-bar"><div class="battery-bar-bg"><div class="battery-bar-fill ${batteryClass}" style="width:${batteryPct}%"></div></div></div>
        </div>`;
    }).join('');
}

function updateLegacyEventLog(state) {
    const log = document.getElementById('event-log');
    if (!log) return;
    const events = (state.events || []).slice(-30).reverse();

    log.innerHTML = events.map(e => `
        <div class="event-entry ${e.category}">
            <span class="event-tick">[T${e.tick}]</span>
            <span class="event-message">${e.message}</span>
        </div>
    `).join('');
}

function updateLegacyDestroySelect(state) {
    const select = document.getElementById('destroy-select');
    if (!select) return;
    const uavs = Object.values(state.uavs || {}).filter(u => u.alive);
    const currentVal = select.value;

    select.innerHTML = '<option value="">-- 选择UAV --</option>' +
        uavs.map(u => `<option value="${u.id}">${u.id} (${ROLE_NAMES[u.role]})</option>`).join('');

    if (currentVal && uavs.find(u => u.id === currentVal)) {
        select.value = currentVal;
    }
}

// ============ 3D 空间音效集成 ============

/**
 * 每帧从仿真状态更新音效引擎
 * 检测状态变化并触发对应的空间化音效事件
 */
function _updateAudio(state) {
    if (!state) return;

    // 确保引擎已初始化
    swarmAudio.init();

    // 1. 更新所有 UAV 引擎声 (3D 空间化，持续音)
    swarmAudio.updateEngineSounds(state);

    if (!_prevAudioState) {
        _prevAudioState = state;
        return;
    }

    const prev = _prevAudioState;
    const uavs = state.uavs || {};
    const prevUAVs = prev.uavs || {};
    const targets = state.targets || {};
    const prevTargets = prev.targets || {};

    // 2. 检测目标被摧毁 → 爆炸音效
    for (const [tid, t] of Object.entries(targets)) {
        const pt = prevTargets[tid];
        if (t.destroyed && pt && !pt.destroyed) {
            swarmAudio.playExplosion(t.position, 1.0);
            console.log('[SwarmAudio] Explosion:', tid);
        }
    }

    // 3. 检测 UAV 状态变化
    for (const [uid, u] of Object.entries(uavs)) {
        const pu = prevUAVs[uid];
        if (!pu) continue;

        // UAV 被摧毁
        if (!u.alive && pu.alive) {
            swarmAudio.playUAVDestroyed(u.position);
            console.log('[SwarmAudio] UAV destroyed:', uid);
        }

        // UAV 受损 (血量下降)
        if (u.alive && u.health < pu.health - 1) {
            swarmAudio.playDamageAlert(u.position);
        }

        // UAV 进入交战状态
        if (u.state === 'ENGAGING' && pu.state !== 'ENGAGING') {
            swarmAudio.playWeaponFire(u.position);
        }

        // UAV 状态变化 (任何消息发送 → 通信提示)
        if ((u.messages_sent || 0) > (pu.messages_sent || 0)) {
            const increment = (u.messages_sent || 0) - (pu.messages_sent || 0);
            if (increment <= 3) { // 过滤大量消息爆发
                swarmAudio.playCommBeep(u.role);
            }
        }
    }

    // 4. 检测新 UAV 加入
    for (const uid of Object.keys(uavs)) {
        if (!prevUAVs[uid]) {
            console.log('[SwarmAudio] New UAV detected:', uid);
        }
    }

    // 5. 网络重组检测
    const currReconn = state.network?.reconnect_count || 0;
    const prevReconn = prev.network?.reconnect_count || 0;
    if (currReconn > prevReconn) {
        swarmAudio.playReconnect();
        console.log('[SwarmAudio] Network reconnect');
    }

    // 6. 任务完成检测
    const currStatus = state.mission?.mission_status;
    const prevStatus = prev.mission?.mission_status;
    if (currStatus === 'completed' && prevStatus !== 'completed') {
        setTimeout(() => swarmAudio.playMissionComplete(), 300);
        console.log('[SwarmAudio] Mission complete!');
    }

    _prevAudioState = state;
}
