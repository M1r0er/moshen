"""
墨参 · 项目会话租约

借鉴"路径不等于授权"的思路：项目 ID 只是定位符，真正决定"当前这一窗口是否有权
写入这个项目"的是一次会话租约（lease）。

典型场景：用户关闭项目 A 又重新打开 A（或打开另一个项目 B），旧窗口里未完成的
请求不应再写入新会话的项目数据。做法是：

- 每次打开项目发放一个新的 lease_id；
- 同一时刻只保留"当前活跃租约"，重新打开即让旧租约失效；
- 写入请求携带租约；若携带的是已失效（陈旧）租约，则拒绝写入。

未携带租约的请求保持放行（向后兼容），只有"明确携带了过期租约"才拦截，
避免在尚未接入租约的调用路径上造成误伤。
"""
import threading
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class Session:
    """一次项目会话"""
    lease_id: str
    project_id: str


class SessionRegistry:
    """会话租约登记处（进程内唯一权威）"""

    def __init__(self):
        self._lock = threading.RLock()
        self._leases: dict[str, str] = {}       # lease_id -> project_id
        self._current_lease: str | None = None  # 当前活跃租约

    def open(self, project_id: str) -> Session:
        """打开项目：发放新租约，并让旧租约立即失效"""
        project_id = (project_id or "").strip()
        if not project_id:
            raise ValueError("project_id 不能为空")
        with self._lock:
            lease_id = uuid.uuid4().hex
            self._leases = {lease_id: project_id}
            self._current_lease = lease_id
            return Session(lease_id=lease_id, project_id=project_id)

    def close(self, lease_id: str) -> bool:
        """关闭会话：仅当租约当前有效时生效"""
        with self._lock:
            if lease_id in self._leases:
                del self._leases[lease_id]
                if self._current_lease == lease_id:
                    self._current_lease = None
                return True
            return False

    def is_active(self, lease_id: str | None) -> bool:
        """该租约是否为当前有效租约"""
        if not lease_id:
            return False
        with self._lock:
            return lease_id in self._leases

    def is_stale(self, lease_id: str | None) -> bool:
        """该租约是否为"已失效但格式像租约"的陈旧租约

        仅当当前存在活跃会话、且请求携带了非活跃租约时判定为陈旧。
        未携带租约（None/空）不算陈旧，以保持向后兼容。
        """
        if not lease_id:
            return False
        with self._lock:
            if self._current_lease is None:
                return False
            return lease_id not in self._leases

    def current_project_id(self) -> str | None:
        with self._lock:
            if self._current_lease:
                return self._leases.get(self._current_lease)
            return None

    def has_active(self) -> bool:
        with self._lock:
            return self._current_lease is not None


_registry = SessionRegistry()


def get_session_registry() -> SessionRegistry:
    return _registry
