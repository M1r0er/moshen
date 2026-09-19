"""墨参 · 角色页路由

角色档案、阶段图（阶段节点 + 事件连线）、AI 分阶段解析、AI 观察收件箱。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from analysis.character_stage import (
    build_stage_messages,
    collect_character_text,
    parse_stage_result,
)
from knowledge.character_kb import LEVELS, LEVEL_LABELS, get_character_kb

router = APIRouter(prefix="/api/characters", tags=["characters"])


def _kb():
    return get_character_kb()


def _require_project(project_id: str) -> None:
    from knowledge.project_kb import get_project_kb_manager

    if not get_project_kb_manager().get_project(project_id):
        raise HTTPException(404, "项目不存在")


# ===== 请求模型 =====

class CreateCharacterRequest(BaseModel):
    name: str
    aliases: list[str] = []
    profile: str = ""
    source: str = "manual"


class UpdateCharacterRequest(BaseModel):
    name: str | None = None
    aliases: list[str] | None = None
    profile: str | None = None
    color: str | None = None
    locked: bool | None = None


class StagePayload(BaseModel):
    id: str | None = None
    title: str = ""
    note: str = ""
    chapter: str = ""
    x: float = 0
    y: float = 0


class StageListRequest(BaseModel):
    stages: list[StagePayload] = []


class EventPayload(BaseModel):
    id: str | None = None
    from_id: str = ""
    to_id: str = ""
    label: str = ""


class EventListRequest(BaseModel):
    events: list[EventPayload] = []


class AnalyzeStageRequest(BaseModel):
    level: str = "standard"   # coarse | standard | fine
    task_id: str = ""


class AcceptObservationRequest(BaseModel):
    mode: str = "note"        # note | stage | alias
    stage_id: str = ""
    title: str = ""
    note: str = ""


# ===== 读取 =====

@router.get("/{project_id}")
async def list_characters(project_id: str):
    """角色列表 + 档位选项"""
    _require_project(project_id)
    return {
        "characters": _kb().list_characters(project_id),
        "levels": [{"value": k, "label": LEVEL_LABELS[k]} for k in LEVELS],
    }


@router.get("/{project_id}/observations")
async def list_observations(project_id: str, status: str = ""):
    """AI 观察收件箱（展平所有角色，可按状态过滤）"""
    _require_project(project_id)
    kb = _kb()
    all_obs = kb.list_observations(project_id)
    pending = [o for o in all_obs if o.get("status") == "pending"]
    return {
        "observations": kb.list_observations(project_id, status) if status else all_obs,
        "pending_count": len(pending),
        "total": len(all_obs),
    }


# ===== 角色 CRUD =====

@router.post("/{project_id}/characters")
async def create_character(project_id: str, req: CreateCharacterRequest):
    """新建角色（设定页「添加到角色」也走这里，source=settings）"""
    _require_project(project_id)
    kb = _kb()
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(400, "角色名不能为空")

    existing = kb.find_by_name(project_id, name)
    if existing:
        # 已存在：把新别名并进去，返回既有角色（让「添加到角色」幂等）
        merged = list(existing.get("aliases") or [])
        for a in req.aliases or []:
            a = str(a).strip()
            if a and a != existing.get("name") and a not in merged:
                merged.append(a)
        if merged != (existing.get("aliases") or []):
            kb.update(project_id, existing["id"], {"aliases": merged})
        char = kb.get(project_id, existing["id"])
        return {"success": True, "created": False, "character": char}

    char = kb.create(project_id, name, req.aliases, req.profile, req.source)
    if char is None:
        raise HTTPException(400, "角色创建失败")
    return {"success": True, "created": True, "character": char}


@router.put("/{project_id}/characters/{cid}")
async def update_character(project_id: str, cid: str, req: UpdateCharacterRequest):
    _require_project(project_id)
    char = _kb().update(project_id, cid, req.model_dump())
    if char is None:
        raise HTTPException(404, "角色不存在")
    return {"success": True, "character": char}


@router.delete("/{project_id}/characters/{cid}")
async def delete_character(project_id: str, cid: str):
    _require_project(project_id)
    if not _kb().delete(project_id, cid):
        raise HTTPException(404, "角色不存在")
    return {"success": True}


@router.put("/{project_id}/characters/{cid}/stages")
async def set_stages(project_id: str, cid: str, req: StageListRequest):
    """整体保存阶段节点（拖拽摆位 / 增删）"""
    _require_project(project_id)
    stages = [
        {"id": s.id, "title": s.title, "note": s.note, "chapter": s.chapter, "x": s.x, "y": s.y}
        for s in req.stages
    ]
    char = _kb().set_stages(project_id, cid, stages)
    if char is None:
        raise HTTPException(404, "角色不存在")
    return {"success": True, "character": char}


@router.put("/{project_id}/characters/{cid}/events")
async def set_events(project_id: str, cid: str, req: EventListRequest):
    """整体保存事件连线"""
    _require_project(project_id)
    events = [
        {"id": e.id, "from": e.from_id, "to": e.to_id, "label": e.label}
        for e in req.events
    ]
    char = _kb().set_events(project_id, cid, events)
    if char is None:
        raise HTTPException(404, "角色不存在")
    return {"success": True, "character": char}


# ===== AI 分阶段解析 =====

@router.post("/{project_id}/characters/{cid}/analyze")
async def analyze_character_stages(project_id: str, cid: str, req: AnalyzeStageRequest):
    """调用模型把角色拆成阶段（覆盖该角色现有阶段图）

    单步阻塞任务：进度按 0/1 → 1/1 上报。
    """
    from analysis.tasks import SingleStep
    from core.llm_provider import get_llm_provider
    from knowledge.project_kb import get_project_kb_manager

    _require_project(project_id)
    kb = _kb()
    char = kb.get(project_id, cid)
    if char is None:
        raise HTTPException(404, "角色不存在")

    step = SingleStep(req.task_id, "characters.stages", "角色分阶段解析")
    step.begin("正在筛选该角色相关章节…")

    text = collect_character_text(get_project_kb_manager(), project_id, char)
    if not text:
        step.fail("正文中没有出现该角色（或本书还没有正文）")
        raise HTTPException(400, "正文中没有出现该角色，无法解析阶段")

    step.begin("正在请求模型解析阶段…")
    level = req.level if req.level in LEVELS else "standard"
    try:
        raw = await get_llm_provider().generate(
            build_stage_messages(char, text, level), role="STRUCTURE_ANALYST", max_tokens=4096
        )
    except Exception as e:
        step.fail(f"{type(e).__name__}: {str(e)[:200]}")
        raise

    parsed = parse_stage_result(raw, char)
    if not parsed["stages"]:
        step.fail("模型未返回可用的阶段结构")
        raise HTTPException(500, "模型未返回可用的阶段结构，请重试或换更细的档位")

    kb.set_stages(project_id, cid, parsed["stages"])
    char = kb.set_events(project_id, cid, parsed["events"])
    step.ok(f"已生成 {len(parsed['stages'])} 个阶段、{len(parsed['events'])} 条阶段事件")
    return {
        "success": True,
        "level": level,
        "stages": len(parsed["stages"]),
        "events": len(parsed["events"]),
        "character": char,
    }


# ===== AI 观察收件箱 =====

@router.post("/{project_id}/observations/{cid}/{oid}/accept")
async def accept_observation(project_id: str, cid: str, oid: str, req: AcceptObservationRequest):
    """采纳观察：note（追加到已有阶段）/ stage（记为新阶段）/ alias（并入别名）"""
    _require_project(project_id)
    char = _kb().accept_observation(
        project_id, cid, oid,
        mode=req.mode, stage_id=req.stage_id, title=req.title, note=req.note,
    )
    if char is None:
        raise HTTPException(404, "角色或观察不存在（或目标阶段已被删除）")
    return {"success": True, "character": char}


@router.post("/{project_id}/observations/{cid}/{oid}/ignore")
async def ignore_observation(project_id: str, cid: str, oid: str):
    _require_project(project_id)
    obs = _kb().set_observation_status(project_id, cid, oid, "ignored")
    if obs is None:
        raise HTTPException(404, "角色或观察不存在")
    return {"success": True, "observation": obs}
