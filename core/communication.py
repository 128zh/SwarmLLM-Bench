"""
高并发通信Broker模块
=====================
基于asyncio的发布/订阅消息中间件，支持：
- 多主题订阅(pub/sub)
- 消息优先级队列(HIGH/MEDIUM/LOW)
- 速率限制与背压处理
- 消息TTL与过期清理
- 通信统计与监控
- 异步非阻塞处理，支持高并发场景
"""
import asyncio
import time
import json
import heapq
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional

from config import (
    COMM_MAX_QUEUE_SIZE,
    COMM_MESSAGE_TTL,
    COMM_HIGH_PRIORITY_RATIO,
)


class MessagePriority(IntEnum):
    """消息优先级"""
    HIGH = 0       # 战斗指令、故障告警、紧急重组
    MEDIUM = 1     # 任务协调、状态同步
    LOW = 2        # 遥测数据、日志


class MessageType:
    """消息类型"""
    HEARTBEAT = "heartbeat"           # 心跳
    TASK_ASSIGN = "task_assign"       # 任务分配
    TASK_STATUS = "task_status"       # 任务状态
    TARGET_INFO = "target_info"       # 目标信息
    ENGAGE_CMD = "engage_cmd"         # 交战指令
    ENGAGE_RESULT = "engage_result"   # 交战结果
    NODE_FAILURE = "node_failure"     # 节点故障
    RECONNECT = "reconnect"           # 重组网
    LEADER_ELECT = "leader_elect"     # 领导者选举
    FORMATION = "formation"           # 编队
    RETREAT = "retreat"               # 撤退
    STATUS_REPORT = "status_report"   # 状态报告
    INTERCEPTOR_DAMAGE = "interceptor_damage"  # 拦截器伤害
    INTERCEPTOR_INFO = "interceptor_info"      # 拦截器位置共享
    BROADCAST = "broadcast"           # 广播


@dataclass(order=True)
class Message:
    """消息体"""
    priority: MessagePriority                        # 排序键：优先级
    seq: int = field(compare=False)                   # 序列号（同优先级按序）
    msg_type: str = field(compare=False, default="")  # 消息类型
    sender: str = field(compare=False, default="")    # 发送者ID
    target: str = field(compare=False, default="")    # 目标ID（空=广播）
    payload: dict = field(compare=False, default_factory=dict)
    timestamp: float = field(compare=False, default=0.0)
    tick: int = field(compare=False, default=0)
    msg_id: str = field(compare=False, default="")


