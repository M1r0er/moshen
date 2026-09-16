"""
墨参 · 通用任务进度通道（SSE）

前端用 task_id 订阅：GET /api/tasks/{task_id}/progress
  - 会先回放该任务已有的历史进度事件
  - 之后持续推送 event: progress
  - 静默超过 HEARTBEAT_SECONDS 时推送一次 event: heartbeat，让前端能区分
    "连接活着但模型在思考"与"连接已断"（对应 §8.2 需修复的既有缺陷 3）
  - 收到终态（done / failed / cancelled）后主动结束流

若订阅时该 task_id 尚未建档（阻塞式请求是"先订阅、后 POST"），会预建通道，
避免因时序问题拿不到最早的进度事件。
"""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from analysis.tasks import STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED, get_channel, open_channel

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

HEARTBEAT_SECONDS = 10.0
TERMINAL = (STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED)

_SSE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/{task_id}/progress")
async def task_progress(task_id: str):
    """订阅任务进度（SSE）"""
    channel = get_channel(task_id) or open_channel(task_id)
    if channel is None:
        # task_id 为空等异常情况，直接给一个空流，前端靠自身超时兜底
        async def empty():
            yield _sse("heartbeat", {"updated_at": time.time()})
        return StreamingResponse(empty(), media_type="text/event-stream", headers=_SSE_HEADERS)

    async def event_generator():
        for event in channel.history():
            yield _sse("progress", event)
            if event.get("status") in TERMINAL:
                return

        queue: asyncio.Queue = asyncio.Queue()

        def listener(payload: dict):
            try:
                queue.put_nowait(payload)
            except Exception:
                pass

        channel.add_listener(listener)
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield _sse("heartbeat", {"updated_at": time.time()})
                    continue
                yield _sse("progress", payload)
                if payload.get("status") in TERMINAL:
                    return
        except asyncio.CancelledError:
            pass
        finally:
            channel.remove_listener(listener)

    return StreamingResponse(
        event_generator(), media_type="text/event-stream", headers=_SSE_HEADERS
    )
