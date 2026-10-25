"""
蜂群自组网模块
==============
实现无人机蜂群的Mesh网络拓扑管理，核心功能：
- 拓扑发现：基于通信范围自动建立邻居关系
- 心跳监控：监测每个节点存活状态
- 故障检测：心跳超时自动判定节点失效
- 自动重组网：节点失效后自动重建网络拓扑
- 领导者选举：失效节点为leader时自动重新选举
- 任务接管：失效节点的任务自动重新分配
- 中继路由：支持多跳中继通信路径
"""
import asyncio
import math
from collections import defaultdict
from typing import Optional
from dataclasses import dataclass, field

from config import (
    COMM_HEARTBEAT_INTERVAL,
    COMM_HEARTBEAT_TIMEOUT,
    SWARM_NETWORK_RANGE,
    SWARM_MAX_NEIGHBORS,
    SWARM_LEADER_ELECTION,
    SPATIAL_HASH_CELL_SIZE,
    distance,
    distance_sq,
)
from core.communication import AsyncMessageBroker, MessagePriority, MessageType
from core.uav_agent import UAVAgent, UAVRole, UAVState


class SpatialHashGrid:
    """
    空间哈希网格 — 将 O(n²) 拓扑计算降为 O(n)

    工作原理：
    1. 将地图按 cell_size 划分网格
    2. 每个实体按其位置存入对应格子
    3. 查询邻居时仅检查相邻9个格子（3×3区域）
    4. 配合距离平方比较，消除 sqrt 开销

    复杂度：O(n) 平均，O(n²) 最坏（所有实体在同一格子）
    """

    def __init__(self, cell_size: float):
        self.cell_size = cell_size
        self._cells: dict = {}

    def clear(self):
        """清空网格"""
        self._cells.clear()

    def _cell_key(self, x: float, y: float) -> tuple:
        """计算位置所在的格子坐标"""
        return (int(x // self.cell_size), int(y // self.cell_size))

    def insert(self, entity_id: str, position: dict):
        """将实体插入网格"""
        key = self._cell_key(position["x"], position["y"])
        if key not in self._cells:
            self._cells[key] = []
        self._cells[key].append(entity_id)

    def query_nearby(self, position: dict, entity_positions: dict) -> set:
        """
        查询指定位置附近的实体ID（3×3 相邻格子）

        Args:
            position: 查询中心位置
            entity_positions: {entity_id: position} 用于精确距离过滤

        Returns:
            相邻格子中所有实体ID的集合
        """
        cx, cy = self._cell_key(position["x"], position["y"])
        candidates = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                cell = self._cells.get((cx + dx, cy + dy))
                if cell:
                    candidates.update(cell)
        return candidates


@dataclass
class NodeInfo:
    """网络节点信息"""
    uav_id: str
    role: str
    position: dict
    last_heartbeat_tick: int = 0
    alive: bool = True
    health: float = 100.0
    battery: float = 100.0
    state: str = "INIT"
    neighbors: set = field(default_factory=set)


class SwarmNetwork:
    """
    蜂群网络管理器
    
    核心机制：
    1. 拓扑发现：每个tick根据位置和通信范围更新邻居关系
    2. 心跳监测：通过Broker收集心跳，超时节点标记为疑似故障
    3. 故障确认：连续超时后确认故障，触发重组网流程
    4. 重组网流程：
       a. 移除故障节点
       b. 重新计算网络拓扑
       c. 如果故障节点是leader，执行领导者选举
       d. 通知所有存活节点新的拓扑和leader
       e. 收集故障节点的未完成任务，交给MissionCoordinator重新分配
    5. 网络分割检测：检测网络是否分裂为多个不连通子网
    """

    def __init__(self, broker: AsyncMessageBroker):
        self.broker = broker
        self.nodes: dict[str, NodeInfo] = {}           # uav_id -> NodeInfo
        self.topology: dict[str, set] = defaultdict(set) # uav_id -> {neighbor_ids}
        self.leader_id: Optional[str] = None
        self._reconnect_count = 0
        self._leader_changes = 0
        self._network_splits = 0
        
        # 故障检测
        self._suspected: dict[str, int] = {}  # uav_id -> 连续超时次数

        # 空间哈希网格（优化拓扑计算，消除O(n²)瓶颈）
        self._spatial_grid = SpatialHashGrid(SPATIAL_HASH_CELL_SIZE)
        self._network_range_sq = SWARM_NETWORK_RANGE ** 2  # 预计算平方范围，避免sqrt

        # 订阅心跳和故障消息
        self.broker.subscribe(MessageType.HEARTBEAT, "swarm_network", self._on_heartbeat)
        self.broker.subscribe(MessageType.NODE_FAILURE, "swarm_network", self._on_node_failure)

        # 任务重分配回调（由SimulationEngine设置）
        self.on_node_failed_callback = None

    def register_uav(self, uav: UAVAgent):
        """注册无人机到网络"""
        self.nodes[uav.id] = NodeInfo(
            uav_id=uav.id,
            role=uav.role.value,
            position=dict(uav.position),
        )
        uav.neighbors = set()

    async def _on_heartbeat(self, msg_type: str, sender: str, payload: dict, tick: int):
        """处理心跳消息"""
        if sender not in self.nodes:
            return
        node = self.nodes[sender]
        node.last_heartbeat_tick = tick
        node.alive = True
        node.position = dict(payload.get("position", node.position))
        node.health = payload.get("health", node.health)
        node.battery = payload.get("battery", node.battery)
        node.state = payload.get("state", node.state)
        # 清除疑似故障
        self._suspected.pop(sender, None)

    async def _on_node_failure(self, msg_type: str, sender: str, payload: dict, tick: int):
        """处理节点故障通知"""
        failed_id = payload.get("uav_id")
        if failed_id and failed_id in self.nodes:
            await self._handle_node_failure(failed_id, tick, payload.get("reason", "unknown"))

    async def update_topology(self, tick: int, uavs: dict[str, UAVAgent]):
        """
        每tick更新网络拓扑
        
        1. 更新节点位置
        2. 基于通信范围重新计算邻居
        3. 检测心跳超时
        4. 处理故障节点
        """
        # 更新位置和存活状态
        for uid, uav in uavs.items():
            if uid in self.nodes:
                self.nodes[uid].position = dict(uav.position)
                self.nodes[uid].alive = uav.alive
                self.nodes[uid].health = uav.health
                self.nodes[uid].battery = uav.battery
                self.nodes[uid].state = uav.state.name

        # 计算通信拓扑（空间哈希网格 + 距离平方，O(n) 替代 O(n²)）
        alive_nodes = {uid: n for uid, n in self.nodes.items() if n.alive}
        new_topology: dict[str, set] = defaultdict(set)

        # 构建空间哈希网格
        self._spatial_grid.clear()
        entity_positions = {}
        for uid, n in alive_nodes.items():
            self._spatial_grid.insert(uid, n.position)
            entity_positions[uid] = n.position

        # 仅查询邻近格子中的候选节点（而非所有节点对）
        range_sq = self._network_range_sq
        for uid, n in alive_nodes.items():
            candidates = self._spatial_grid.query_nearby(n.position, entity_positions)
            for nid in candidates:
                if nid <= uid:  # 去重（每对只处理一次）
                    continue
                if nid not in alive_nodes:
                    continue
                # 用距离平方比较避免sqrt
                if distance_sq(n.position, alive_nodes[nid].position) <= range_sq:
                    new_topology[uid].add(nid)
                    new_topology[nid].add(uid)

        # 限制最大邻居数（保留最近的，使用距离平方排序）
        for uid in new_topology:
            if len(new_topology[uid]) > SWARM_MAX_NEIGHBORS:
                self_pos = self.nodes[uid].position
                neighbors_with_dist = [
                    (distance_sq(self_pos, self.nodes[nid].position), nid)
                    for nid in new_topology[uid]
                ]
                neighbors_with_dist.sort()
                new_topology[uid] = {nid for _, nid in neighbors_with_dist[:SWARM_MAX_NEIGHBORS]}

        self.topology = new_topology

        # 更新每个UAV的邻居信息
        for uid, neighbors in new_topology.items():
            if uid in uavs:
                uavs[uid].neighbors = set(neighbors)
                self.nodes[uid].neighbors = set(neighbors)

        # 心跳超时检测
        await self._check_heartbeats(tick, uavs)

    async def _check_heartbeats(self, tick: int, uavs: dict[str, UAVAgent]):
        """检查心跳超时"""
        for uid, node in list(self.nodes.items()):
            if not node.alive:
                continue
            if uid not in uavs or not uavs[uid].alive:
                continue

            time_since_heartbeat = tick - node.last_heartbeat_tick
            if time_since_heartbeat > COMM_HEARTBEAT_TIMEOUT:
                self._suspected[uid] = self._suspected.get(uid, 0) + 1
                # 连续2次超时确认故障
                if self._suspected[uid] >= 2:
                    await self._handle_node_failure(uid, tick, "heartbeat_timeout")

    async def _handle_node_failure(self, failed_id: str, tick: int, reason: str):
        """
        处理节点故障 - 核心重组网逻辑
        
        流程：
        1. 标记节点为故障
        2. 从拓扑中移除
        3. 如果是leader，执行选举
        4. 通知所有存活节点
        5. 触发任务重分配
        """
        if failed_id not in self.nodes:
            return

        node = self.nodes[failed_id]
        if not node.alive:
            return  # 已经处理过了

        node.alive = False
        self._suspected.pop(failed_id, None)

        # 从拓扑中移除
        self.topology.pop(failed_id, None)
        for uid in self.topology:
            self.topology[uid].discard(failed_id)

        self._reconnect_count += 1

        # 如果故障节点是leader，重新选举
        need_leader_election = (self.leader_id == failed_id)
        if need_leader_election and SWARM_LEADER_ELECTION:
            await self._elect_leader(tick)

        # 通知所有存活节点重组网
        alive_nodes = [uid for uid, n in self.nodes.items() if n.alive]
        await self.broker.publish(
            MessageType.RECONNECT, "swarm_network",
            {
                "failed_node": failed_id,
                "reason": reason,
                "alive_nodes": alive_nodes,
                "topology": {uid: list(neighbors) for uid, neighbors in self.topology.items()},
                "new_leader": self.leader_id,
                "tick": tick,
            },
            priority=MessagePriority.HIGH,
            tick=tick,
        )

        # 广播故障通知（确保所有节点都知道）
        await self.broker.publish(
            MessageType.NODE_FAILURE, "swarm_network",
            {
                "uav_id": failed_id,
                "reason": reason,
                "tick": tick,
                "reconnect_count": self._reconnect_count,
            },
            priority=MessagePriority.HIGH,
            tick=tick,
        )

        # 触发任务重分配回调
        if self.on_node_failed_callback:
            await self.on_node_failed_callback(failed_id, tick)

        # 检测网络分割
        await self._check_network_partition(tick)

    async def _elect_leader(self, tick: int):
        """
        领导者选举算法
        
        选举策略：
        1. 优先选择role=leader的存活节点
        2. 其次选择health最高、battery最高的节点
        3. 如果没有leader角色节点，选striker中能力最强的
        """
        alive_nodes = [
            (uid, n) for uid, n in self.nodes.items() if n.alive
        ]
        if not alive_nodes:
            self.leader_id = None
            return

        # 按优先级排序：role优先级 > health > battery
        role_priority = {"leader": 0, "striker": 1, "scout": 2, "relay": 3}
        alive_nodes.sort(key=lambda x: (
            role_priority.get(x[1].role, 9),
            -x[1].health,
            -x[1].battery,
        ))

        new_leader = alive_nodes[0][0]
        old_leader = self.leader_id
        self.leader_id = new_leader
        self._leader_changes += 1

        # 广播选举结果
        await self.broker.publish(
            MessageType.LEADER_ELECT, "swarm_network",
            {
                "leader_id": new_leader,
                "old_leader": old_leader,
                "reason": "election",
                "tick": tick,
            },
            priority=MessagePriority.HIGH,
            tick=tick,
        )

    async def _check_network_partition(self, tick: int):
        """检测网络是否分割为多个不连通子网"""
        alive_nodes = {uid for uid, n in self.nodes.items() if n.alive}
        if len(alive_nodes) < 2:
            return

        # BFS检测连通分量
        visited = set()
        components = []

        for start in alive_nodes:
            if start in visited:
                continue
            # BFS
            component = set()
            queue = [start]
            while queue:
                node = queue.pop(0)
                if node in visited:
                    continue
                visited.add(node)
                component.add(node)
                for neighbor in self.topology.get(node, set()):
                    if neighbor not in visited:
                        queue.append(neighbor)
            components.append(component)

        if len(components) > 1:
            self._network_splits += 1
            # 通知网络分割
            await self.broker.publish(
                MessageType.BROADCAST, "swarm_network",
                {
                    "event": "network_partition",
                    "components": [list(c) for c in components],
                    "tick": tick,
                },
                priority=MessagePriority.HIGH,
                tick=tick,
            )

    def set_initial_leader(self, leader_id: str):
        """设置初始领导者"""
        self.leader_id = leader_id
        if leader_id in self.nodes:
            self.nodes[leader_id].role = "leader"

    def get_network_status(self) -> dict:
        """获取网络状态摘要"""
        alive_count = sum(1 for n in self.nodes.values() if n.alive)
        total_edges = sum(len(neighbors) for neighbors in self.topology.values()) // 2
        avg_neighbors = total_edges / max(alive_count, 1)

        # 网络连通性
        alive_nodes = {uid for uid, n in self.nodes.items() if n.alive}
        connected = True
        if alive_nodes:
            visited = set()
            start = next(iter(alive_nodes))
            queue = [start]
            while queue:
                node = queue.pop(0)
                if node in visited:
                    continue
                visited.add(node)
                for neighbor in self.topology.get(node, set()):
                    if neighbor not in visited:
                        queue.append(neighbor)
            connected = (visited == alive_nodes)

        return {
            "total_nodes": len(self.nodes),
            "alive_nodes": alive_count,
            "failed_nodes": len(self.nodes) - alive_count,
            "leader_id": self.leader_id,
            "topology_edges": total_edges,
            "avg_neighbors": round(avg_neighbors, 2),
            "network_connected": connected,
            "reconnect_count": self._reconnect_count,
            "leader_changes": self._leader_changes,
            "network_splits": self._network_splits,
            "topology": {uid: list(n) for uid, n in self.topology.items()},
            "nodes": {
                uid: {
                    "role": n.role,
                    "alive": n.alive,
                    "health": round(n.health, 1),
                    "battery": round(n.battery, 1),
                    "state": n.state,
                    "neighbors": list(n.neighbors),
                    "position": dict(n.position),
                }
                for uid, n in self.nodes.items()
            },
        }

    def find_communication_path(self, src: str, dst: str) -> list:
        """
        查找从src到dst的通信路径（BFS最短路径）
        用于中继路由
        """
        if src == dst:
            return [src]
        if src not in self.topology or dst not in self.topology:
            return []

        visited = {src}
        queue = [(src, [src])]
        while queue:
            node, path = queue.pop(0)
            for neighbor in self.topology.get(node, set()):
                if neighbor == dst:
                    return path + [neighbor]
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, path + [neighbor]))
        return []  # 不可达
