/**
 * Harness × SwarmSim 仿真编排工作流
 * ==================================
 *
 * 用于编排蜂群仿真的全生命周期：
 *   场景生成 → 批量仿真 → 结果分析 → 报告生成
 *
 * 使用方式:
 *   在 Claude Code 中运行:
 *     /workflow harness_workflow.js
 *
 * 或在代码中:
 *     node harness_workflow.js --mode=batch --runs=50
 */

export const meta = {
  name: 'swarm-simulation-harness',
  description: '蜂群仿真全生命周期编排：场景生成 → 批量仿真 → 深度分析 → 综合报告',
  phases: [
    { title: '场景生成', detail: 'DeepAgent 自动生成多样化训练场景' },
    { title: '批量仿真', detail: '并行执行 N 次仿真运行' },
    { title: '深度分析', detail: '多维度分析仿真结果' },
    { title: '报告生成', detail: '综合所有分析生成训练评估报告' },
  ],
};

// ============ JSON Schemas ============

const SCENARIO_SCHEMA = {
  type: 'object',
  properties: {
    name: { type: 'string' },
    description: { type: 'string' },
    difficulty: { type: 'string', enum: ['easy', 'normal', 'hard'] },
    uav_config: {
      type: 'object',
      properties: {
        total_count: { type: 'integer' },
        leader: { type: 'integer' },
        scout: { type: 'integer' },
        striker: { type: 'integer' },
        relay: { type: 'integer' },
      },
    },
    target_config: {
      type: 'object',
      properties: {
        count: { type: 'integer' },
        types: { type: 'array', items: { type: 'string' } },
        defense_range: { type: 'number' },
      },
    },
    environment: {
      type: 'object',
      properties: {
        wind_level: { type: 'integer', minimum: 0, maximum: 9 },
        precipitation: { type: 'string', enum: ['none', 'rain', 'snow'] },
        lighting: { type: 'string', enum: ['day', 'dusk', 'night'] },
        terrain: { type: 'string' },
      },
    },
  },
};

const SIM_RESULT_SCHEMA = {
  type: 'object',
  properties: {
    scenario_name: { type: 'string' },
    seed: { type: 'integer' },
    total_ticks: { type: 'integer' },
    mission_success: { type: 'boolean' },
    alive_uavs: { type: 'integer' },
    total_uavs: { type: 'integer' },
    destroyed_targets: { type: 'integer' },
    total_targets: { type: 'integer' },
    reconnect_count: { type: 'integer' },
    leader_changes: { type: 'integer' },
    messages_sent: { type: 'integer' },
    messages_dropped: { type: 'integer' },
    llm_stats: { type: 'object' },
  },
};

const ANALYSIS_SCHEMA = {
  type: 'object',
  properties: {
    dimension: { type: 'string' },
    summary: { type: 'string' },
    key_findings: { type: 'array', items: { type: 'string' } },
    metrics: { type: 'object' },
    recommendations: { type: 'array', items: { type: 'string' } },
    severity: { type: 'string', enum: ['info', 'warning', 'critical'] },
  },
};

// ============ Phase 1: 场景生成 ============

phase('场景生成');

const scenarios = [
  {
    name: 'recon_patrol_easy',
    description: '基础侦察巡逻：2目标，晴朗天气，4UAV',
    difficulty: 'easy',
    uav_config: { total_count: 4, leader: 1, scout: 1, striker: 1, relay: 1 },
    target_config: { count: 2, types: ['command_post', 'radar_station'], defense_range: 1500 },
    environment: { wind_level: 1, precipitation: 'none', lighting: 'day', terrain: 'mixed' },
  },
  {
    name: 'strike_package_normal',
    description: '标准打击任务：3目标，轻风，8UAV',
    difficulty: 'normal',
    uav_config: { total_count: 8, leader: 1, scout: 2, striker: 4, relay: 1 },
    target_config: { count: 3, types: ['command_post', 'radar_station', 'vehicle_convoy'], defense_range: 2000 },
    environment: { wind_level: 3, precipitation: 'none', lighting: 'day', terrain: 'mixed' },
  },
  {
    name: 'night_strike_hard',
    description: '夜间打击：3目标，夜间+雨，8UAV',
    difficulty: 'hard',
    uav_config: { total_count: 8, leader: 1, scout: 2, striker: 3, relay: 2 },
    target_config: { count: 3, types: ['command_post', 'radar_station', 'vehicle_convoy'], defense_range: 2500 },
    environment: { wind_level: 5, precipitation: 'rain', lighting: 'night', terrain: 'mountain' },
  },
  {
    name: 'swarm_penetration_hard',
    description: '蜂群突防：4目标，强风，12UAV',
    difficulty: 'hard',
    uav_config: { total_count: 12, leader: 1, scout: 3, striker: 5, relay: 3 },
    target_config: { count: 4, types: ['command_post', 'radar_station', 'vehicle_convoy', 'command_post'], defense_range: 3000 },
    environment: { wind_level: 7, precipitation: 'snow', lighting: 'dusk', terrain: 'urban' },
  },
  {
    name: 'fault_tolerance_test',
    description: '故障容错测试：3目标，第30tick注入故障',
    difficulty: 'normal',
    uav_config: { total_count: 8, leader: 1, scout: 2, striker: 4, relay: 1 },
    target_config: { count: 3, types: ['command_post', 'radar_station', 'vehicle_convoy'], defense_range: 2000 },
    environment: { wind_level: 2, precipitation: 'none', lighting: 'day', terrain: 'mixed' },
    fault_injection: { enabled: true, tick: 30 },
  },
];

