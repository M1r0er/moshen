"""
墨参 MoShen · 小说写作助手
FastAPI 主入口
"""
import os
import sys
import time
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# 确保可以导入同目录下的模块
sys.path.insert(0, str(Path(__file__).parent))

from core.config import get_config_manager, MODEL_ROLES
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
from routes.tasks import router as tasks_router

app = FastAPI(title="墨参 MoShen", version="0.8.4", description="小说写作助手")

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
app.include_router(tasks_router)


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


_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


@app.middleware("http")
async def enforce_local_host(request, call_next):
    host = request.headers.get("host", "")
    if host.startswith("["):
        hostname = host.split("]")[0][1:]
    else:
        hostname = host.split(":")[0]
    if hostname.strip().lower() not in _ALLOWED_HOSTS:
        return JSONResponse({"detail": "仅允许本机访问"}, status_code=400)
    return await call_next(request)


# ===== 配置管理路由 =====

class ConfigUpdateRequest(BaseModel):
    default_api: dict = {}
    independent_keys: bool = False
    roles: dict = {}
    api_channels: list = []
    image_config: dict = {}
    # 显式要求清除的密钥目标：role:XXX / image / channel:<id|name> / default
    # （密钥留空只表示"保持不变"；清空必须显式走这里，既避免误抹，也保留删除能力）
    clear_keys: list[str] = []


@app.get("/api/config")
async def get_config():
    """获取模型配置状态（包含默认API、开关、四角色）

    所有 api_key 均已掩码为 "********"，明文密钥不会下发到前端。
    """
    mgr = get_config_manager()
    return mgr.get_full_config()


@app.post("/api/config")
async def save_config(req: ConfigUpdateRequest):
    """保存模型配置

    密钥留空或回传掩码 = 保持原值；需要清空请通过 clear_keys 指定目标。
    """
    mgr = get_config_manager()
    data = {
        "default_api": req.default_api,
        "independent_keys": req.independent_keys,
        "roles": req.roles,
        "api_channels": req.api_channels,
        "image_config": req.image_config,
        "clear_keys": req.clear_keys,
    }
    mgr.save_config(data)
    return {"success": True, **mgr.get_full_config()}


class ApplyChannelRequest(BaseModel):
    """把某个 API 渠道应用到指定职能"""
    role: str
    channel: str


@app.post("/api/config/apply-channel")
async def apply_channel(req: ApplyChannelRequest):
    """把某个 API 渠道的配置应用到指定职能

    在服务端完成密钥复制，前端无需（也无法）持有明文密钥。
    """
    mgr = get_config_manager()
    try:
        return {"success": True, **mgr.apply_channel_to_role(req.role, req.channel)}
    except ValueError as e:
        raise HTTPException(400, str(e))


class ConfigTestRequest(BaseModel):
    """连通性自检请求

    target: role（单个职能，默认）| image（生图模型）| all（全部一次性自检）
    """
    target: str = "role"
    role: str | None = None


async def _probe_role(role: str) -> dict:
    """探测某个职能（或 DEFAULT 通用）的聊天接口是否可用"""
    from core.llm_provider import LLMProvider

    mgr = get_config_manager()
    cfg = mgr.get_model(role)
    label = "通用（默认渠道）" if role == "DEFAULT" else MODEL_ROLES.get(role, role)
    if cfg is None:
        return {"target": "role", "role": role, "name": label, "configured": False,
                "success": False, "detail": "未找到可用配置，请先填写 Base URL 与 API Key"}

    provider = LLMProvider()
    t0 = time.perf_counter()
    base = {"target": "role", "role": role, "name": label, "configured": True,
            "model": cfg.model, "base_url": cfg.base_url}
    try:
        result = await provider.generate(
            [{"role": "user", "content": "请回复'连接成功'四个字"}],
            role=role,
            max_tokens=20,
        )
        return {**base, "success": True, "detail": (result or "").strip() or "已连通",
                "elapsed": round(time.perf_counter() - t0, 2)}
    except Exception as e:
        return {**base, "success": False, "detail": str(e)[:300],
                "elapsed": round(time.perf_counter() - t0, 2)}


async def _probe_image() -> dict:
    """探测生图模型是否可用（使用无效尺寸探测，不真实出图、不产生费用）"""
    from core.llm_provider import LLMProvider

    cfg = get_config_manager().image_config or {}
    configured = bool(cfg.get("enabled") and cfg.get("api_key") and cfg.get("base_url"))
    base = {"target": "image", "name": "生图模型（肖像绘制）",
            "model": cfg.get("model") or "", "base_url": cfg.get("base_url") or "",
            "configured": configured}
    if not configured:
        return {**base, "success": False, "detail": "未启用，或未填写 Base URL / API Key"}
    res = await LLMProvider().probe_image()
    return {**base, "success": bool(res.get("ok")), "detail": res.get("detail", ""),
            "elapsed": res.get("elapsed")}


@app.post("/api/config/test")
async def test_config(req: ConfigTestRequest):
    """测试模型连通性：单个职能 / 生图 / 全部"""
    if req.target == "image":
        return await _probe_image()
    if req.target == "all":
        items = [await _probe_role("DEFAULT")]
        for role in MODEL_ROLES:
            items.append(await _probe_role(role))
        items.append(await _probe_image())
        return {"success": all(i.get("success") for i in items), "items": items}
    return await _probe_role(req.role or "DIALOGUE_PARTNER")


# ===== 健康检查 =====

@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "墨参 MoShen", "version": "0.8.4"}


# ===== 前端静态文件 =====

from core.resource_path import get_frontend_dir

frontend_dir = get_frontend_dir()

if frontend_dir.exists():
    # 挂载前端静态资源（禁用缓存：绿色版升级后必须立即生效，避免复用旧页面）
    app.mount(
        "/static",
        StaticFiles(directory=str(frontend_dir)),
        name="static",
    )


# 禁止 HTML/资源被 Electron 磁盘缓存复用，否则升级绿色版后仍会显示旧界面
_NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


@app.get("/")
async def index():
    """返回前端首页"""
    index_path = frontend_dir / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path), headers=_NO_CACHE_HEADERS)
    return {"message": "墨参 MoShen 后端已启动，前端文件未找到"}


@app.get("/{full_path:path}")
async def catch_all(full_path: str):
    """SPA 回退：所有非 API 路径返回前端"""
    if full_path.startswith("api/"):
        raise HTTPException(404, "API not found")
    index_path = frontend_dir / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path), headers=_NO_CACHE_HEADERS)
    raise HTTPException(404, "Not found")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8765,
        reload=True,
        reload_dirs=[str(Path(__file__).parent)],
    )
