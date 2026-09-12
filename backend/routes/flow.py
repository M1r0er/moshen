"""
墨参 · 创作流程路由

把"蓝图 → 草稿 → 审稿 → 修稿 → 定稿"做成显式、可查询、带门禁的章节状态。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from knowledge.flow import STAGE_LABELS, STAGES, get_flow_manager
from knowledge.project_kb import get_project_kb_manager

router = APIRouter(prefix="/api/flow", tags=["flow"])


class SetStageRequest(BaseModel):
    stage: str
    note: str = ""
    force: bool = False


class ReviewRequest(BaseModel):
    report_filename: str = ""
    note: str = ""


def _chapter_key(vol_id: str, ch_id: str) -> str:
    return f"{vol_id}/{ch_id}"


@router.get("/{project_id}")
async def get_flow(project_id: str):
    """获取项目全部章节的创作阶段"""
    if not get_project_kb_manager().get_project(project_id):
        raise HTTPException(404, "项目不存在")
    manager = get_flow_manager()
    return {
        "stages": STAGES,
        "labels": STAGE_LABELS,
        "chapters": manager.list_chapters(project_id),
    }


@router.get("/{project_id}/volumes/{vol_id}/chapters/{ch_id}")
async def get_chapter_flow(project_id: str, vol_id: str, ch_id: str):
    """获取单个章节的创作阶段"""
    entry = get_flow_manager().get_chapter(project_id, _chapter_key(vol_id, ch_id))
    return {"chapter": entry}


@router.post("/{project_id}/volumes/{vol_id}/chapters/{ch_id}/stage")
async def set_chapter_stage(project_id: str, vol_id: str, ch_id: str, req: SetStageRequest):
    """推进章节阶段（默认受门禁约束；force=True 为作者显式重置）"""
    result = get_flow_manager().set_stage(
        project_id, _chapter_key(vol_id, ch_id), req.stage, req.note, req.force
    )
    if not result.get("success"):
        raise HTTPException(409, result.get("error", "阶段推进被拒绝"))
    return result


@router.post("/{project_id}/volumes/{vol_id}/chapters/{ch_id}/review")
async def mark_reviewed(project_id: str, vol_id: str, ch_id: str, req: ReviewRequest):
    """把审稿报告应用到章节：标记为已审稿（审稿 → 修稿 闭环的入口）"""
    note = req.note or (f"审稿报告: {req.report_filename}" if req.report_filename else "标记已审稿")
    result = get_flow_manager().set_stage(
        project_id, _chapter_key(vol_id, ch_id), "reviewed", note
    )
    if not result.get("success"):
        raise HTTPException(409, result.get("error", "标记已审稿失败"))
    return result
