#!/usr/bin/env python3
"""
蜂群多无人机协同作战仿真系统
============================
入口文件

系统架构：
┌──────────────────────────────────────┐
│         Web 可视化仪表盘 (浏览器)      │
│    Canvas战场地图 / 网络拓扑 / 统计    │
├──────────────────────────────────────┤
│      aiohttp Web Server + WebSocket  │
├──────────────────────────────────────┤
│          仿真引擎 (SimulationEngine)   │
│     ┌──────────┬──────────┐          │
│     │任务协调器 │ 蜂群网络  │          │
│     ├──────────┼──────────┤          │
│     │通信Broker│ 物理仿真  │          │
│     └──────────┴──────────┘          │
├──────────────────────────────────────┤
│          UAV Agents (N个智能体)       │
│    指挥机/侦察机/攻击机/中继机        │
├──────────────────────────────────────┤
│          Environment (目标/威胁)      │
└──────────────────────────────────────┘

核心特性：
1. 蜂群自组网：Mesh拓扑自动发现，心跳监测
2. 故障容错：单机损坏时自动重组网、领导者选举、任务重分配
3. 高并发通信：异步消息Broker，优先级队列，背压控制
4. 协同作战：多机协同侦察-打击-中继
5. 实时可视化：Web仪表盘，战场态势图，网络拓扑
6. 空间哈希网格：O(n) 拓扑计算，消除O(n²)瓶颈
7. 多进程并行：大规模蜂群（50+UAV）自动启用
8. 蒙特卡洛：批量仿真统计分析
9. RL训练接口：Gymnasium环境，支持PPO/SAC/MADDPG

启动方式：
    python main.py                  # 启动仿真+Web服务器
    python main.py --headless       # 仅命令行模式（无Web）
    python main.py --parallel       # 启用多进程并行UAV计算
    python main.py --monte-carlo    # 蒙特卡洛批量仿真模式
    python main.py --rl             # 强化学习训练模式
    python main.py --steps 200      # 运行指定步数后退出
"""
import asyncio
import argparse
import sys
import os
import time

# 确保项目根目录在path中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import WEB_HOST, WEB_PORT, DEFAULT_UAV_COUNT, MISSION_TARGET_COUNT
from core.simulation import SimulationEngine
from visualization.server import VisualizationServer


async def run_with_web(uav_count: int, target_count: int, auto_start: bool, fault_tick: int,
                      max_steps: int, parallel: bool = False, llm_enabled: bool = False,
                      no_interceptors: bool = False, interceptor_difficulty: str = "normal"):
    """启动Web可视化模式"""
    llm_client = None
    if llm_enabled:
        from core.llm_client import get_llm_client
        llm_client = get_llm_client()
    engine = SimulationEngine(uav_count, target_count, parallel_uavs=parallel,
                              enable_interceptors=not no_interceptors,
                              interceptor_difficulty=interceptor_difficulty,
                              llm_enabled=llm_enabled, llm_client=llm_client)
    engine.initialize()

    # 自动启用故障注入
    if fault_tick > 0:
        engine.enable_fault_injection(fault_tick)

    server = VisualizationServer(engine, llm_enabled=llm_enabled)
    await server.start()

    print(f"\n{'='*60}")
    print(f"  蜂群多无人机协同作战仿真系统")
    print(f"{'='*60}")
    print(f"  无人机数量: {uav_count}")
    print(f"  目标数量:   {target_count}")
    print(f"  故障注入:   {'tick=' + str(fault_tick) if fault_tick > 0 else '禁用'}")
    print(f"  Web界面:    http://localhost:{WEB_PORT}")
    print(f"{'='*60}")
    print(f"\n  在浏览器中打开上述地址查看实时态势")
    print(f"  可在界面中启动/暂停仿真、注入故障\n")

    # 发送初始状态
    await server._on_state_update(engine.get_state())

    if auto_start:
        await asyncio.sleep(1)
        asyncio.ensure_future(engine.run(max_steps))

    # 保持运行
    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n正在停止仿真...")
        engine.running = False


