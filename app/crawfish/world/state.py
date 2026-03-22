from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


@dataclass(frozen=True)
class WorldConfig:
    world_size: int = 10000
    view_radius: int = 30
    tick_ms: int = 2000  # 2秒一次，降低CPU负载
    max_users: int = 500  # 支持500用户
    inactive_timeout_sec: int = 300  # 5分钟不活跃才移除


@dataclass
class UserState:
    user_id: int
    x: int
    y: int
    updated_at: float = field(default_factory=lambda: time.time())
    last_seen: float = field(default_factory=lambda: time.time())


class WorldState:
    """
    2D 世界状态管理器

    空间哈希网格：将世界分成 CELL_SIZE×CELL_SIZE 的格子，
    每个格子维护该区域内的用户列表。
    get_visible() 从 O(n) 降到 O(1)。

    CELL_SIZE = view_radius = 30，确保只用检查相邻格子就能覆盖完整视野。
    """

    CELL_SIZE = 30  # 与 view_radius 保持一致

    def __init__(self, config: WorldConfig | None = None) -> None:
        self.config = config or WorldConfig()
        self.users: dict[int, UserState] = {}  # user_id -> UserState
        self.occupied: dict[tuple[int, int], int] = {}  # (x, y) -> user_id
        self._grid: dict[tuple[int, int], set[int]] = {}  # (gx, gy) -> set of user_ids
        self._lock = threading.Lock()

    def _grid_key(self, x: int, y: int) -> tuple[int, int]:
        """坐标 -> 格子坐标"""
        return (x // self.CELL_SIZE, y // self.CELL_SIZE)

    def _cell_users(self, gx: int, gy: int) -> set[int]:
        """获取格子内的用户ID集合（格子不存在则返回空集合）"""
        return self._grid.get((gx, gy), set())

    def _add_to_grid(self, state: UserState) -> None:
        """将用户加入网格"""
        gx, gy = self._grid_key(state.x, state.y)
        if (gx, gy) not in self._grid:
            self._grid[(gx, gy)] = set()
        self._grid[(gx, gy)].add(state.user_id)

    def _remove_from_grid(self, state: UserState) -> None:
        """将用户从网格移除"""
        gx, gy = self._grid_key(state.x, state.y)
        if (gx, gy) in self._grid:
            self._grid[(gx, gy)].discard(state.user_id)
            if not self._grid[(gx, gy)]:
                del self._grid[(gx, gy)]

    def spawn_user(self, user_id: int, last_x: int | None = None, last_y: int | None = None) -> UserState:
        """
        生成用户到世界。
        如果传入了 last_x/y，优先恢复到该坐标（断线重连场景）。
        """
        with self._lock:
            existing = self.users.get(user_id)
            if existing:
                existing.last_seen = time.time()
                return existing

            if len(self.users) >= self.config.max_users:
                raise ValueError("world is full")

            # 优先恢复上次坐标
            if last_x is not None and last_y is not None:
                if self._in_bounds(last_x, last_y) and (last_x, last_y) not in self.occupied:
                    state = UserState(user_id=user_id, x=last_x, y=last_y)
                    self.users[user_id] = state
                    self.occupied[(last_x, last_y)] = user_id
                    self._add_to_grid(state)
                    return state

            # 随机找空位
            for _ in range(self.config.max_users * 20):
                x = random.randrange(0, self.config.world_size)
                y = random.randrange(0, self.config.world_size)
                if (x, y) in self.occupied:
                    continue
                state = UserState(user_id=user_id, x=x, y=y)
                self.users[user_id] = state
                self.occupied[(x, y)] = user_id
                self._add_to_grid(state)
                return state

            raise ValueError("cannot find spawn point")

    def move_user(self, user_id: int, x: int, y: int) -> bool:
        """移动用户，返回是否成功"""
        with self._lock:
            state = self.users.get(user_id)
            if not state:
                raise KeyError("user not found")
            if not self._in_bounds(x, y):
                return False
            if (x, y) in self.occupied and self.occupied[(x, y)] != user_id:
                return False

            # 从旧格子移除
            self._remove_from_grid(state)
            # 从旧占用移除
            if (state.x, state.y) in self.occupied:
                self.occupied.pop((state.x, state.y), None)

            # 更新坐标
            state.x = x
            state.y = y
            state.updated_at = time.time()
            state.last_seen = state.updated_at

            # 加入新格子和新占用
            self.occupied[(x, y)] = user_id
            self._add_to_grid(state)
            return True

    def get_visible(
        self, user_id: int, view_radius: int | None = None
    ) -> list[UserState]:
        """
        获取视野内的所有用户。
        空间哈希优化：只检查用户所在格子及周围 N 个格子（N = ceil(range/CELL_SIZE)）。
        view_radius 默认为 config.view_radius，可动态调高/调低。
        """
        with self._lock:
            me = self.users.get(user_id)
            if not me:
                raise KeyError("user not found")
            me.last_seen = time.time()

            radius = view_radius if view_radius is not None else self.config.view_radius
            # 空间哈希：检查周围 NxN 格子
            n_cells = (radius // self.CELL_SIZE) + 1
            cx, cy = self._grid_key(me.x, me.y)
            visible: list[UserState] = []

            for dx in range(-n_cells, n_cells + 1):
                for dy in range(-n_cells, n_cells + 1):
                    for uid in self._cell_users(cx + dx, cy + dy):
                        if uid == user_id:
                            continue
                        s = self.users.get(uid)
                        if s is None:
                            continue
                        if abs(s.x - me.x) <= radius and abs(s.y - me.y) <= radius:
                            visible.append(s)

            return visible

    def get_nearby_users(self, user_id: int) -> list[UserState]:
        """获取附近用户（用于 encounter 检测），和 get_visible 相同实现"""
        return self.get_visible(user_id)

    def cleanup_inactive(self) -> int:
        """清理不活跃用户，返回清理数量"""
        cutoff = time.time() - self.config.inactive_timeout_sec
        removed = 0
        with self._lock:
            stale_ids = [uid for uid, state in self.users.items() if state.last_seen < cutoff]
            for uid in stale_ids:
                state = self.users.pop(uid, None)
                if state:
                    self.occupied.pop((state.x, state.y), None)
                    self._remove_from_grid(state)
                    removed += 1
        return removed

    def _in_bounds(self, x: int, y: int) -> bool:
        size = self.config.world_size
        return 0 <= x < size and 0 <= y < size

    def bulk_init_from_db(self, user_positions: list[tuple[int, int, int]]) -> int:
        """
        启动时从 DB 批量初始化世界状态。
        user_positions: [(user_id, x, y), ...]
        返回初始化用户数。
        """
        count = 0
        with self._lock:
            for user_id, x, y in user_positions:
                if user_id in self.users:
                    continue
                if self._in_bounds(x, y) and (x, y) not in self.occupied:
                    state = UserState(user_id=user_id, x=x, y=y)
                    self.users[user_id] = state
                    self.occupied[(x, y)] = user_id
                    self._add_to_grid(state)
                    count += 1
        return count
