"""
墨参 MoShen · 小说写作助手
FastAPI 主入口
"""
import os
import sys
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# 确保可以导入同目录下的模块
sys.path.insert(0, str(Path(__file__).parent))

from core.config import get_config_manager
from routes.chat import router as chat_router
from routes.project import router as project_router
from routes.files import router as files_router
from routes.workspace import router as workspace_router
from routes.knowledge import router as knowledge_router
from routes.settings_writer import router as settings_router
from routes.conversation import router as conversation_router
from routes.outline import router as outline_router
from routes.writing import router as writing_router
from routes.foreshadowing import router as foreshadowing_router
from routes.relations import router as relations_router
from routes.session import router as session_router
from routes.flow import router as flow_router
from routes.plot_points import router as plot_points_router

app = FastAPI(title="墨参 MoShen", version="0.6.8", description="小说写作助手")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载路由
app.include_router(chat_router)
app.include_router(project_router)
app.include_router(files_router)
app.include_router(workspace_router)
app.include_router(knowledge_router)
app.include_router(settings_router)
app.include_router(conversation_router)
app.include_router(outline_router)
app.include_router(writing_router)
app.include_router(foreshadowing_router)
app.include_router(relations_router)
app.include_router(session_router)
app.include_router(flow_router)
app.include_router(plot_points_router)


# ===== 项目会话租约校验 =====
# 已接入租约的调用方会在请求头携带 X-Moshen-Lease。若携带的是"已失效的陈旧租约"
# （项目被重新打开后旧窗口仍在发请求），则拒绝其写操作，避免旧窗口污染新会话。
# 未携带租约的请求保持放行，以兼容尚未接入租约的调用路径。
_SESSION_EXEMPT_PREFIXES = (
    "/api/session", "/api/config", "/api/workspace", "/api/health",
    "/api/projects", "/api/chat/intents", "/api/chat/models", "/docs", "/openapi.json",
)
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def enforce_session_lease(request, call_next):
    if request.method in _MUTATING_METHODS:
        path = request.url.path
        if path.startswith("/api/") and not path.startswith(_SESSION_EXEMPT_PREFIXES):
            from core.session import get_session_registry
            lease = request.headers.get("X-Moshen-Lease")
            if get_session_registry().is_stale(lease):
                return JSONResponse(
                    {"detail": "项目会话已过期（项目可能已被重新打开），请重新打开项目后再操作"},
                    status_code=409,
                )
    return await call_next(request)


# ===== 配置管理路由 =====

class ConfigUpdateRequest(BaseModel):
    default_api: dict = {}
    independent_keys: bool = False
    roles: dict = {}
    api_channels: list = []
    image_config: dict = {}


@app.get("/api/config")
async def get_config():
    """获取模型配置状态（包含默认API、开关、四角色）"""
    mgr = get_config_manager()
    return mgr.get_full_config()


@app.post("/api/config")
async def save_config(req: ConfigUpdateRequest):
    """保存模型配置"""
    mgr = get_config_manager()
    data = {
        "default_api": req.default_api,
        "independent_keys": req.independent_keys,
        "roles": req.roles,
        "api_channels": req.api_channels,
        "image_config": req.image_config,
    }
    mgr.save_config(data)
    return {"success": True, **mgr.get_full_config()}


@app.post("/api/config/test")
async def test_config(body: dict):
    """测试模型连接"""
    from core.llm_provider import LLMProvider
    from core.config import ModelConfig

    role = body.get("role", "DIALOGUE_PARTNER")
    mgr = get_config_manager()
    cfg = mgr.get_model(role)

    if cfg is None:
        return {"success": False, "error": "未找到可用配置"}

    try:
        provider = LLMProvider()
        result = await provider.generate(
            [{"role": "user", "content": "请回复'连接成功'四个字"}],
            role=role,
            max_tokens=20,
        )
        return {"success": True, "response": result.strip()}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ===== 健康检查 =====

@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "墨参 MoShen", "version": "0.6.8"}


# ===== 前端静态文件 =====

from core.resource_path import get_frontend_dir

frontend_dir = get_frontend_dir()

if frontend_dir.exists():
    # 挂载前端静态资源
    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")


@app.get("/")
async def index():
    """返回前端首页"""
    index_path = frontend_dir / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "墨参 MoShen 后端已启动，前端文件未找到"}


@app.get("/{full_path:path}")
async def catch_all(full_path: str):
    """SPA 回退：所有非 API 路径返回前端"""
    if full_path.startswith("api/"):
        raise HTTPException(404, "API not found")
    index_path = frontend_dir / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    raise HTTPException(404, "Not found")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8765,
        reload=True,
        reload_dirs=[str(Path(__file__).parent)],
    )
