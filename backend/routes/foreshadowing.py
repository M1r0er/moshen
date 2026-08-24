"""
墨参 · 伏笔（钩子）管理路由
CRUD + AI 检测 + 回收检测
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from knowledge.project_kb import get_project_kb_manager
from core.llm_provider import get_llm_provider
import json
import re

router = APIRouter(prefix="/api/foreshadowing", tags=["foreshadowing"])
kb = get_project_kb_manager()
llm = get_llm_provider()


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

def _build_detect_prompt(chapters: list[dict], existing_names: list[str],
                         scope: str = "single") -> list[dict]:
    """构建伏笔检测的提示词"""
    chapter_texts = []
    for ch in chapters:
        vol_info = f"{ch.get('vol_title', '')} " if ch.get('vol_title') else ""
        ch_info = f"第{ch.get('ch_number', '')}章 {ch.get('ch_title', '')}"
        chapter_texts.append(f"【{vol_info}{ch_info}】\n{ch.get('content', '')[:8000]}")

    text = "\n\n".join(chapter_texts)
    existing = "、".join(existing_names) if existing_names else "（暂无）"

    scope_desc = "本章末尾" if scope == "single" else "全部章节"
    focus = "请特别注意章节末尾部分，很多伏笔会在章尾留下。" if scope == "single" else "请通读全部内容，全面梳理。"

    system = "你是专业的小说编辑，擅长识别和梳理故事中的伏笔（钩子）。请分析给定的小说内容，找出其中埋下的伏笔。"

    user = f"""以下是小说内容：

{text}

已知的伏笔列表：{existing}

任务：从上述内容中识别伏笔（钩子）。
{focus}

要求：
1. 找出可能是伏笔的情节、物品、人物设定、预言、谜团等
2. 每条伏笔包含：name（简短伏笔名）、content（具体内容，引用原文关键句或概括）、chapter（所在章节标题）、is_new（是否为新伏笔，true/false）
3. 如果内容中出现了对已有伏笔的呼应/回收，也请标注出来，is_new=false，并说明在哪个章节回收
4. 只返回 JSON 数组，不要有其他文字，格式：
[{{"name": "...", "content": "...", "chapter": "...", "is_new": true/false, "is_resolution": true/false}}]
"""

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


@router.post("/{project_id}/detect")
async def detect_foreshadowing(project_id: str, req: DetectRequest):
    """AI 检测伏笔

    scope=single: 单章检测（侧重章尾）
    scope=full: 全文检测
    """
    all_chapters = kb.get_all_chapters_text(project_id)
    if not all_chapters:
        return {"found": [], "message": "还没有章节内容"}

    if req.scope == "single":
        # 单章模式：找到指定章节
        target = [c for c in all_chapters if c["ch_id"] == req.ch_id]
        if not target:
            raise HTTPException(404, "章节不存在")
        chapters_to_check = target
    else:
        # 全文模式
        chapters_to_check = all_chapters

    # 已有伏笔名
    existing = [f["name"] for f in kb.list_foreshadowings(project_id)]

    messages = _build_detect_prompt(chapters_to_check, existing, req.scope)

    try:
        result = await llm.generate(
            messages, role="STRUCTURE_ANALYST",
            temperature=0.3, max_tokens=4096,
        )
        # 解析 JSON
        json_str = result.strip()
        # 去掉可能的 markdown 代码块标记
        if json_str.startswith("```"):
            json_str = re.sub(r"^```(?:json)?\s*", "", json_str)
            json_str = re.sub(r"\s*```$", "", json_str)
        found = json.loads(json_str)
        return {"found": found}
    except Exception as e:
        raise HTTPException(500, f"检测失败: {str(e)}")


@router.post("/{project_id}/recover-check")
async def recovery_check(project_id: str, req: DetectRequest):
    """回收检测：扫描章节检查已有伏笔是否被回收"""
    existing = kb.list_foreshadowings(project_id)
    if not existing:
        return {"results": [], "message": "还没有伏笔记录"}

    all_chapters = kb.get_all_chapters_text(project_id)
    if not all_chapters:
        return {"results": [], "message": "还没有章节内容"}

    if req.scope == "single":
        target = [c for c in all_chapters if c["ch_id"] == req.ch_id]
        if not target:
            raise HTTPException(404, "章节不存在")
        chapters_to_check = target
    else:
        chapters_to_check = all_chapters

    # 构建提示词
    fs_list = "\n".join([f"- {f['name']}（出现{f['entry_count']}次，状态：{'未回收' if f['status']=='active' else '已回收'}）" for f in existing])
    ch_texts = "\n\n".join([
        f"【第{c.get('ch_number','')}章 {c.get('ch_title','')}】\n{c.get('content','')[:5000]}"
        for c in chapters_to_check
    ])

    messages = [
        {"role": "system", "content": "你是专业的小说编辑，负责检查伏笔是否已经被回收（呼应、揭秘、解决）。"},
        {"role": "user", "content": f"""以下是已有的伏笔列表：
{fs_list}

以下是小说章节内容：
{ch_texts}

请检查：在上述章节内容中，哪些伏笔已经被回收/呼应/揭秘了？

要求：
1. 只返回已确认回收的伏笔
2. 每条包含：name（伏笔名）、content（回收的具体内容）、chapter（回收所在章节标题）
3. 只返回 JSON 数组，不要有其他文字，格式：
[{{"name": "...", "content": "...", "chapter": "..."}}]
"""}
    ]

    try:
        result = await llm.generate(
            messages, role="STRUCTURE_ANALYST",
            temperature=0.2, max_tokens=2048,
        )
        json_str = result.strip()
        if json_str.startswith("```"):
            json_str = re.sub(r"^```(?:json)?\s*", "", json_str)
            json_str = re.sub(r"\s*```$", "", json_str)
        recovered = json.loads(json_str)
        return {"results": recovered}
    except Exception as e:
        raise HTTPException(500, f"检测失败: {str(e)}")