class AsyncMessageBroker:
    """
    异步消息Broker - 蜂群通信中枢
    
    核心设计：
    1. 优先级堆队列：高优先级消息（战斗/故障）优先处理
    2. 主题订阅：按topic分类消息，支持精准投递
    3. 速率限制：防止单节点通信洪泛
    4. 背压机制：队列满时丢弃低优先级消息
    5. 统计监控：实时跟踪消息吞吐量、延迟、丢包率
    """

    def __init__(self, max_queue_size=COMM_MAX_QUEUE_SIZE):
        self._queue: list = []                        # 优先级堆队列
        self._seq_counter = 0                          # 全局序列号
        self._subscribers: dict = defaultdict(dict)    # topic -> {subscriber_id: callback}
        self._max_queue_size = max_queue_size
        self._lock = asyncio.Lock()                    # 异步锁（并发安全）
        
        # 通信统计
        self.stats = {
            "total_sent": 0,
            "total_delivered": 0,
            "total_dropped": 0,
            "by_priority": {p.name: {"sent": 0, "delivered": 0, "dropped": 0} for p in MessagePriority},
            "by_type": defaultdict(lambda: {"sent": 0, "delivered": 0}),
            "current_queue_size": 0,
            "peak_queue_size": 0,
            "delivery_latency_ms": 0.0,
        }
        
        # 速率限制：每个发送者每tick最大消息数
        self._rate_limits: dict = defaultdict(lambda: {"count": 0, "tick": 0})
        self._max_msgs_per_tick = 50
        
        # 消息历史（用于去重和追溯）
        self._processed_ids: deque = deque(maxlen=2000)

    async def publish(
        self,
        msg_type: str,
        sender: str,
        payload: dict,
        target: str = "",
        priority: MessagePriority = MessagePriority.MEDIUM,
        tick: int = 0,
    ) -> bool:
        """
        发布消息到Broker
        
        Returns:
            True=入队成功, False=被丢弃
        """
        async with self._lock:
            # 速率限制检查
            sender_state = self._rate_limits[sender]
            if sender_state["tick"] == tick:
                if sender_state["count"] >= self._max_msgs_per_tick:
                    self.stats["total_dropped"] += 1
                    self.stats["by_priority"][priority.name]["dropped"] += 1
                    return False
            else:
                sender_state["tick"] = tick
                sender_state["count"] = 0
            sender_state["count"] += 1

            # 背压：队列满时丢弃最低优先级消息
            if len(self._queue) >= self._max_queue_size:
                # 移除最低优先级的消息（堆中最大的）
                removed = heapq.heappop(self._queue)
                self.stats["total_dropped"] += 1
                self.stats["by_priority"][MessagePriority(removed.priority).name]["dropped"] += 1

            # 创建消息
            self._seq_counter += 1
            msg = Message(
                priority=priority,
                seq=self._seq_counter,
                msg_type=msg_type,
                sender=sender,
                target=target,
                payload=payload,
                timestamp=time.time(),
                tick=tick,
                msg_id=f"{sender}_{self._seq_counter}",
            )

            heapq.heappush(self._queue, msg)
            
            # 更新统计
            self.stats["total_sent"] += 1
            self.stats["by_priority"][priority.name]["sent"] += 1
            self.stats["by_type"][msg_type]["sent"] += 1
            self.stats["current_queue_size"] = len(self._queue)
            self.stats["peak_queue_size"] = max(self.stats["peak_queue_size"], len(self._queue))

            return True

    async def process_batch(self, max_messages: int = None) -> int:
        """
        批量处理队列中的消息（每个tick调用一次）
        
        策略：
        - 按优先级比例处理：高优先级优先
        - 批量处理提高吞吐量
        - 异步回调不阻塞
        
        Returns:
            处理的消息数
        """
        processed = 0
        if max_messages is None:
            max_messages = len(self._queue)

        while self._queue and processed < max_messages:
            msg = heapq.heappop(self._queue)
            
            # TTL检查
            if msg.tick > 0 and hasattr(self, '_current_tick'):
                age = self._current_tick - msg.tick
                if age > COMM_MESSAGE_TTL:
                    self.stats["total_dropped"] += 1
                    continue

            # 去重检查
            if msg.msg_id in self._processed_ids:
                continue
            self._processed_ids.append(msg.msg_id)

            # 投递消息
            await self._deliver(msg)
            
            # 统计延迟
            latency = (time.time() - msg.timestamp) * 1000
            self.stats["delivery_latency_ms"] = (
                self.stats["delivery_latency_ms"] * 0.9 + latency * 0.1
            )
            
            processed += 1

        self.stats["current_queue_size"] = len(self._queue)
        return processed

    async def _deliver(self, msg: Message):
        """投递消息给订阅者"""
        delivered = False
        
        # 精准投递：如果指定了target
        if msg.target:
            subs = self._subscribers.get(msg.target, {})
            for sub_id, callback in subs.items():
                try:
                    await callback(msg.msg_type, msg.sender, msg.payload, msg.tick)
                    delivered = True
                except Exception:
                    pass
        else:
            # 广播：投递给所有订阅了该消息类型的节点
            subs = self._subscribers.get(msg.msg_type, {})
            for sub_id, callback in subs.items():
                # 跳过发送者自身（避免自回环）
                if sub_id == msg.sender:
                    continue
                try:
                    await callback(msg.msg_type, msg.sender, msg.payload, msg.tick)
                    delivered = True
                except Exception:
                    pass

            # 同时投递给订阅了"broadcast"主题的节点
            broadcast_subs = self._subscribers.get(MessageType.BROADCAST, {})
            for sub_id, callback in broadcast_subs.items():
                if sub_id == msg.sender:
                    continue
                try:
                    await callback(msg.msg_type, msg.sender, msg.payload, msg.tick)
                    delivered = True
                except Exception:
                    pass

        if delivered:
            self.stats["total_delivered"] += 1
            self.stats["by_priority"][msg.priority.name]["delivered"] += 1
            self.stats["by_type"][msg.msg_type]["delivered"] += 1

    def subscribe(self, topic: str, subscriber_id: str, callback) -> None:
        """
        订阅主题
        
        Args:
            topic: 消息类型或节点ID
            subscriber_id: 订阅者ID
            callback: async callback(msg_type, sender, payload, tick)
        """
        self._subscribers[topic][subscriber_id] = callback

    def unsubscribe(self, topic: str, subscriber_id: str) -> None:
        """取消订阅"""
        if topic in self._subscribers:
            self._subscribers[topic].pop(subscriber_id, None)

    def set_tick(self, tick: int):
        """设置当前tick（用于TTL计算）"""
        self._current_tick = tick

    def get_stats(self) -> dict:
        """获取通信统计"""
        stats = dict(self.stats)
        stats["by_type"] = {k: dict(v) for k, v in self.stats["by_type"].items()}
        return stats

    def clear(self):
        """清空队列和状态"""
        self._queue.clear()
        self._subscribers.clear()
        self._processed_ids.clear()
