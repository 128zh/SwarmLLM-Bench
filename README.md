SwarmLLM-Bench: LLM-Enhanced Multi-Agent Cooperative Control Simulation Platform
SwarmLLM-Bench：大语言模型驱动的多智能体协同控制仿真平台

https://github.com/user-attachments/assets/f81fa67c-1477-4ca0-8559-a341273e5de0

![Python Version](https://img.shields.io/badge/Python-3.9%2B-blue)
![Framework](https://img.shields.io/badge/Framework-FastAPI-green)
![License](https://img.shields.io/badge/License-MIT-yellow)
![Paper](https://img.shields.io/badge/Paper-SwarmLLM--Bench-orange)

Highlights | 核心亮点
[English]
SwarmLLM-Bench is an open-source simulation platform bridging the gap between Large Language Models (LLMs) and physically constrained multi-agent systems. Unlike traditional text-only agents, our platform integrates LLMs into the OODA (Observe-Orient-Decide-Act) loop for real-time UAV swarm coordination. Key innovations include:
FSM + LLM Hybrid Harness Architecture: Ensures 100% safety with deterministic FSM fallback while leveraging LLM for emergent tactical reasoning.
4-Layer Safety Harness: Strict validation of Role, State, Confidence, and Safety Rules to prevent LLM hallucinations in physical environments.
OODA-Driven Emergence: Quantifiable enhancement in decision density (27x OODA loops) and survival rate (97%) in adversarial scenarios.
High-Fidelity Physics: Built on Gymnasium, simulating realistic aerodynamics, battery drain, and communication delays.
[中文]
SwarmLLM-Bench 是一个旨在弥合大语言模型（LLM）与物理约束多智能体系统之间鸿沟的开源仿真平台。与传统的纯文本智能体不同，本平台将 LLM 深度集成到 OODA（观察-调整-决策-行动）循环中，用于实时无人机蜂群协同。核心创新包括：
FSM + LLM 混合 Harness 架构：在利用 LLM 进行涌现式战术推理的同时，通过确定性 FSM 兜底，确保物理执行的绝对安全。
四层安全护栏（Harness）：对角色、状态、置信度和安全规则进行严格校验，彻底杜绝 LLM 在物理环境中的幻觉与危险指令。
OODA 驱动的战术涌现：在对抗场景中，决策密度（OODA循环频次）提升 27 倍，无人机平均存活率高达 97%。
高保真物理仿真：基于 Gymnasium 构建，真实模拟空气动力学、电量消耗及通信延迟等物理约束。
️ Architecture | 系统架构
[English]
The platform adopts a 3-layer decoupled architecture:
DeepAgent Layer (Cognitive): Handles high-level intent parsing and long-horizon tactical planning via LLM.
HybridUAV Layer (Physical): Executes real-time flight control under strict physical constraints.
LLMClient Layer (Bridge): Responsible for bidirectional mapping (Symbolization & Grounding) and enforcing the 4-layer safety harness.
[中文]
本平台采用三层解耦架构设计：
DeepAgent 层（认知大脑）：通过 LLM 负责高层意图理解与长程战术规划。
HybridUAV 层（物理实体）：在严格的物理约束下执行实时飞行控制。
LLMClient 层（语义-物理桥梁）：负责双向映射（符号化与物理落地），并强制执行四层安全护栏机制。
(建议在此处插入你的架构流程图)

Quick Start | 快速上手
[English]
Get up and running in 3 steps:
bash

编辑



# 1. Clone the repository
git clone https://github.com/[YourUsername]/SwarmLLM-Bench.git
cd SwarmLLM-Bench

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the simulation
python main.py --config configs/mvp.yaml
[中文]
只需 3 步即可运行仿真：
bash

编辑



# 1. 克隆仓库
git clone https://github.com/[YourUsername]/SwarmLLM-Bench.git
cd SwarmLLM-Bench

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动仿真
python main.py --config configs/mvp.yaml
Benchmarks | 性能基准
[English]
Comparison between Pure FSM Baseline (P1) and LLM-Driven Swarm (P3):
表格
Metric	P1 (Pure FSM)	P3 (LLM-Driven)	Improvement
Mission Success Rate	0%	88%	+88%
UAV Survival Rate	12.5%	97%	️ +84.5%
OODA Loops / Run	26	706	27x
[中文]
纯规则基线（P1）与 LLM 驱动蜂群（P3）的核心指标对比：
表格
评估指标	P1 (纯 FSM)	P3 (LLM 驱动)	提升幅度
任务成功率	0%	88%	+88%
无人机存活率	12.5%	97%	️ +84.5%
OODA循环频次	26	706	27倍
Citation | 引用
[English]
If you find SwarmLLM-Bench useful in your research, please consider citing:
bibtex

编辑



@article{swarmllm2024,
  title={SwarmLLM-Bench: LLM-Enhanced Multi-Agent Cooperative Control Simulation Platform},
  author={[Your Name]},
  journal={arXiv preprint},
  year={2024}
}
[中文]
如果 SwarmLLM-Bench 对您的研究有所帮助，请考虑引用：
bibtex

编辑



@article{swarmllm2024,
  title={SwarmLLM-Bench：大语言模型驱动的多智能体协同控制仿真平台},
  author={[张杰]},

https://github.com/user-attachments/assets/370754e2-b71a-4d9b-8743-79b3f2c3024e


  journal={arXiv preprint},
  year={2024}
}
Contributing | 参与贡献
[English]
Contributions are welcome! Please read our Contributing Guidelines before submitting a Pull Request.
[中文]
欢迎提交 Issue 和 Pull Request！参与贡献前请先阅读 贡献指南。
License | 开源协议
This project is licensed under the MIT License. See the LICENSE file for details.
本项目基于 MIT 协议开源，详情请参阅 LICENSE 文件。
