"""
墨参 · 对话路由
SSE 流式对话接口
"""
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from engines.dialogue_manager import get_dialogue_manager
from engines.mention_tasks import MentionTaskError, prepare_task_context
from core.config import get_config_manager, MODEL_ROLES

router = APIRouter(prefix="/api/chat", tags=["chat"])


class MentionRef(BaseModel):
    type: str
    id: str
    title: str = ""
    task: str | None = None


class TaskSpec(BaseModel):
    key: str
    target_ids: list[str] = []


class ChapterScope(BaseModel):
    mode: str                      # "range" | "latest"
    start: int | None = None
    end: int | None = None
    n: int | None = None


class ChatRequest(BaseModel):
    message: str
    project_id: str | None = None
    history: list[dict] = []
    model: str | None = None        # 指定模型名称，None/"auto" 为自动选择
    role: str | None = None         # 指定职能角色，None/"auto" 为自动选择
    # v0.11.0：结构化引用与任务，旧客户端不传时走原有纯文本路径
    references: list[MentionRef] = []
    task: TaskSpec | None = None
    chapter_scope: ChapterScope | None = None


@router.post("")
async def chat(req: ChatRequest):
    """流式对话接口（SSE）"""
    manager = get_dialogue_manager()

    # 任务材料在建立 SSE 流之前解析完成；非法输入直接以 HTTP 状态码返回
    context_material = ""
    coverage = None
    if req.references or req.task or req.chapter_scope:
        try:
            task_ctx = prepare_task_context(
                project_id=req.project_id or "",
                references=[r.model_dump() for r in req.references],
                task=req.task.model_dump() if req.task else None,
                chapter_scope=req.chapter_scope.model_dump() if req.chapter_scope else None,
            )
            context_material = task_ctx.get("material", "")
            coverage = task_ctx.get("coverage")
        except MentionTaskError as e:
            raise HTTPException(status_code=e.status_code, detail={"code": e.code, "message": e.message})

    async def event_generator():
        # chat_stream 直接产出 {"event": ..., "data": ...} 结构，
        # 由 EventSourceResponse 统一序列化，避免二次拼接/拆解。
        async for event in manager.chat_stream(
            user_input=req.message,
            history=req.history,
            project_id=req.project_id,
            model_override=req.model,
            role_override=req.role,
            context_material=context_material,
            coverage=coverage,
        ):
            yield event

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

    # 模型 → 所属渠道名称映射（用于前端展示）
    model_to_channel: dict[str, str] = {}
    for ch in cm.api_channels:
        ch_name = ch.get("name", "")
        for m in ch.get("models", []):
            if m and m.strip():
                model_to_channel.setdefault(m.strip(), ch_name)

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
        "model_to_channel": model_to_channel,
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