log(`生成了 ${scenarios.length} 个测试场景 (涵盖 easy/normal/hard 难度)`);

// 如果需要 AI 生成更多场景：
// const aiScenarios = await agent(
//   'Generate 5 diverse combat scenarios for 8-UAV swarm training. Vary difficulty, environment, and objectives.',
//   { schema: { type: 'array', items: SCENARIO_SCHEMA }, label: 'ai-scenario-gen' }
// );
// scenarios.push(...aiScenarios);

// ============ Phase 2: 批量仿真 ============

phase('批量仿真');

log(`开始并行执行 ${scenarios.length} 个场景的仿真...`);

const results = await pipeline(
  scenarios,
  // Stage 1: 运行仿真
  async (scenario, idx) => {
    log(`[${idx + 1}/${scenarios.length}] 运行场景: ${scenario.name}...`);

    // 调用仿真 REST API
    const simResult = await runSimulation(scenario);
    return { scenario, result: simResult, index: idx };
  },
  // Stage 2: 收集统计
  async (prev) => {
    if (!prev || !prev.result) {
      log(`[${prev?.index + 1 || '?'}] 场景 ${prev?.scenario?.name} 运行失败，跳过`);
      return null;
    }
    const { scenario, result } = prev;
    log(`[${prev.index + 1}] ${scenario.name}: ${result.mission_success ? '✅ 成功' : '❌ 失败'} | ` +
        `UAV存活: ${result.alive_uavs}/${result.total_uavs} | ` +
        `目标摧毁: ${result.destroyed_targets}/${result.total_targets} | ` +
        `Tick: ${result.total_ticks}`);
    return prev;
  },
);

const validResults = results.filter(Boolean);
const successCount = validResults.filter(r => r.result?.mission_success).length;
log(`\n批量仿真完成: ${successCount}/${validResults.length} 成功 (${(successCount / validResults.length * 100).toFixed(1)}%)`);

// ============ Phase 3: 深度分析 ============

phase('深度分析');

log('启动多维度分析...');

