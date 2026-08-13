"""
墨参 · 对话管理路由
支持一个项目下多个独立对话（多线并行）
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from knowledge.project_kb import get_project_kb_manager

router = APIRouter(prefix="/api/conversations", tags=["conversation"])
kb = get_project_kb_manager()


class CreateConversationRequest(BaseModel):
    title: str = ""


class SaveConversationRequest(BaseModel):
    messages: list[dict]
    title: str | None = None


class RenameConversationRequest(BaseModel):
    title: str


@router.get("/{project_id}")
async def list_conversations(project_id: str):
    """列出项目的所有对话"""
    convs = kb.list_conversations(project_id)
    return {"conversations": convs}


@router.post("/{project_id}")
async def create_conversation(project_id: str, req: CreateConversationRequest):
    """创建新对话"""
    conv = kb.create_conversation(project_id, req.title)
    if not conv:
        raise HTTPException(404, "项目不存在")
    return conv


@router.get("/{project_id}/{conv_id}")
async def get_conversation(project_id: str, conv_id: str):
    """获取对话完整内容（含消息）"""
    conv = kb.get_conversation(project_id, conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")
    return conv


@router.put("/{project_id}/{conv_id}")
async def save_conversation(project_id: str, conv_id: str, req: SaveConversationRequest):
    """保存对话消息"""
    result = kb.save_conversation(project_id, conv_id, req.messages, req.title)
    if not result:
        raise HTTPException(404, "对话不存在")
    return {"success": True, "conversation": {
        "id": result["id"],
        "title": result["title"],
        "updated_at": result["updated_at"],
        "message_count": len(result.get("messages", [])),
    }}


@router.delete("/{project_id}/{conv_id}")
async def delete_conversation(project_id: str, conv_id: str):
    """删除对话"""
    success = kb.delete_conversation(project_id, conv_id)
    if not success:
        raise HTTPException(404, "对话不存在")
    return {"success": True}


@router.put("/{project_id}/{conv_id}/rename")
async def rename_conversation(project_id: str, conv_id: str, req: RenameConversationRequest):
    """重命名对话"""
    if not req.title.strip():
        raise HTTPException(400, "标题不能为空")
    result = kb.rename_conversation(project_id, conv_id, req.title.strip())
    if not result:
        raise HTTPException(404, "对话不存在")
    return {"success": True, "title": result["title"]}
