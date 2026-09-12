"""
墨参 · 项目会话路由

前端在切换/打开项目时调用 /api/session/open 领取租约，并在后续请求中
通过 `X-Moshen-Lease` 头携带。若租约已因"项目被重新打开"而失效，
后端会拒绝该陈旧窗口的写入请求。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.session import get_session_registry
from knowledge.project_kb import get_project_kb_manager

router = APIRouter(prefix="/api/session", tags=["session"])


class OpenSessionRequest(BaseModel):
    project_id: str


class CloseSessionRequest(BaseModel):
    lease_id: str = ""


@router.post("/open")
async def open_session(req: OpenSessionRequest):
    """打开项目会话：发放新租约，旧租约立即失效"""
    project_id = (req.project_id or "").strip()
    if not project_id:
        raise HTTPException(400, "project_id 不能为空")
    if not get_project_kb_manager().get_project(project_id):
        raise HTTPException(404, "项目不存在")

    session = get_session_registry().open(project_id)
    return {
        "success": True,
        "project_id": session.project_id,
        "lease_id": session.lease_id,
    }


@router.post("/close")
async def close_session(req: CloseSessionRequest):
    """关闭项目会话（仅当租约有效时生效）"""
    return {"success": get_session_registry().close(req.lease_id)}


@router.get("/current")
async def current_session():
    """查询当前活跃会话"""
    registry = get_session_registry()
    return {
        "active": registry.has_active(),
        "project_id": registry.current_project_id(),
    }
