"""
墨参 · 伏笔（钩子）管理路由
CRUD + AI 检测 + 回收检测
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from knowledge.project_kb import get_project_kb_manager
from analysis.foreshadowing_analyzer import ForeshadowingAnalyzer

router = APIRouter(prefix="/api/foreshadowing", tags=["foreshadowing"])
kb = get_project_kb_manager()


# ===== 请求模型 =====

class CreateForeshadowingRequest(BaseModel):
    name: str
    content: str = ""
    chapter_id: str = ""
    chapter_title: str = ""


class UpdateForeshadowingRequest(BaseModel):
    name: str | None = None
    status: str | None = None  # active | resolved
    resolution_chapter: str | None = None


class AddEntryRequest(BaseModel):
    content: str
    chapter_id: str = ""
    chapter_title: str = ""


class DetectRequest(BaseModel):
    vol_id: str = ""
    ch_id: str = ""
    scope: str = "single"  # single | full
    task_id: str = ""      # 通用任务进度通道（前端订阅用，可空）


# ===== CRUD =====

@router.get("/{project_id}")
async def list_foreshadowings(project_id: str):
    """列出所有伏笔（摘要列表）"""
    items = kb.list_foreshadowings(project_id)
    return {"foreshadowings": items}


@router.get("/{project_id}/{f_id}")
async def get_foreshadowing(project_id: str, f_id: str):
    """获取单个伏笔详情"""
    fs = kb.get_foreshadowing(project_id, f_id)
    if not fs:
        raise HTTPException(404, "伏笔不存在")
    return fs


@router.post("/{project_id}")
async def create_foreshadowing(project_id: str, req: CreateForeshadowingRequest):
    """创建新伏笔"""
    if not req.name.strip():
        raise HTTPException(400, "伏笔名不能为空")
    fs = kb.create_foreshadowing(project_id, req.name.strip(), req.content, req.chapter_id, req.chapter_title)
    if not fs:
        raise HTTPException(404, "项目不存在")
    return fs


@router.put("/{project_id}/{f_id}")
async def update_foreshadowing(project_id: str, f_id: str, req: UpdateForeshadowingRequest):
    """更新伏笔信息"""
    fs = kb.update_foreshadowing(project_id, f_id, req.name, req.status, req.resolution_chapter)
    if not fs:
        raise HTTPException(404, "伏笔不存在")
    return fs


@router.delete("/{project_id}/{f_id}")
async def delete_foreshadowing(project_id: str, f_id: str):
    """删除伏笔"""
    ok = kb.delete_foreshadowing(project_id, f_id)
    if not ok:
        raise HTTPException(404, "伏笔不存在")
    return {"success": True}


@router.post("/{project_id}/{f_id}/entries")
async def add_entry(project_id: str, f_id: str, req: AddEntryRequest):
    """添加伏笔出现记录"""
    entry = kb.add_entry(project_id, f_id, req.content, req.chapter_id, req.chapter_title)
    if not entry:
        raise HTTPException(404, "伏笔不存在")
    return entry


@router.delete("/{project_id}/{f_id}/entries/{entry_id}")
async def remove_entry(project_id: str, f_id: str, entry_id: str):
    """删除伏笔出现记录"""
    ok = kb.remove_entry(project_id, f_id, entry_id)
    if not ok:
        raise HTTPException(404, "记录不存在")
    return {"success": True}


# ===== AI 检测 =====

def _select_chapters(all_chapters: list[dict], req: DetectRequest) -> list[dict]:
    """根据 scope 选择章节（单章/全文）—— detect 与 recover-check 共用"""
    if req.scope == "single":
        target = [c for c in all_chapters if c["ch_id"] == req.ch_id]
        if not target:
            raise HTTPException(404, "章节不存在")
        return target
    return all_chapters


def _chapters_to_text(chapters: list[dict], max_len: int = 8000) -> str:
    """将章节列表拼接为分析文本"""
    texts = []
    for ch in chapters:
        vol_info = f"{ch.get('vol_title', '')} " if ch.get('vol_title') else ""
        ch_info = f"第{ch.get('ch_number', '')}章 {ch.get('ch_title', '')}"
        content = ch.get('content', '')[:max_len]
        texts.append(f"【{vol_info}{ch_info}】\n{content}")
    return "\n\n".join(texts)


@router.post("/{project_id}/detect")
async def detect_foreshadowing(project_id: str, req: DetectRequest):
    """AI 检测伏笔（scope=single 单章 / full 全文）"""
    from analysis.tasks import SingleStep

    step = SingleStep(req.task_id, "foreshadowing.detect", "伏笔检测")
    step.begin("正在检测伏笔…")

    all_chapters = kb.get_all_chapters_text(project_id)
    if not all_chapters:
        step.ok("还没有章节内容")
        return {"found": [], "message": "还没有章节内容"}

    chapters_to_check = _select_chapters(all_chapters, req)
    existing = [f["name"] for f in kb.list_foreshadowings(project_id)]

    analyzer = ForeshadowingAnalyzer(mode="detect")
    analyzer.set_existing(existing)
    text = _chapters_to_text(chapters_to_check)

    try:
        found = await analyzer.analyze(
            text, project_id, ctx={"scope": req.scope}
        )
    except Exception as e:
        step.fail(str(e))
        raise HTTPException(500, f"检测失败: {str(e)}")

    step.ok(f"检测到 {len(found)} 处伏笔")
    return {"found": found}


@router.post("/{project_id}/recover-check")
async def recovery_check(project_id: str, req: DetectRequest):
    """回收检测：扫描章节检查已有伏笔是否被回收"""
    from analysis.tasks import SingleStep

    step = SingleStep(req.task_id, "foreshadowing.recover", "回收检测")
    step.begin("正在检查已有伏笔是否被回收…")

    existing = kb.list_foreshadowings(project_id)
    if not existing:
        step.ok("还没有伏笔记录")
        return {"results": [], "message": "还没有伏笔记录"}

    all_chapters = kb.get_all_chapters_text(project_id)
    if not all_chapters:
        step.ok("还没有章节内容")
        return {"results": [], "message": "还没有章节内容"}

    chapters_to_check = _select_chapters(all_chapters, req)

    analyzer = ForeshadowingAnalyzer(mode="recover_check")
    text = _chapters_to_text(chapters_to_check, max_len=5000)

    try:
        recovered = await analyzer.analyze(
            text, project_id, ctx={"existing_foreshadowings": existing}
        )
    except Exception as e:
        step.fail(str(e))
        raise HTTPException(500, f"回收检测失败: {str(e)}")

    step.ok(f"检查了 {len(existing)} 条伏笔")
    return {"results": recovered}
