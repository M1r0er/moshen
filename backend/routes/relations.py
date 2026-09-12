"""
墨参 · 关系图谱路由
星图功能：分析小说文本提取实体/关系/时间线，提供 3D 关系图数据。
"""
import asyncio
import hashlib
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.safe_io import atomic_write_json
from knowledge.project_kb import get_project_kb_manager
from analysis.relation_graph_analyzer import RelationGraphAnalyzer, TYPES
from analysis.chapter_split import split_by_chapters

router = APIRouter(prefix="/api/relations", tags=["relations"])

# 后台任务状态：project_id -> {"task": asyncio.Task, "messages": deque, "listeners": set}
_tasks: dict[str, dict] = {}


class AnalyzeRequest(BaseModel):
    preset: str = "standard"  # fast | standard | deep
    full: bool = False  # 是否重新分析（清空已有结果）
    source: str = "chapters"  # chapters | text
    text: str = ""  # source=text 时的文本内容


class AnalyzeTextRequest(BaseModel):
    text: str
    preset: str = "standard"


def _get_project_dir(project_id: str) -> Path:
    d = get_project_kb_manager().get_project_dir(project_id)
    if not d:
        raise HTTPException(404, "项目不存在")
    return d


def _get_xingtu_dir(project_id: str) -> Path:
    return _get_project_dir(project_id) / "星图"


def _load_state(project_dir: Path) -> dict:
    state_file = project_dir / "星图" / "state.json"
    if state_file.exists():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"read_files": {}}


def _save_state(project_dir: Path, state: dict) -> None:
    state_file = project_dir / "星图" / "state.json"
    atomic_write_json(state_file, state)