async def run_headless(uav_count: int, target_count: int, fault_tick: int, max_steps: int,
                       parallel: bool = False, no_interceptors: bool = False,
                       interceptor_difficulty: str = "normal", llm_enabled: bool = False):
    """命令行模式（无Web界面）"""
    llm_client = None
    if llm_enabled:
        from core.llm_client import get_llm_client
        llm_client = get_llm_client()
    engine = SimulationEngine(uav_count, target_count, parallel_uavs=parallel,
                              enable_interceptors=not no_interceptors,
                              interceptor_difficulty=interceptor_difficulty,
                              llm_enabled=llm_enabled, llm_client=llm_client)
    engine.initialize()

    if fault_tick > 0:
        engine.enable_fault_injection(fault_tick)

    print(f"\n{'='*60}")
    print(f"  蜂群协同作战仿真 - 命令行模式")
    print(f"{'='*60}")
    print(f"  无人机: {uav_count} | 目标: {target_count}")
    print(f"  故障注入: {'tick=' + str(fault_tick) if fault_tick > 0 else '禁用'}")
    print(f"  最大步数: {max_steps if max_steps > 0 else '无限'}")
    print(f"{'='*60}\n")

    # 运行仿真
    await engine.run(max_steps)

    # 输出结果
    state = engine.get_state()
    summary = state["summary"]
    comm = state["communication"]
    network = state["network"]
    mission = state["mission"]

    print(f"\n{'='*60}")
    print(f"  仿真结束")
    print(f"{'='*60}")
    print(f"  总tick数:        {state['tick']}")
    print(f"  运行时间:        {state['elapsed_time']}s")
    print(f"  任务状态:        {mission['mission_status']}")
    print(f"  存活/损失UAV:    {summary['alive_uavs']}/{summary['destroyed_uavs']}")
    print(f"  已摧毁目标:      {summary['destroyed_targets']}/{summary['total_targets']}")
    print(f"  重组网次数:      {network['reconnect_count']}")
    print(f"  领导者变更:      {network['leader_changes']}")
    print(f"  网络分割:        {network['network_splits']}")
    print(f"  --- 通信统计 ---")
    print(f"  消息发送/投递/丢弃: {comm['total_sent']}/{comm['total_delivered']}/{comm['total_dropped']}")
    print(f"  峰值队列:        {comm['peak_queue_size']}")
    print(f"  平均延迟:        {comm['delivery_latency_ms']:.1f}ms")
    print(f"  --- 事件日志 ---")
    for event in engine.event_log[-15:]:
        print(f"  [T{event['tick']}] [{event['category']}] {event['message']}")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="蜂群多无人机协同作战仿真系统")
    parser.add_argument("--headless", action="store_true", help="命令行模式（无Web界面）")
    parser.add_argument("-n", "--uavs", type=int, default=DEFAULT_UAV_COUNT, help=f"无人机数量 (默认{DEFAULT_UAV_COUNT})")
    parser.add_argument("-t", "--targets", type=int, default=MISSION_TARGET_COUNT, help=f"目标数量 (默认{MISSION_TARGET_COUNT})")
    parser.add_argument("--fault", type=int, default=0, help="故障注入tick (0=禁用, >0在第N个tick注入)")
    parser.add_argument("--auto-start", action="store_true", help="自动启动仿真（Web模式）")
    parser.add_argument("--steps", type=int, default=0, help="最大运行步数 (0=无限)")
    parser.add_argument("--parallel", action="store_true", help="启用多进程并行UAV计算（大规模蜂群）")
    parser.add_argument("--workers", type=int, default=None, help="并行worker进程数（默认CPU-1）")
    parser.add_argument("--llm", action="store_true", help="启用LLM/DeepAgent增强 (需设置 LLM_API_KEY)")

    # 拦截器配置
    parser.add_argument("--no-interceptors", action="store_true", help="禁用敌方拦截集群")
    parser.add_argument("--interceptor-difficulty", type=str, default="normal",
                        choices=["easy", "normal", "hard"], help="拦截器难度 (默认normal)")

    # 蒙特卡洛模式
    parser.add_argument("--monte-carlo", action="store_true", help="蒙特卡洛批量仿真模式")
    parser.add_argument("--num-runs", type=int, default=100, help="蒙特卡洛运行次数 (默认100)")

    # RL模式
    parser.add_argument("--rl", action="store_true", help="强化学习训练模式")
    parser.add_argument("--rl-mode", type=str, default="centralized",
                        choices=["centralized", "decentralized"],
                        help="RL控制模式 (默认centralized)")
    parser.add_argument("--rl-episodes", type=int, default=1000, help="RL训练episode数 (默认1000)")

    args = parser.parse_args()

    # 蒙特卡洛模式
    if args.monte_carlo:
        run_monte_carlo_mode(args)
        return

    # RL模式
    if args.rl:
        run_rl_mode(args)
        return

    # 正常仿真模式
    if args.headless:
        asyncio.run(run_headless(args.uavs, args.targets, args.fault, args.steps, args.parallel,
                                  no_interceptors=args.no_interceptors,
                                  interceptor_difficulty=args.interceptor_difficulty,
                                  llm_enabled=args.llm))
    else:
        try:
            asyncio.run(run_with_web(args.uavs, args.targets, args.auto_start,
                                     args.fault, args.steps, args.parallel, args.llm,
                                     no_interceptors=args.no_interceptors,
                                     interceptor_difficulty=args.interceptor_difficulty))
        except KeyboardInterrupt:
            print("\n已退出")


