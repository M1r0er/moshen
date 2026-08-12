"""
墨参 · 对话路由
SSE 流式对话接口
"""
import json
from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from engines.dialogue_manager import get_dialogue_manager

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    project_id: str | None = None
    history: list[dict] = []


@router.post("")
async def chat(req: ChatRequest):
    """流式对话接口（SSE）"""
    manager = get_dialogue_manager()

    async def event_generator():
        async for sse_data in manager.chat_stream(
            user_input=req.message,
            history=req.history,
            project_id=req.project_id,
        ):
            # sse_data 已经是 "event: xxx\ndata: {...}\n\n" 格式
            # 解析为 sse_starlette 需要的格式
            lines = sse_data.strip().split("\n")
            event = ""
            data = ""
            for line in lines:
                if line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: "):
                    data = line[6:]
            yield {"event": event, "data": data}

    return EventSourceResponse(event_generator())


@router.get("/intents")
async def list_intents():
    """列出所有支持的意图类型"""
    from engines.intent_router import get_intent_router
    router_ = get_intent_router()
    return {"intents": router_.list_intents()}