def _read_master_data(type_dir: Path) -> dict | None:
    json_file = type_dir / "关系脉络.json"
    if json_file.exists():
        try:
            return json.loads(json_file.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


async def _run_analysis(project_id: str, req: AnalyzeRequest):
    """后台执行关系图谱分析"""
    project_dir = _get_project_dir(project_id)
    state = _load_state(project_dir)

    # 收集待分析文本
    if req.source == "text" and req.text.strip():
        text = req.text
        chapters = []
        parts = split_by_chapters(text)
        if parts and len(parts) >= 2:
            for i, p in enumerate(parts):
                name = p["heading"] or f"第{i + 1}部分"
                chapters.append({"name": name, "content": p["text"]})
        else:
            chapters.append({"name": "全文", "content": text})
    else:
        # 从写作模块获取章节
        all_chapters = get_project_kb_manager().get_all_chapters_text(project_id)
        chapters = [
            {
                "name": f"第{c.get('ch_number', '')}章 {c.get('ch_title', '')}",
                "content": c.get("content", ""),
            }
            for c in all_chapters
            if c.get("content", "").strip()
        ]

    if not chapters:
        _push_message(project_id, "没有可分析的文本内容")
        return

    # 全量重新分析：清空已有结果
    if req.full:
        import shutil
        relation_dir = project_dir / "星图" / "关系"
        if relation_dir.exists():
            shutil.rmtree(relation_dir)
        state["read_files"] = {}
        _push_message(project_id, "重新分析模式：已清空已有结果")

    _push_message(project_id, f"开始分析，共 {len(chapters)} 个章节块")

    # 逐类型分析
    for type_ in TYPES:
        analyzer = RelationGraphAnalyzer(type_=type_)
        type_dir = project_dir / "星图" / "关系" / type_
        previous = _read_master_data(type_dir)

        _push_message(project_id, f"正在分析「{type_}」…")
        try:
            data = await analyzer.analyze_batched(
                chapters,
                project_id=project_id,
                preset=req.preset,
                previous=previous,
                ctx={"project_id": project_id, "previous_data": previous},
                progress_cb=lambda msg: _push_message(project_id, msg),
            )
            await analyzer.save(data, project_dir, {"previous_data": previous})
            _push_message(
                project_id,
                f"「{type_}」完成：{len(data.get('entities', []))} 个条目，{len(data.get('relations', []))} 条关系",
            )
        except Exception as e:
            _push_message(project_id, f"「{type_}」分析失败：{str(e)}")

    _save_state(project_dir, state)
    _push_message(project_id, "分析完成")


def _push_message(project_id: str, msg: str):
    """向该项目的所有 SSE 监听器推送消息"""
    task_info = _tasks.get(project_id)
    if not task_info:
        return
    task_info["messages"].append(msg)
    if len(task_info["messages"]) > 300:
        task_info["messages"].popleft()
    for listener in list(task_info["listeners"]):
        try:
            listener(msg)
        except Exception:
            pass


@router.post("/{project_id}/analyze")
async def analyze(project_id: str, req: AnalyzeRequest):
    """触发关系图谱分析"""
    if not get_project_kb_manager().get_project(project_id):
        raise HTTPException(404, "项目不存在")

    # 如果已有任务在运行，返回冲突
    if project_id in _tasks and not _tasks[project_id]["task"].done():
        raise HTTPException(409, "分析进行中")

    from collections import deque

    _tasks[project_id] = {
        "task": None,
        "messages": deque(maxlen=300),
        "listeners": set(),
    }
    task = asyncio.create_task(_run_analysis(project_id, req))
    _tasks[project_id]["task"] = task
    return {"started": True}


@router.post("/{project_id}/analyze-text")
async def analyze_text(project_id: str, req: AnalyzeTextRequest):
    """直接粘贴文本分析"""
    if not req.text.strip():
        raise HTTPException(400, "文本内容不能为空")
    return await analyze(
        project_id,
        AnalyzeRequest(preset=req.preset, source="text", text=req.text),
    )


@router.get("/{project_id}/progress")
async def progress(project_id: str):
    """SSE 实时进度流"""
    if not get_project_kb_manager().get_project(project_id):
        raise HTTPException(404, "项目不存在")

    if project_id not in _tasks:
        from collections import deque

        _tasks[project_id] = {
            "task": None,
            "messages": deque(maxlen=300),
            "listeners": set(),
        }

    task_info = _tasks[project_id]

    async def event_generator():
        # 先回放历史消息
        for msg in list(task_info["messages"]):
            yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"

        queue: asyncio.Queue = asyncio.Queue()

        def listener(msg):
            try:
                queue.put_nowait(msg)
            except Exception:
                pass

        task_info["listeners"].add(listener)
        try:
            while True:
                msg = await queue.get()
                yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            task_info["listeners"].discard(listener)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{project_id}/overview")
async def overview(project_id: str):
    """汇总：6 分类各多少条目"""
    project_dir = _get_xingtu_dir(project_id)
    types = []
    for type_ in TYPES:
        data = _read_master_data(project_dir / "关系" / type_)
        types.append({
            "type": type_,
            "entity_count": len(data.get("entities", [])) if data else 0,
            "relation_count": len(data.get("relations", [])) if data else 0,
        })
    return {"types": types}


@router.get("/{project_id}/data")
async def get_data(project_id: str, type: str = "角色"):
    """读取某分类的关系脉络 JSON"""
    if type not in TYPES:
        raise HTTPException(400, f"未知类型，支持：{', '.join(TYPES)}")
    data = _read_master_data(_get_xingtu_dir(project_id) / "关系" / type)
    return {"empty": data is None, "data": data}


@router.get("/{project_id}/entity/{type}/{name}")
async def get_entity(project_id: str, type: str, name: str):
    """读取条目档案 Markdown"""
    if type not in TYPES:
        raise HTTPException(400, f"未知类型，支持：{', '.join(TYPES)}")
    safe = name.replace("/", "").replace("\\", "")
    # 跨分类查找
    for t in [type] + [t for t in TYPES if t != type]:
        file = _get_xingtu_dir(project_id) / "关系" / t / f"{safe}.md"
        if file.exists():
            return {"content": file.read_text(encoding="utf-8"), "type": t}
    raise HTTPException(404, "条目不存在")


# ===== 肖像绘制 =====

from analysis.portrait import (
    generate_portraits, redraw_portrait, get_portrait_path, find_portrait,
)
from fastapi.responses import FileResponse


@router.post("/{project_id}/portraits/generate")
async def generate_all_portraits(project_id: str):
    """为所有角色条目生成肖像（幂等：已有则跳过）"""
    project_dir = get_project_kb_manager().get_project_dir(project_id)
    if project_dir is None:
        raise HTTPException(404, "项目不存在")

    async def _run():
        import io
        results = await generate_portraits(project_dir, progress_cb=lambda msg: None)
        _tasks.setdefault(project_id, {})["portrait_result"] = results
        _tasks[project_id]["portrait_done"] = True

    _tasks.setdefault(project_id, {})
    _tasks[project_id]["portrait_done"] = False
    _tasks[project_id]["portrait_result"] = None
    _tasks[project_id]["portrait_task"] = asyncio.create_task(_run())
    return {"started": True}


@router.get("/{project_id}/portraits/status")
async def portrait_status(project_id: str):
    """查询肖像生成状态"""
    task_info = _tasks.get(project_id, {})
    return {
        "done": task_info.get("portrait_done", False),
        "result": task_info.get("portrait_result"),
    }


@router.get("/{project_id}/portraits/{name}")
async def get_portrait(project_id: str, name: str):
    """获取角色肖像图片"""
    project_dir = get_project_kb_manager().get_project_dir(project_id)
    if project_dir is None:
        raise HTTPException(404, "项目不存在")
    safe = name.replace("/", "").replace("\\", "")
    path = get_portrait_path(project_dir, safe)
    if path is None:
        raise HTTPException(404, "肖像不存在")
    return FileResponse(str(path))


@router.get("/{project_id}/portraits/{name}/info")
async def portrait_info(project_id: str, name: str):
    """查询肖像是否存在"""
    project_dir = get_project_kb_manager().get_project_dir(project_id)
    if project_dir is None:
        raise HTTPException(404, "项目不存在")
    safe = name.replace("/", "").replace("\\", "")
    ext = find_portrait(project_dir, safe)
    return {"exists": ext is not None, "ext": ext}


@router.post("/{project_id}/portraits/{name}/redraw")
async def redraw_one_portrait(project_id: str, name: str):
    """重绘单个角色肖像（强制覆盖）"""
    project_dir = get_project_kb_manager().get_project_dir(project_id)
    if project_dir is None:
        raise HTTPException(404, "项目不存在")
    safe = name.replace("/", "").replace("\\", "")
    try:
        ext = await redraw_portrait(project_dir, safe)
        return {"success": True, "ext": ext}
    except Exception as e:
        raise HTTPException(500, str(e)[:200])

