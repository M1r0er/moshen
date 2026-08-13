"""
墨参 · 对话路由
SSE 流式对话接口
"""
import json
from fastapi import APIRouter, UploadFile, File
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from engines.dialogue_manager import get_dialogue_manager
from core.config import get_config_manager, MODEL_ROLES

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    project_id: str | None = None
    history: list[dict] = []
    model: str | None = None        # 指定模型名称，None/"auto" 为自动选择
    role: str | None = None         # 指定职能角色，None/"auto" 为自动选择


@router.post("")
async def chat(req: ChatRequest):
    """流式对话接口（SSE）"""
    manager = get_dialogue_manager()

    async def event_generator():
        async for sse_data in manager.chat_stream(
            user_input=req.message,
            history=req.history,
            project_id=req.project_id,
            model_override=req.model,
            role_override=req.role,
        ):
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


@router.get("/models")
async def list_available_models():
    """获取可用的模型列表和职能信息（用于前端下拉选择）"""
    cm = get_config_manager()
    default_models = cm.get_available_models("DEFAULT")

    roles_info = {}
    for role_key, role_desc in MODEL_ROLES.items():
        models = cm.get_available_models(role_key) if cm.independent_keys else default_models
        roles_info[role_key] = {
            "label": role_desc,
            "models": models,
        }

    return {
        "independent_keys": cm.independent_keys,
        "default_models": default_models,
        "roles": roles_info,
    }


@router.post("/upload-file")
async def upload_chat_file(file: UploadFile = File(...)):
    """上传文件并返回文本内容，用于对话中引用文件"""
    from routes.knowledge import parse_uploaded_file
    try:
        content = await parse_uploaded_file(file)
        return {
            "filename": file.filename,
            "content": content,
            "size": len(content),
        }
    except Exception as e:
        return {"error": str(e), "filename": file.filename}