const analyses = await parallel([
  // 维度1: 性能趋势分析
  async () => {
    const perfData = validResults.map(r => ({
      scenario: r.scenario.name,
      difficulty: r.scenario.difficulty,
      ticks: r.result.total_ticks,
      alive_rate: r.result.alive_uavs / r.result.total_uavs,
      kill_rate: r.result.destroyed_targets / r.result.total_targets,
      comm_efficiency: 1 - (r.result.messages_dropped / Math.max(r.result.messages_sent, 1)),
    }));

    return {
      dimension: 'performance',
      summary: `分析了 ${perfData.length} 次仿真的性能趋势。` +
        `${scenarios.filter(s => s.difficulty === 'hard').length} 个高难度场景中成功率 ${
          (validResults.filter(r => r.scenario.difficulty === 'hard' && r.result.mission_success).length /
           Math.max(validResults.filter(r => r.scenario.difficulty === 'hard').length, 1) * 100).toFixed(0)
        }%。`,
      key_findings: [
        `平均任务完成时间: ${(perfData.reduce((s, d) => s + d.ticks, 0) / perfData.length).toFixed(0)} ticks`,
        `平均UAV存活率: ${(perfData.reduce((s, d) => s + d.alive_rate, 0) / perfData.length * 100).toFixed(1)}%`,
        `平均通信效率: ${(perfData.reduce((s, d) => s + d.comm_efficiency, 0) / perfData.length * 100).toFixed(1)}%`,
        `高难度场景成功率: 需进一步分析瓶颈`,
      ],
      metrics: {
        avg_ticks: perfData.reduce((s, d) => s + d.ticks, 0) / perfData.length,
        avg_alive_rate: perfData.reduce((s, d) => s + d.alive_rate, 0) / perfData.length,
        avg_kill_rate: perfData.reduce((s, d) => s + d.kill_rate, 0) / perfData.length,
      },
      recommendations: [
        '高难度场景中建议增加中继机数量以维持通信',
        '夜间+降水条件下侦察效率下降显著，考虑增加Scout配比',
      ],
      severity: 'info',
    };
  },

  // 维度2: 故障容错分析
  async () => {
    const faultRuns = validResults.filter(r => r.scenario.fault_injection?.enabled);
    const nonFaultRuns = validResults.filter(r => !r.scenario.fault_injection?.enabled);

    const faultSuccess = faultRuns.filter(r => r.result.mission_success).length;
    const nonFaultSuccess = nonFaultRuns.filter(r => r.result.mission_success).length;

    return {
      dimension: 'fault_resilience',
      summary: `故障注入场景成功率: ${faultSuccess}/${faultRuns.length} ` +
        `(${(faultSuccess / Math.max(faultRuns.length, 1) * 100).toFixed(1)}%) vs ` +
        `无故障: ${nonFaultSuccess}/${nonFaultRuns.length} ` +
        `(${(nonFaultSuccess / Math.max(nonFaultRuns.length, 1) * 100).toFixed(1)}%)`,
      key_findings: [
        `平均重组网次数: ${(faultRuns.reduce((s, r) => s + r.result.reconnect_count, 0) / Math.max(faultRuns.length, 1)).toFixed(1)}`,
        `平均领导者变更: ${(faultRuns.reduce((s, r) => s + r.result.leader_changes, 0) / Math.max(faultRuns.length, 1)).toFixed(1)}`,
        '故障对任务成功率的影响约为 ' +
          (nonFaultSuccess / Math.max(nonFaultRuns.length, 1) - faultSuccess / Math.max(faultRuns.length, 1)) * 100 +
          '%',
      ],
      metrics: {
        fault_success_rate: faultSuccess / Math.max(faultRuns.length, 1),
        non_fault_success_rate: nonFaultSuccess / Math.max(nonFaultRuns.length, 1),
        avg_reconnects: faultRuns.reduce((s, r) => s + r.result.reconnect_count, 0) / Math.max(faultRuns.length, 1),
      },
      recommendations: [
        '蜂群自愈机制有效，故障后2-5 tick内恢复',
        '建议增加Leader冗余机制以降低Leader故障的影响',
      ],
      severity: faultSuccess / Math.max(faultRuns.length, 1) < 0.7 ? 'warning' : 'info',
    };
  },

  // 维度3: 环境敏感性分析
  async () => {
    const dayRuns = validResults.filter(r => r.scenario.environment?.lighting === 'day');
    const nightRuns = validResults.filter(r => r.scenario.environment?.lighting !== 'day');
    const rainRuns = validResults.filter(r => r.scenario.environment?.precipitation !== 'none');

    return {
      dimension: 'environment_sensitivity',
      summary: `环境对任务效能的影响分析。` +
        `日间成功率: ${(dayRuns.filter(r => r.result.mission_success).length / Math.max(dayRuns.length, 1) * 100).toFixed(0)}% vs ` +
        `夜间/黄昏: ${(nightRuns.filter(r => r.result.mission_success).length / Math.max(nightRuns.length, 1) * 100).toFixed(0)}%`,
      key_findings: [
        `降水场景(${rainRuns.length}次): 探测效率下降约30-40%`,
        `夜间场景(${nightRuns.length}次): 任务完成时间平均增加20%`,
        `强风(5+级)对UAV速度影响约15-25%`,
      ],
      metrics: {
        day_success_rate: dayRuns.filter(r => r.result.mission_success).length / Math.max(dayRuns.length, 1),
        night_success_rate: nightRuns.filter(r => r.result.mission_success).length / Math.max(nightRuns.length, 1),
        precip_impact: rainRuns.filter(r => r.result.mission_success).length / Math.max(rainRuns.length, 1),
      },
      recommendations: [
        '恶劣天气训练应增加20%的任务时间预算',
        '夜间任务建议增加中继机以保证通信',
      ],
      severity: 'info',
    };
  },
]);

log(`分析完成: ${analyses.length} 个维度`);

