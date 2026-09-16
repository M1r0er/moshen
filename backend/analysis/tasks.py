"""
墨参 · 通用任务进度通道

统一进度事件契约（《加载与等待动画需求文档》§6.9 ①）：

    {
      "task_key": "relations.analyze",
      "task_name": "星图分析",
      "phase": "batch",
      "completed": 20,
      "total": 71,
      "unit": "批",
      "progress": 0.2817,          # 0–1，与 completed/total 冲突时以前者为准
      "status": "running",         # running / done / failed / cancelled
      "detail": "批次 20/71 分析中…",
      "message": "",               # failed 时的失败原因
      "updated_at": 1758000000.0   # Unix 秒，前端据此判定"等待响应"
    }

用途：为"阻塞式请求"型长流程任务（拆文分析、蒸馏学习、伏笔检测、回收检测、
知识搜索、本地知识蒸馏、爽点使用检查、肖像批量生成）提供进度回流。
task_id 由前端生成并随请求下发，前端用 task_id 订阅
GET /api/tasks/{task_id}/progress（SSE），后端只负责在通道上打点。
这样不必引入全局任务注册中心的生命周期管理（§6.9 方案一）。
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any, Dict, List, Optional

STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

_HISTORY_SIZE = 200
_MAX_CHANNELS = 40
_channels: Dict[str, "TaskChannel"] = {}


class TaskChannel:
    """单个任务的进度通道：历史回放 + 多订阅者广播"""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.task_key = ""
        self.task_name = ""
        self.status = STATUS_RUNNING
        self.updated_at = time.time()
        self.events: deque = deque(maxlen=_HISTORY_SIZE)
        self.listeners: set = set()

    def emit(self, **fields: Any) -> dict:
        """打一条进度点（字段为 None 的一律不下发，前端据此判断"总量未知"）"""
        if fields.get("task_key"):
            self.task_key = fields["task_key"]
        if fields.get("task_name"):
            self.task_name = fields["task_name"]

        status = fields.get("status") or self.status
        self.status = status

        payload: Dict[str, Any] = {
            "task_key": self.task_key,
            "task_name": self.task_name,
            "phase": fields.get("phase") or "",
            "status": status,
            "updated_at": time.time(),
        }
        for key in ("completed", "total", "unit", "progress", "detail", "message"):
            if fields.get(key) is not None:
                payload[key] = fields[key]

        self.updated_at = payload["updated_at"]
        self.events.append(payload)
        for listener in list(self.listeners):
            try:
                listener(payload)
            except Exception:
                pass
        return payload

    def finish(self, **fields: Any) -> dict:
        fields.setdefault("status", STATUS_DONE)
        if fields.get("progress") is None:
            fields["progress"] = 1.0
        return self.emit(**fields)

    def history(self) -> List[dict]:
        return list(self.events)

    def add_listener(self, callback) -> None:
        self.listeners.add(callback)

    def remove_listener(self, callback) -> None:
        self.listeners.discard(callback)


def open_channel(task_id: str, task_key: str = "", task_name: str = "") -> Optional[TaskChannel]:
    """按 task_id 建档。task_id 为空表示本次不追踪进度，返回 None。"""
    task_id = (task_id or "").strip()
    if not task_id:
        return None
    channel = _channels.get(task_id)
    if channel is None or channel.status != STATUS_RUNNING:
        channel = TaskChannel(task_id)
        _channels[task_id] = channel
        _evict_finished()
    if task_key:
        channel.task_key = task_key
    if task_name:
        channel.task_name = task_name
    return channel


def get_channel(task_id: str) -> Optional[TaskChannel]:
    return _channels.get((task_id or "").strip())


def drop_channel(task_id: str) -> None:
    _channels.pop((task_id or "").strip(), None)


def _evict_finished() -> None:
    """容量保护：只清理已结束的通道，正在跑的一律保留"""
    if len(_channels) <= _MAX_CHANNELS:
        return
    for key, channel in list(_channels.items()):
        if len(_channels) <= _MAX_CHANNELS // 2:
            break
        if channel.status != STATUS_RUNNING:
            _channels.pop(key, None)


class SingleStep:
    """单步阻塞任务的进度包装：0/1 → 1/1（§6.9 ② 表中"单步"一类）"""

    def __init__(self, task_id: str, task_key: str, task_name: str, unit: str = "项") -> None:
        self.channel = open_channel(task_id, task_key, task_name)
        self.unit = unit

    def begin(self, detail: str = "", total: int = 1) -> None:
        if self.channel:
            self.channel.emit(
                completed=0, total=total, unit=self.unit, phase="run", detail=detail
            )

    def ok(self, detail: str = "") -> None:
        if self.channel:
            self.channel.finish(
                completed=1, total=1, unit=self.unit, phase="done", detail=detail
            )

    def fail(self, message: str) -> None:
        if self.channel:
            self.channel.emit(status=STATUS_FAILED, message=message or "任务失败")
