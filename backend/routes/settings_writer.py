"""
墨参 · 设定优化路由
用户输入自己的想法/设定草稿，AI 帮助完善和优化，使其更言简意赅。用户可以修改结果。
设定文件存放在 {workspace}/projects/{project_id}/settings/ 目录下。
"""
import re
import time
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.llm_provider import get_llm_provider
from routes.workspace import get_workspace_path, read_text_safe, write_text_safe

router = APIRouter(prefix="/api/settings", tags=["settings"])


# ===== 工具函数 =====

def get_settings_dir(project_id: str) -> Path:
    """获取项目设定目录"""
    ws = get_workspace_path()
    return ws / "projects" / project_id / "settings"


def sanitize_name(name: str) -> str:
    """校验文件名/项目ID，防止路径遍历"""
    if not name or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "无效的名称")
    return name


def ensure_settings_dir(settings_dir: Path) -> Path:
    """确保设定目录存在"""
    settings_dir.mkdir(parents=True, exist_ok=True)
    return settings_dir


# ===== 请求模型 =====

class OptimizeRequest(BaseModel):
    project_id: str
    content: str
    category: str = "other"  # world / character / plot / system / other


class SaveSettingRequest(BaseModel):
    filename: str
    content: str


# ===== 路由 =====
# 注意: /optimize 必须定义在 /{project_id} 之前，避免被路径参数匹配

@router.post("/optimize")
async def optimize_setting(req: OptimizeRequest):
    """AI 优化设定文本，使其更言简意赅、逻辑清晰、表达准确"""
    if not req.content.strip():
        raise HTTPException(400, "设定内容不能为空")

    valid_categories = {"world", "character", "plot", "system", "other"}
    category = req.category if req.category in valid_categories else "other"

    llm = get_llm_provider()

    # 根据类别定制优化策略
    category_prompts = {
        "world": "这是世界观设定。请确保：地理/历史/种族/势力等要素层次分明，设定术语统一，避免冗余描述。突出独特性，让读者快速建立世界认知。",
        "character": "这是人物设定。请确保：性格特征鲜明，外貌描写精炼有力，背景动机逻辑自洽。避免面面俱到，突出核心辨识度。",
        "plot": "这是剧情大纲。请确保：因果链清晰，冲突递进合理，伏笔埋设与回收有迹可循。用简洁的语言勾勒主线骨架。",
        "system": "这是力量体系设定。请确保：等级划分明确，规则边界清晰，成长路径合理。避免数值堆砌，突出体系特色。",
        "other": "请确保设定文本结构清晰、表达精准。",
    }
    category_hint = category_prompts.get(category, category_prompts["other"])

    prompt = (
        f"你是墨参，一位资深网文设定编辑。请优化以下设定文本。\n\n"
        f"## 优化要求\n"
        f"1. 言简意赅：删除冗余修饰，保留核心信息\n"
        f"2. 逻辑清晰：梳理因果关系，确保设定自洽\n"
        f"3. 表达精准：用准确的设定术语替代口语化描述\n"
        f"4. 保持原意：不改变用户的核心创意和设定方向\n\n"
        f"## 类别指导\n{category_hint}\n\n"
        f"## 用户原文\n{req.content}\n\n"
        f"请直接输出优化后的设定文本，不要加任何解释或前言。"
    )
    try:
        result = await llm.generate(
            [{"role": "user", "content": prompt}],
            role="TEXT_MASTER",
            max_tokens=4096,
        )
    except Exception as e:
        raise HTTPException(500, f"LLM 优化失败: {e}")

    return {
        "optimized": result,
        "category": category,
        "original_length": len(req.content),
        "optimized_length": len(result),
    }


@router.get("/{project_id}")
async def list_settings(project_id: str):
    """获取项目的所有设定（列出 settings/ 目录下的文件）"""
    project_id = sanitize_name(project_id)
    settings_dir = get_settings_dir(project_id)
    if not settings_dir.exists():
        return {"files": []}

    files = []
    for f in settings_dir.iterdir():
        if f.is_file() and not f.name.startswith("."):
            stat = f.stat()
            files.append({
                "filename": f.name,
                "size": stat.st_size,
                "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
            })
    files.sort(key=lambda x: x["modified"], reverse=True)
    return {"files": files}


@router.get("/{project_id}/{filename}")
async def get_setting(project_id: str, filename: str):
    """获取单个设定内容"""
    project_id = sanitize_name(project_id)
    filename = sanitize_name(filename)
    settings_dir = get_settings_dir(project_id)
    filepath = settings_dir / filename

    if not filepath.exists():
        raise HTTPException(404, "设定文件不存在")

    content = read_text_safe(filepath)
    return {"filename": filename, "content": content}


@router.post("/{project_id}")
async def save_setting(project_id: str, req: SaveSettingRequest):
    """保存设定（body: filename 和 content）"""
    project_id = sanitize_name(project_id)
    filename = sanitize_name(req.filename)
    if not filename:
        raise HTTPException(400, "文件名不能为空")

    settings_dir = get_settings_dir(project_id)
    ensure_settings_dir(settings_dir)

    filepath = settings_dir / filename
    write_text_safe(filepath, req.content)

    return {"success": True, "filename": filename, "path": str(filepath)}


@router.delete("/{project_id}/{filename}")
async def delete_setting(project_id: str, filename: str):
    """删除设定"""
    project_id = sanitize_name(project_id)
    filename = sanitize_name(filename)
    settings_dir = get_settings_dir(project_id)
    filepath = settings_dir / filename

    if not filepath.exists():
        raise HTTPException(404, "设定文件不存在")

    filepath.unlink()
    return {"success": True, "filename": filename}
