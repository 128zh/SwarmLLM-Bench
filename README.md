项目名称：大语言模型（LLM）增强的多智能体协同仿真平台**

**项目角色**：核心架构师 / 多智能体系统负责人**技术栈**：`Python (Asyncio)` `LangGraph` `DeepSeek/Claude/OpenAI API` `Gymnasium` `PEFT (QLoRA 4-bit)` `ProcessPoolExecutor` `WebSocket` `Three.js` `Canvas 2D` `Web Audio API (HRTF)` `Web Speech API` `Spatial Hashing` `JSONL`

* * *

**项目概述**主导研发面向军事智能化训练与多智能体算法验证的 **LLM 驱动分布式协同仿真平台**。系统采用 **"FSM + LLM"混合认知架构（Hybrid Cognitive Architecture）**——确定性状态机保障 100ms 实时安全执行，大语言模型以异步非阻塞模式注入高层战术推理——构建了从**高保真物理仿真**、**去中心化通信组网**、**LLM 增强战术决策**、**数据飞轮闭环进化**到**自然语言人机指挥**的全链路闭环。平台支持 50+ 无人机异构蜂群并发仿真，集成动态环境引擎（0-9 级风力/降水/地形/电子战衰减），三阶段对照实验（P1 纯规则 0% → P3 LLM 88%）验证了 LLM 对蜂群任务成功率的颠覆性提升。

* * *

**核心职责与技术成果****1. 设计并实现 LLM 驱动的 DeepAgent 混合认知决策架构（Harness 核心）**

* 突破传统 FSM 单一规则决策范式，设计 **"FSM 安全网 + LLM 异步推理"双层混合 Harness**——FSM 层以 100ms 周期保障基本生存/规避/通信（永不依赖 LLM），LLM 层以 5-15 tick 异步间隔提供高层战术推理（目标选择/协同策略/异常应对），LLM 超时或异常时系统**静默回退至 FSM 兜底**，确保零中断。
* 构建 **四层安全校验管线（Safety Validation Pipeline）**：角色白名单（Leader 禁止直接攻击）→ 状态白名单（受损状态禁止进攻）→ 置信度门槛（<0.3 拒绝）→ 物理安全规则（HP<15 锁定攻击），每层独立拦截，LLM 决策须全部通过方可执行。
* 设计 **多模型路由层（Multi-Provider LLM Router）**：统一封装 Claude API（tool_use 原生支持）、OpenAI API（json_schema 模式）、DeepSeek API（OpenAI 兼容）及本地模型（Ollama/vLLM），支持**分层异构部署**（Leader 用 Opus 强推理，Striker 用 Flash 低成本，重复态势走语义缓存）。
* 设计 **语义缓存层（Semantic Cache）**：基于 MD5 哈希的态势指纹匹配——相同战场态势直接复用历史 LLM 决策，缓存命中率 >70%，单次仿真 API 成本控制在 **$0.003**。

**2. 构建基于 LangGraph 范式的异构多智能体协同体系**

* 采用 **LangGraph 风格状态机** 定义 UAV Agent 的 10 种状态及条件转换边，实现 Agent 在复杂对抗环境下的**自主行为规划与故障自愈**（节点损毁后 <0.5s 完成 Leader 重选举与任务再分配）。
* 设计 **异构角色体系**：Leader（全局调度）、Scout（广域感知）、Striker（火力输出）、Relay（通信中继）四类 Agent，通过**优先级消息总线（Priority Pub/Sub Broker，HIGH/MEDIUM/LOW 三级 + TTL 过期 + 去重）** 实现跨角色信息共享与"侦察→共享→协同打击→中继回传"战术闭环。
* 引入 **合同网协议（Contract Net Protocol）** 替代传统集中式任务分配——Coordinator 发布任务公告 → 各 UAV 独立评估能力并投标（Bid = {ETA, 成功率, 成本}）→ 仲裁授标 → 执行监控，提升蜂群在 Leader 损毁/通信降级场景下的抗毁性。

**3. 设计五层数据飞轮（Data Flywheel）实现策略闭环进化**