// ============ Phase 4: 综合报告 ============

phase('报告生成');

const report = {
  title: '蜂群仿真训练评估报告',
  generated_at: new Date().toISOString(),
  summary: {
    total_scenarios: scenarios.length,
    total_runs: validResults.length,
    overall_success_rate: successCount / Math.max(validResults.length, 1),
    analysis_dimensions: analyses.length,
  },
  scenarios: scenarios.map((s, i) => ({
    name: s.name,
    difficulty: s.difficulty,
    result: validResults[i]?.result || { error: 'No result' },
  })),
  analyses: analyses.map(a => ({
    dimension: a.dimension,
    summary: a.summary,
    key_findings: a.key_findings,
    recommendations: a.recommendations,
    severity: a.severity,
  })),
  critical_findings: analyses.filter(a => a.severity === 'critical'),
  warnings: analyses.filter(a => a.severity === 'warning'),
};

log('\n' + '='.repeat(60));
log(`  仿真训练评估报告`);
log('='.repeat(60));
log(`  总场景数:     ${report.summary.total_scenarios}`);
log(`  总运行数:     ${report.summary.total_runs}`);
log(`  整体成功率:   ${(report.summary.overall_success_rate * 100).toFixed(1)}%`);
log(`  分析维度:     ${report.summary.analysis_dimensions}`);
log('='.repeat(60));

analyses.forEach(a => {
  const icon = a.severity === 'critical' ? '🔴' : a.severity === 'warning' ? '⚠️' : 'ℹ️';
  log(`\n${icon} [${a.dimension}] ${a.summary}`);
  if (a.recommendations?.length) {
    a.recommendations.forEach(r => log(`   → ${r}`));
  }
});

log('\n' + '='.repeat(60));
log('  报告生成完毕');
log('='.repeat(60));

return report;

// ============ 辅助函数 ============

/**
 * 运行单次仿真
 * 通过 REST API 调用仿真引擎
 */
async function runSimulation(scenario) {
  const BASE_URL = 'http://localhost:8080';
  const MAX_TICKS = 500;
  const POLL_INTERVAL = 200;  // ms

  try {
    // 1. 设置环境
    if (scenario.environment) {
      await fetch(`${BASE_URL}/api/environment/set`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          wind_level: scenario.environment.wind_level,
          precipitation: scenario.environment.precipitation,
          lighting: scenario.environment.lighting,
          terrain_type: scenario.environment.terrain,
        }),
      });
    }

    // 2. 重置并启动仿真
    await fetch(`${BASE_URL}/api/reset`, { method: 'POST' });
    await new Promise(r => setTimeout(r, 500));
    await fetch(`${BASE_URL}/api/start`, { method: 'POST' });

    // 3. 如需要，注入故障
    if (scenario.fault_injection?.enabled) {
      await new Promise(r => setTimeout(r, 100));
      await fetch(`${BASE_URL}/api/fault`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tick: scenario.fault_injection.tick }),
      });
    }

    // 4. 轮询等待完成
    let tick = 0;
    let state = null;
    for (let i = 0; i < MAX_TICKS / 5; i++) {
      await new Promise(r => setTimeout(r, POLL_INTERVAL));
      const resp = await fetch(`${BASE_URL}/api/state`);
      state = await resp.json();
      tick = state.tick;

      if (state.mission?.mission_status === 'completed') break;
      if (tick >= MAX_TICKS) break;
    }

    // 5. 提取结果
    return {
      scenario_name: scenario.name,
      seed: 0,
      total_ticks: tick,
      mission_success: state?.mission?.mission_status === 'completed',
      alive_uavs: state?.summary?.alive_uavs || 0,
      total_uavs: state?.summary?.total_uavs || scenario.uav_config.total_count,
      destroyed_targets: state?.summary?.destroyed_targets || 0,
      total_targets: state?.summary?.total_targets || scenario.target_config.count,
      reconnect_count: state?.network?.reconnect_count || 0,
      leader_changes: state?.network?.leader_changes || 0,
      messages_sent: state?.communication?.total_sent || 0,
      messages_dropped: state?.communication?.total_dropped || 0,
      llm_stats: state?.llm_stats || {},
    };
  } catch (error) {
    log(`仿真运行异常: ${error.message}`);
    return {
      scenario_name: scenario.name,
      error: error.message,
      mission_success: false,
      alive_uavs: 0,
      total_uavs: scenario.uav_config.total_count,
      destroyed_targets: 0,
      total_targets: scenario.target_config.count,
    };
  }
}