def run_monte_carlo_mode(args):
    """蒙特卡洛批量仿真"""
    print(f"\n{'='*60}")
    print(f"  蒙特卡洛批量仿真")
    print(f"{'='*60}")
    print(f"  运行次数:     {args.num_runs}")
    print(f"  UAV数量:      {args.uavs}")
    print(f"  目标数量:      {args.targets}")
    print(f"  故障注入tick:  {args.fault if args.fault > 0 else '禁用'}")
    print(f"  Worker数:      {args.workers or '自动(CPU-1)'}")
    print(f"{'='*60}\n")

    start = time.time()
    stats = SimulationEngine.run_monte_carlo(
        num_runs=args.num_runs,
        uav_count=args.uavs,
        target_count=args.targets,
        max_ticks=args.steps if args.steps > 0 else 500,
        fault_tick=args.fault,
        max_workers=args.workers,
    )
    elapsed = time.time() - start

    print(f"\n{'='*60}")
    print(f"  仿真结果")
    print(f"{'='*60}")
    print(f"  总运行次数:     {stats['total_runs']}")
    print(f"  成功/失败:      {stats['successful_runs']}/{stats['failed_runs']}")
    print(f"  任务成功率:     {stats['success_rate']:.1%}")
    print(f"  平均存活UAV:    {stats['alive_uavs']['mean']:.1f}")
    print(f"     (范围: {stats['alive_uavs']['min']:.0f}-{stats['alive_uavs']['max']:.0f})")
    print(f"  平均摧毁目标:   {stats['destroyed_targets']['mean']:.1f}")
    print(f"  平均重组网次数: {stats['reconnect_count']['mean']:.1f}")
    print(f"  平均领导者变更: {stats['leader_changes']['mean']:.1f}")
    print(f"  平均消息发送:   {stats['messages_sent']['mean']:.0f}")
    print(f"  总耗时:         {elapsed:.1f}s")
    print(f"  平均每次耗时:   {elapsed/args.num_runs:.2f}s")
    print(f"{'='*60}\n")


def run_rl_mode(args):
    """强化学习训练模式"""
    print(f"\n{'='*60}")
    print(f"  强化学习训练模式")
    print(f"{'='*60}")
    print(f"  控制模式:     {args.rl_mode}")
    print(f"  UAV数量:      {args.uavs}")
    print(f"  目标数量:      {args.targets}")
    print(f"  训练Episodes:  {args.rl_episodes}")
    print(f"{'='*60}\n")

    from env import make_env

    env = make_env(
        mode=args.rl_mode,
        uav_count=args.uavs,
        target_count=args.targets,
        max_ticks=args.steps if args.steps > 0 else 500,
    )

    print("环境创建成功!")
    print(f"  观测空间: {env.observation_space}")
    print(f"  行动空间: {env.action_space}")
    print()

    # 随机策略演示
    print("运行随机策略演示...")
    obs, info = env.reset()
    total_reward = 0
    for step in range(100):
        action = env.action_space.sample()
        obs, reward, terminated, truncated, info = env.step(action)
        total_reward += reward
        if step % 20 == 0:
            print(f"  Step {step}: reward={reward:.2f}, "
                  f"alive={info['alive_uavs']}, destroyed={info['destroyed_targets']}")
        if terminated or truncated:
            print(f"  终止于 step {step}: {info}")
            break

    print(f"\n  总奖励: {total_reward:.1f}")
    print(f"  最终tick: {info['tick']}")
    print(f"  存活UAV: {info['alive_uavs']}/{args.uavs}")
    print(f"  摧毁目标: {info['destroyed_targets']}/{args.targets}")

    # 提示Stable-Baselines3训练
    print(f"\n{'='*60}")
    print("  使用 Stable-Baselines3 训练示例:")
    print(f"{'='*60}")
    print("""
    from stable_baselines3 import PPO
    from swarm_sim.env import make_vec_env

    vec_env = make_vec_env(num_envs=4, mode="centralized", uav_count=8)
    model = PPO("MultiInputPolicy", vec_env, verbose=1, tensorboard_log="./logs/")
    model.learn(total_timesteps=1_000_000)
    model.save("swarm_ppo_model")
    """)

    env.close()


if __name__ == "__main__":
    main()
