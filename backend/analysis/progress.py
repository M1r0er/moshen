"""
墨参 · 分析进度回调封装
提供统一的进度推送接口，支持 SSE 流式输出与历史回放。
"""
from collections import deque
from typing import Callable


class ProgressTracker:
    """分析进度追踪器

    封装进度消息的收集、历史回放与回调分发。
    用于 SSE 重连时回放最近进度，以及多订阅者广播。
    """

    def __init__(self, history_size: int = 300):
        self._history: deque[str] = deque(maxlen=history_size)
        self._listeners: set[Callable[[str], None]] = set()

    def push(self, message: str) -> None:
        """推送一条进度消息"""
        self._history.append(message)
        for listener in list(self._listeners):
            try:
                listener(message)
            except Exception:
                # 监听器异常不影响其他监听者
                pass

    def add_listener(self, callback: Callable[[str], None]) -> Callable[[], None]:
        """添加进度监听器，返回取消订阅函数"""
        self._listeners.add(callback)
        return lambda: self._listeners.discard(callback)

    def get_history(self) -> list[str]:
        """获取历史进度消息（用于 SSE 重连回放）"""
        return list(self._history)

    def clear(self) -> None:
        """清空历史与监听器"""
        self._history.clear()
        self._listeners.clear()
