"""
墨参 · 写作页路由
卷/章管理、正文保存、伏笔检测入口
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from knowledge.project_kb import get_project_kb_manager
from knowledge.flow import get_flow_manager

router = APIRouter(prefix="/api/writing", tags=["writing"])


def _kb():
    """每次请求获取管理器（避免持有过期的 root 快照）"""
    return get_project_kb_manager()


# ===== 请求模型 =====

class CreateVolumeRequest(BaseModel):
    number: int | None = None
    title: str = ""


class UpdateVolumeRequest(BaseModel):
    number: int | None = None
    title: str | None = None


class CreateChapterRequest(BaseModel):
    vol_id: str
    number: int | None = None
    title: str = ""
    numbering_mode: str = "continue"  # continue | per_volume


class UpdateChapterRequest(BaseModel):
    number: int | None = None
    title: str | None = None


class SaveChapterRequest(BaseModel):
    content: str
    title: str | None = None


# ===== 索引 =====

@router.get("/{project_id}")
async def get_writing_index(project_id: str):
    """获取写作索引（全部卷+章元数据）"""
    data = _kb().get_writing_index(project_id)
    if data is None:
        raise HTTPException(404, "项目不存在")
    return data


# ===== 卷 =====

@router.post("/{project_id}/volumes")
async def create_volume(project_id: str, req: CreateVolumeRequest):
    """创建新卷"""
    vol = _kb().create_volume(project_id, req.number, req.title)
    if not vol:
        raise HTTPException(404, "项目不存在")
    return vol


@router.put("/{project_id}/volumes/{vol_id}")
async def update_volume(project_id: str, vol_id: str, req: UpdateVolumeRequest):
    """更新卷信息"""
    vol = _kb().update_volume(project_id, vol_id, req.number, req.title)
    if not vol:
        raise HTTPException(404, "卷不存在")
    return vol


@router.delete("/{project_id}/volumes/{vol_id}")
async def delete_volume(project_id: str, vol_id: str):
    """删除卷"""
    ok = _kb().delete_volume(project_id, vol_id)
    if not ok:
        raise HTTPException(404, "卷不存在")
    return {"success": True}


# ===== 章节 =====

@router.post("/{project_id}/chapters")
async def create_chapter(project_id: str, req: CreateChapterRequest):
    """创建新章节"""
    ch = _kb().create_chapter(project_id, req.vol_id, req.number, req.title, req.numbering_mode)
    if not ch:
        raise HTTPException(404, "卷不存在")
    return ch


@router.get("/{project_id}/volumes/{vol_id}/chapters/{ch_id}")
async def get_chapter(project_id: str, vol_id: str, ch_id: str):
    """获取章节内容"""
    content = _kb().get_chapter_content(project_id, vol_id, ch_id)
    if content is None:
        raise HTTPException(404, "章节不存在")
    return {"content": content}


@router.put("/{project_id}/volumes/{vol_id}/chapters/{ch_id}")
async def save_chapter(project_id: str, vol_id: str, ch_id: str, req: SaveChapterRequest):
    """保存章节内容

    流程门禁：已定稿章节不可直接改写（需先重置）；修改已审稿/已修稿的正文
    会使其回到"草稿"阶段，提示需要重新审稿。
    """
    kb = _kb()
    flow = get_flow_manager()
    key = f"{vol_id}/{ch_id}"
    entry = flow.get_chapter(project_id, key)
    stage = (entry or {}).get("stage")

    if stage == "finalized":
        raise HTTPException(409, "该章已定稿，如需修改请先在创作流程中重置为草稿")

    result = kb.save_chapter_content(project_id, vol_id, ch_id, req.content, req.title)
    if not result:
        raise HTTPException(404, "章节不存在")

    if stage is None or stage == "planned":
        flow.set_stage(project_id, key, "drafted", note="保存正文")
    elif stage in ("reviewed", "revised"):
        # 正文已改动，原审稿结论失效，需要重新审稿
        flow.set_stage(project_id, key, "drafted", note="正文修改，审稿结论已失效", force=True)

    return {"success": True, "chapter": result}


@router.put("/{project_id}/volumes/{vol_id}/chapters/{ch_id}/meta")
async def update_chapter(project_id: str, vol_id: str, ch_id: str, req: UpdateChapterRequest):
    """更新章节元数据（编号/标题）"""
    result = _kb().update_chapter(project_id, vol_id, ch_id, req.number, req.title)
    if not result:
        raise HTTPException(404, "章节不存在")
    return {"success": True, "chapter": result}


@router.delete("/{project_id}/volumes/{vol_id}/chapters/{ch_id}")
async def delete_chapter(project_id: str, vol_id: str, ch_id: str):
    """删除章节"""
    ok = _kb().delete_chapter(project_id, vol_id, ch_id)
    if not ok:
        raise HTTPException(404, "章节不存在")
    return {"success": True}