* **L1 仿真运行层**：每次仿真运行产生态势-决策-结果数据流。
* **L2 数据采集层**：自动提取 **TrainingTriplet（态势向量 + LLM 决策 + 结果标签）** 三元组，结构化持久化至 JSONL。
* **L3 闭环学习层**：自动识别成功模式（"先清拦截器再攻目标"）与失败模式（"规避死循环"），生成战术经验库。
* **L4 知识蒸馏层**：将成功/失败案例转化为 **Few-shot Prompt 注入**与 **System Prompt 自动优化**，实现"越仿真越聪明"。
* **L5 策略进化层**：基于 **Self-Play 对抗训练（Blue LLM vs Red LLM）** + **ELO 评分机制** + **遗传算法策略重组**，实现蜂群战术的自主进化。

**4. 构建 LoRA 微调闭环（QLoRA Fine-Tuning Pipeline）**

* 设计 **飞轮数据 → Alpaca/ShareGPT 微调格式** 自动转换管线，正样本（高奖励决策）与负样本（规避死循环/自杀决策）自动标注。
* 实现 **QLoRA 4-bit 量化微调**（PEFT + bitsandbytes），支持在消费级 GPU（RTX 3090/4090, 8GB VRAM）上对 DeepSeek/Qwen 等基座模型进行领域适配，微调后模型无缝替换 LLMClient。
* 构建 **A/B 评估框架**：微调前 vs 微调后模型在相同仿真场景下并行跑分，量化微调收益。

**5. 打造标准化 RL 训练环境与 OODA 量化评估引擎**

* 基于 **Gymnasium** 标准接口封装仿真环境，支持集中式（Centralized）、分布式（Decentralized）、层级式（Hierarchical）三种 MARL 控制模式，兼容 PPO/QMIX/MADDPG。
* 基于 John Boyd OODA 理论开发 **显式循环度量引擎（OODA Engine）**，提出 **`orient_depth` 指标**（0-3 维：威胁向量分析 × 机会窗口评估 × 决策选项生成），将 Agent 的"态势理解深度"从定性转为定量。引入 LLM 后，orient_depth 从基线 0.0 提升至 1.9，OODA 循环数**涌现增长 27 倍**（26→706 次/run）。
* 构建 **Monte Carlo 批量并行评估器**（ProcessPoolExecutor），支持 100+ 独立仿真并行运行，输出统计显著性分析。

**6. 实现沉浸式人机交互与多模态态势感知（Harness 感知与交互层）**

* 开发 **自然语言指挥官接口（NL Command Interface）**：LLM 将指挥官自然语言指令（"侦察东南区域，发现目标后呼叫攻击"）解析为结构化 MissionPlan JSON，同时支持态势自然语言简报生成。
* 集成 **语音指挥模块（Voice Commander）**：基于 Web Speech API 实现语音→文本→NL Command→仿真的全链路语音控制。
* 构建 **3D 战场可视化系统**：Three.js + Canvas 2D 双渲染管线，支持俯视/跟随/FPV 多视角切换、**红外热成像（IR）后处理**、OODA 循环实时面板。
* 开发 **3D 空间音效引擎（Spatial Audio）**：基于 Web Audio API 的 **HRTF（头部传递函数）** 实现 UAV 引擎声/武器开火/爆炸/通信提示音的程序化合成与 3D 空间定位，独立于音频文件，全部声音程序化生成。
* 实现 **空间哈希网格（Spatial Hashing）拓扑优化**：将 O(n²) 通信拓扑计算降至 O(n)，支撑 50+ 节点实时组网。

* * *

**项目量化成果**

| 指标  | 基线 (纯规则 FSM) | LLM 增强后 | 提升  |
| --- | --- | --- | --- |
| 任务成功率 | 0% (0/7) | **88%** (7/8) | 0→88% |
| UAV 存活率 | 12.5% | **97%** | 7.8× |
| 拦截器击杀/run | 0   | **1.6** | ∞   |
| OODA 循环数/run | 26  | **706** | 27.2× |
| orient_depth | 0.0 (虚假 S 级) | 1.9 (真实 C 级) | 从虚假到真实 |
| 语义缓存命中率 | —   | **>70%** | —   |
| 单次仿真 LLM 成本 | —   | **$0.003** | —   |
| LLM 异常导致的系统崩溃 | —   | **0 次** | FSM 兜底保障 |
| 支持并发 UAV 数 | 8 (O(n²)瓶颈) | **50+** (空间哈希) | 6×+**项目角色**：核心架构师 / 多智能体系统负责人 |
