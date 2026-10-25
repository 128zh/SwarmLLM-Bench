"""
蜂群多无人机协同作战仿真系统 - 全局配置
"""
import math


# ============ 仿真参数 ============
SIM_FPS = 10                  # 仿真帧率（每秒tick数）
SIM_TIME_SCALE = 1.0          # 仿真时间倍率
MAP_WIDTH = 10000.0           # 地图宽度(米)
MAP_HEIGHT = 10000.0          # 地图高度(米)

# ============ 无人机参数 ============
DEFAULT_UAV_COUNT = 8         # 默认无人机数量
UAV_MAX_SPEED = 80.0          # 最大速度 m/s
UAV_MAX_ALTITUDE = 500.0      # 最大飞行高度 m
UAV_DETECTION_RANGE = 3000.0  # 传感器探测范围 m
UAV_WEAPON_RANGE = 2000.0     # 武器射程 m
UAV_MAX_BATTERY = 100.0       # 满电量
UAV_BATTERY_DRAIN = 0.05      # 每tick电量消耗
UAV_HEALTH_MAX = 100.0        # 满血量

# ============ 通信参数 ============
COMM_HEARTBEAT_INTERVAL = 2   # 心跳间隔(ticks)
COMM_HEARTBEAT_TIMEOUT = 6    # 心跳超时(ticks)，超过即判定故障
COMM_MAX_QUEUE_SIZE = 500     # 每个主题消息队列最大长度
COMM_BROADCAST_RANGE = 5000.0 # 广播通信范围 m（超出则需中继）
COMM_HIGH_PRIORITY_RATIO = 0.5 # 高优先级消息处理比例
COMM_MESSAGE_TTL = 30         # 消息存活时间(ticks)

# ============ 蜂群网络参数 ============
SWARM_LEADER_ELECTION = True  # 是否启用领导者选举
SWARM_MAX_NEIGHBORS = 5       # 每个节点最大邻居数
SWARM_NETWORK_RANGE = 4000.0  # 组网通信范围 m

# ============ 任务参数 ============
MISSION_TARGET_COUNT = 3      # 任务目标数量
MISSION_TARGET_HEALTH = 100.0 # 目标血量
MISSION_TARGET_DAMAGE = 35.0  # 每次攻击造成的伤害

# ============ 角色定义 ============
UAV_ROLES = {
    "leader":  {"speed": 0.9, "detection": 1.2, "weapon": 0.5, "desc": "指挥机"},
    "scout":   {"speed": 1.3, "detection": 1.5, "weapon": 0.3, "desc": "侦察机"},
    "striker": {"speed": 1.0, "detection": 0.8, "weapon": 1.5, "desc": "攻击机"},
    "relay":   {"speed": 0.8, "detection": 0.6, "weapon": 0.2, "desc": "中继机"},
}

# ============ Web服务器参数 ============
WEB_HOST = "0.0.0.0"
WEB_PORT = 8010
WS_BROADCAST_INTERVAL = 0.1   # WebSocket推送间隔(秒)


def distance(pos1: dict, pos2: dict) -> float:
    """计算两点间距离"""
    return math.sqrt(
        (pos1["x"] - pos2["x"]) ** 2 +
        (pos1["y"] - pos2["y"]) ** 2 +
        (pos1.get("z", 0) - pos2.get("z", 0)) ** 2
    )


def distance_sq(pos1: dict, pos2: dict) -> float:
    """计算两点间距离平方（避免sqrt，用于范围比较）"""
    return (
        (pos1["x"] - pos2["x"]) ** 2 +
        (pos1["y"] - pos2["y"]) ** 2 +
        (pos1.get("z", 0) - pos2.get("z", 0)) ** 2
    )


# ============ 空间哈希网格参数 ============
SPATIAL_HASH_CELL_SIZE = max(SWARM_NETWORK_RANGE, UAV_DETECTION_RANGE, COMM_BROADCAST_RANGE)

# ============ 环境参数 ============
ENV_WIND_LEVEL = 0              # 0-9级风力, 0=无风
ENV_PRECIPITATION = "none"      # none / rain / snow
ENV_LIGHTING = "day"            # day / dusk / night
ENV_TERRAIN_TYPE = "mixed"      # mixed / mountain / urban / water
ENV_WIND_EFFECT_ON_UAV = True   # 风是否影响UAV移动
ENV_WIND_SPEED_MAP = [0, 0.5, 1.5, 3.0, 5.5, 8.0, 11.0, 14.0, 17.5, 21.0]  # 各级风速(m/s)
ENV_PRECIP_REDUCTION = {"none": 1.0, "rain": 0.7, "snow": 0.6}  # 降水对探测距离的影响系数

# ============ 录制与回放参数 ============
RECORDING_MAX_TICKS = 10000     # 内存缓冲区最大tick数
RECORDING_DIR = "recordings"    # 录制文件存储目录

# ============ 场景参数 ============
SCENARIO_DIR = "scenarios"      # 场景模板目录

# ============ 操控参数 ============
MANUAL_CONTROL_UPDATE_HZ = 50   # 手动控制命令更新率(Hz)
MANUAL_CONTROL_MAX_SPEED = 80.0 # 手动控制最大速度(m/s)

# ============ LLM / DeepAgent 参数 ============
LLM_ENABLED = True                   # 是否启用LLM增强 (默认关闭，向后兼容)
LLM_PROVIDER = "deepseek"               # LLM提供商: claude | openai | local
LLM_MODEL = "deepseek-v4-flash"  # 默认模型 (低延迟)
LLM_MODEL_CRITICAL = "deepseek-v4-flash"   # 关键决策模型 (高智能)
LLM_API_KEY = 'sk-xxxx'                    # API密钥 (DeepSeek)
LLM_BASE_URL = 'https://api.deepseek.com'                   # API基础URL (DeepSeek)
LLM_TIMEOUT_MS = 800                   # LLM调用超时(毫秒)
LLM_MAX_RETRIES = 2                    # 失败重试次数
LLM_CACHE_ENABLED = True               # 是否启用语义缓存
LLM_CACHE_SIZE = 500                   # 缓存条目上限
LLM_CALL_INTERVAL_TICKS = 8            # LLM调用间隔(ticks) — 轻量模式
LLM_CALL_INTERVAL_LEADER = 5           # Leader的LLM调用间隔
LLM_CALL_INTERVAL_STRIKER = 15         # Striker的LLM调用间隔
LLM_CALL_INTERVAL_SCOUT = 10           # Scout的LLM调用间隔
LLM_TOKEN_BUDGET_PER_TICK = 200        # 每UAV每tick最大输入token
LLM_OUTPUT_MAX_TOKENS = 100            # 每次LLM输出最大token

# ============ Prompt模板路径 ============
PROMPT_DIR = "prompts"                 # 战术Prompt模板目录
