"""
墨参 · 爽爆点路由

为大纲页的「爽爆点」子页面提供增删改查、使用次数记录，
以及"让 AI 检查已有正文、判断各爽爆点已用次数"的建议接口。
AI 只给建议，是否采纳由作者决定。
"""
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.llm_provider import get_llm_provider
from core.utils import parse_json_response
from knowledge.plot_points import (
    PP_STATUSES,
    PP_TYPES,
    get_plot_point_manager,
)
from knowledge.project_kb import get_project_kb_manager

router = APIRouter(prefix="/api/plot-points", tags=["plot-points"])

# 送入模型判断的正文上限（字符）
_ANALYZE_MAX_CHARS = 40000


class PlotPointRequest(BaseModel):
    title: str
    type: str = "爽点"
    trope: str = ""
    content: str = ""
    segment: str = ""
    status: str = "pending"
    used_count: int = 0
    fatigue_threshold: int | None = None
    planned_count: int | None = None


class UpdatePlotPointRequest(BaseModel):
    title: str | None = None
    type: str | None = None
    trope: str | None = None
    content: str | None = None
    segment: str | None = None
    status: str | None = None
    used_count: int | None = None
    fatigue_threshold: int | None = None
    planned_count: int | None = None


class UsageRequest(BaseModel):
    delta: int = 1
    set_to: int | None = None
    note: str = ""
    chapter_key: str = ""


class ThresholdRequest(BaseModel):
    fatigue_threshold: int


def _require_project(project_id: str) -> None:
    if not get_project_kb_manager().get_project(project_id):
        raise HTTPException(404, "项目不存在")


@router.get("/{project_id}")
async def list_plot_points(project_id: str):
    """获取爽爆点列表、阈值与统计"""
    _require_project(project_id)
    manager = get_plot_point_manager()
    data = manager.list_points(project_id)
    data["stats"] = manager.stats(project_id)
    return data


@router.post("/{project_id}")
async def create_plot_point(project_id: str, req: PlotPointRequest):
    """新增爽爆点"""
    _require_project(project_id)
    if not req.title.strip():
        raise HTTPException(400, "标题不能为空")
    point = get_plot_point_manager().create_point(project_id, req.model_dump())
    if not point:
        raise HTTPException(400, "创建失败")
    return {"success": True, "point": point}


@router.put("/{project_id}/threshold")
async def set_threshold(project_id: str, req: ThresholdRequest):
    """设置项目的审美疲劳阈值（同一类型累计使用达到该值即提示）

    注意：该路由必须注册在 /{project_id}/{pp_id} 之前，否则会被其抢先匹配。
    """
    _require_project(project_id)
    value = get_plot_point_manager().set_fatigue_threshold(project_id, req.fatigue_threshold)
    return {"success": True, "fatigue_threshold": value}


@router.put("/{project_id}/{pp_id}")
async def update_plot_point(project_id: str, pp_id: str, req: UpdatePlotPointRequest):
    """更新爽爆点（含作者直接修改已用次数）"""
    _require_project(project_id)
    point = get_plot_point_manager().update_point(
        project_id, pp_id, req.model_dump(exclude_unset=True)
    )
    if not point:
        raise HTTPException(404, "爽爆点不存在")
    return {"success": True, "point": point}


@router.delete("/{project_id}/{pp_id}")
async def delete_plot_point(project_id: str, pp_id: str):
    """删除爽爆点"""
    _require_project(project_id)
    if not get_plot_point_manager().delete_point(project_id, pp_id):
        raise HTTPException(404, "爽爆点不存在")
    return {"success": True}


@router.post("/{project_id}/{pp_id}/used")
async def record_usage(project_id: str, pp_id: str, req: UsageRequest):
    """记录一次使用：`delta` 自增自减，或 `set_to` 直接设定已用次数"""
    _require_project(project_id)
    point = get_plot_point_manager().add_usage(
        project_id, pp_id,
        delta=req.delta, set_to=req.set_to,
        note=req.note, chapter_key=req.chapter_key,
    )
    if not point:
        raise HTTPException(404, "爽爆点不存在")
    return {"success": True, "point": point}


