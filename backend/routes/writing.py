"""
墨参 · 写作页路由
卷/章管理、正文保存、伏笔检测入口
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from knowledge.project_kb import get_project_kb_manager

router = APIRouter(prefix="/api/writing", tags=["writing"])
kb = get_project_kb_manager()


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
    data = kb.get_writing_index(project_id)
    if data is None:
        raise HTTPException(404, "项目不存在")
    return data


# ===== 卷 =====

@router.post("/{project_id}/volumes")
async def create_volume(project_id: str, req: CreateVolumeRequest):
    """创建新卷"""
    vol = kb.create_volume(project_id, req.number, req.title)
    if not vol:
        raise HTTPException(404, "项目不存在")
    return vol


@router.put("/{project_id}/volumes/{vol_id}")
async def update_volume(project_id: str, vol_id: str, req: UpdateVolumeRequest):
    """更新卷信息"""
    vol = kb.update_volume(project_id, vol_id, req.number, req.title)
    if not vol:
        raise HTTPException(404, "卷不存在")
    return vol


@router.delete("/{project_id}/volumes/{vol_id}")
async def delete_volume(project_id: str, vol_id: str):
    """删除卷"""
    ok = kb.delete_volume(project_id, vol_id)
    if not ok:
        raise HTTPException(404, "卷不存在")
    return {"success": True}


# ===== 章节 =====

@router.post("/{project_id}/chapters")
async def create_chapter(project_id: str, req: CreateChapterRequest):
    """创建新章节"""
    ch = kb.create_chapter(project_id, req.vol_id, req.number, req.title, req.numbering_mode)
    if not ch:
        raise HTTPException(404, "卷不存在")
    return ch


@router.get("/{project_id}/volumes/{vol_id}/chapters/{ch_id}")
async def get_chapter(project_id: str, vol_id: str, ch_id: str):
    """获取章节内容"""
    content = kb.get_chapter_content(project_id, vol_id, ch_id)
    if content is None:
        raise HTTPException(404, "章节不存在")
    return {"content": content}


@router.put("/{project_id}/volumes/{vol_id}/chapters/{ch_id}")
async def save_chapter(project_id: str, vol_id: str, ch_id: str, req: SaveChapterRequest):
    """保存章节内容"""
    result = kb.save_chapter_content(project_id, vol_id, ch_id, req.content, req.title)
    if not result:
        raise HTTPException(404, "章节不存在")
    return {"success": True, "chapter": result}


@router.put("/{project_id}/volumes/{vol_id}/chapters/{ch_id}/meta")
async def update_chapter(project_id: str, vol_id: str, ch_id: str, req: UpdateChapterRequest):
    """更新章节元数据（编号/标题）"""
    result = kb.update_chapter(project_id, vol_id, ch_id, req.number, req.title)
    if not result:
        raise HTTPException(404, "章节不存在")
    return {"success": True, "chapter": result}


@router.delete("/{project_id}/volumes/{vol_id}/chapters/{ch_id}")
async def delete_chapter(project_id: str, vol_id: str, ch_id: str):
    """删除章节"""
    ok = kb.delete_chapter(project_id, vol_id, ch_id)
    if not ok:
        raise HTTPException(404, "章节不存在")
    return {"success": True}