def _build_chapters_digest(project_id: str) -> tuple[str, int, bool]:
    """把已写正文拼成可送入模型的文本

    Returns:
        (文本, 总字符数, 是否被截断)
    """
    chapters = get_project_kb_manager().get_all_chapters_text(project_id)
    parts = []
    for c in chapters:
        body = (c.get("content") or "").strip()
        if not body:
            continue
        label = f"第{c.get('ch_number')}章 {c.get('ch_title') or ''}".strip()
        parts.append(f"### {label}\n{body}")
    full = "\n\n".join(parts)
    total = len(full)
    if total > _ANALYZE_MAX_CHARS:
        return full[:_ANALYZE_MAX_CHARS], total, True
    return full, total, False


@router.post("/{project_id}/analyze-usage")
async def analyze_usage(project_id: str):
    """让 AI 检查已有正文，判断每个爽爆点大致已用过几次

    只返回建议，不直接改写数据；作者在前端逐条确认后才写入。
    """
    _require_project(project_id)
    manager = get_plot_point_manager()
    data = manager.list_points(project_id)
    points = data.get("points", [])
    if not points:
        return {"suggestions": [], "overall": "当前项目还没有爽爆点，先添加后再检查。"}

    text, total_chars, truncated = _build_chapters_digest(project_id)
    if not text.strip():
        return {"suggestions": [], "overall": "当前项目还没有正文内容，无法检查使用次数。"}

    catalog = "\n".join(
        f"- id={p['id']} | {p.get('type')} | {p.get('trope') or '无套路'} | {p.get('title')} | 当前记录已用 {p.get('used_count') or 0} 次"
        for p in points
    )

    prompt = (
        "你是网文审稿编辑。下面是本书已有的正文，以及作者维护的「爽爆点/爆点」清单。\n"
        "请逐条判断每个爽爆点在正文中实际出现过几次（按套路/桥段实际落地计次，"
        "同一章内同一手法重复出现只算一次），并给出证据所在章节与简短理由。\n\n"
        "判断原则：\n"
        "1. 只依据正文中确实写出的内容计次，不要凭标题猜测；\n"
        "2. 正文中找不到对应桥段的，计 0 次；\n"
        "3. 证据请写成「第X章」这样便于作者核对的形式；\n"
        "4. 不要修改清单本身。\n\n"
        f"## 爽爆点清单\n{catalog}\n\n"
        f"## 已有正文{'（因长度限制仅提供前一部分）' if truncated else ''}\n{text}\n\n"
        "请只输出 JSON，格式：\n"
        '{"overall": "整体说明", "suggestions": ['
        '{"id": "清单中的id", "used_count": 0, "evidence": "第X章", "reason": "理由"}]}'
    )

    llm = get_llm_provider()
    try:
        raw = await llm.generate(
            [{"role": "user", "content": prompt}],
            role="STRUCTURE_ANALYST",
            temperature=0.2,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        raise HTTPException(500, f"AI 检查失败: {e}")

    parsed = parse_json_response(raw)
    if not isinstance(parsed, dict):
        raise HTTPException(500, "AI 返回内容无法解析为 JSON，请重试")

    known = {p["id"]: p for p in points}
    suggestions = []
    for item in parsed.get("suggestions", []) or []:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id", "")).strip()
        point = known.get(pid)
        if not point:
            continue
        try:
            suggested = max(0, int(item.get("used_count", 0)))
        except (TypeError, ValueError):
            suggested = 0
        current = int(point.get("used_count") or 0)
        suggestions.append({
            "id": pid,
            "title": point.get("title", ""),
            "type": point.get("type", ""),
            "segment": point.get("segment", ""),
            "current_used_count": current,
            "suggested_used_count": suggested,
            "changed": suggested != current,
            "evidence": str(item.get("evidence", "")).strip(),
            "reason": str(item.get("reason", "")).strip(),
        })

    suggestions.sort(key=lambda s: (not s["changed"], s["title"]))

    return {
        "suggestions": suggestions,
        "overall": str(parsed.get("overall", "")).strip(),
        "chapters_examined_chars": min(total_chars, _ANALYZE_MAX_CHARS),
        "truncated": truncated,
        "types": PP_TYPES,
        "statuses": PP_STATUSES,
    }
