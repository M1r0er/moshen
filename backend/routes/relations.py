"""
墨参 · 关系图谱路由
星图功能：分析小说文本提取实体/关系/时间线，提供 3D 关系图数据。
"""
import asyncio
import hashlib
import json
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from core.safe_io import atomic_write_json, update_json
from core.llm_provider import get_llm_provider
from knowledge.project_kb import get_project_kb_manager
from knowledge.relation_graph_render import to_file_name
from analysis.relation_graph_analyzer import (
    TYPES,
    RelationGraphMultiAnalyzer,
    count_protected,
    empty_data,
    ensure_rids,
    multi_prompt_overhead,
    new_rid,
    persist_type_data,
    strip_manual_protection,
)
from analysis.chapter_split import split_by_chapters

router = APIRouter(prefix="/api/relations", tags=["relations"])

# 分析档位 → 单个批次的目标字符数
# 批次越大调用次数越少（更快、更省），批次越小抽取粒度越细（更全、更贵）
PRESET_BATCH_CHARS = {"fast": 20000, "standard": 12000, "deep": 6000}
# 批次下限与单次请求的上下文上限（字符），用于自动收缩批次避免超出模型上下文
MIN_BATCH_CHARS = 3000
MAX_PROMPT_CHARS = 100000

# 后台任务状态：project_id -> {"task": asyncio.Task, "messages": deque, "listeners": set}
_tasks: dict[str, dict] = {}


def _fmt_elapsed(seconds: float) -> str:
    """把秒数格式化为易读的时长"""
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}分{s}秒"
    h, m = divmod(m, 60)
    return f"{h}小时{m}分"


class AnalyzeRequest(BaseModel):
    preset: str = "standard"  # fast | standard | deep
    full: bool = False  # AI 结果是否从零重新生成（手动内容仍受保护）
    mode: str = "preserve"  # preserve | overwrite_manual
    source: str = "chapters"  # chapters | text | uploads
    text: str = ""  # source=text 时的文本内容
    files: list[str] = []  # source=uploads 时选中的文件名（空 = 全部）
    incremental: bool = True  # source=uploads 时跳过已分析且内容未变的文件


class AnalyzeTextRequest(BaseModel):
    text: str
    preset: str = "standard"
    mode: str = "preserve"


class EntityPayload(BaseModel):
    """手动新增/编辑条目的载荷"""
    id: str
    aliases: list[str] = []
    summary: str = ""
    profile: str = ""
    category: str = ""
    weight: int = 1
    gender: str = ""
    age: str = ""
    identity: str = ""
    appearance: str = ""
    personality: str = ""
    locked: bool = False


class EntityUpdatePayload(BaseModel):
    """编辑条目：仅传入需要改动的字段；new_id/target_type 支持改名与换分类"""
    new_id: str | None = None
    target_type: str | None = None
    aliases: list[str] | None = None
    summary: str | None = None
    profile: str | None = None
    category: str | None = None
    weight: int | None = None
    gender: str | None = None
    age: str | None = None
    identity: str | None = None
    appearance: str | None = None
    personality: str | None = None
    locked: bool | None = None


class RelationPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_id: str = Field(alias="from")
    to_id: str = Field(alias="to")
    type: str = "关联"
    detail: str = ""
    time: str = ""
    locked: bool = False


class RelationUpdatePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_id: str | None = Field(default=None, alias="from")
    to_id: str | None = Field(default=None, alias="to")
    type: str | None = None
    detail: str | None = None
    time: str | None = None
    locked: bool | None = None



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


def _load_type_data(project_id: str, type_: str) -> dict:
    """读取某分类的关系脉络（缺失时返回空结构）"""
    data = _read_master_data(_get_xingtu_dir(project_id) / "关系" / type_)
    if not isinstance(data, dict):
        data = {}
    data.setdefault("type", type_)
    data.setdefault("generatedAt", "")
    data.setdefault("entities", [])
    data.setdefault("relations", [])
    data.setdefault("timeline", [])
    return data


def _collect_upload_chapters(
    project_dir: Path,
    files: list[str],
    incremental: bool,
    read_files: dict,
) -> tuple[list[dict], dict[str, str], list[str]]:
    """收集 uploads 目录下已上传文档的文本，并按章节切分为分析块

    Args:
        project_dir: 项目根目录
        files: 选中的文件名（空表示全部分析）
        incremental: 是否跳过「已分析且内容未变化」的文件
        read_files: 星图 state.json 中记录的「文件名 -> 内容哈希」映射

    Returns:
        (章节块列表, 本次分析成功的文件哈希映射, 跳过说明列表)
    """
    upload_dir = project_dir / "uploads"
    if not upload_dir.exists():
        return [], {}, ["项目 uploads 目录不存在，请先在「文件」页上传文档"]

    wanted = {name for name in files if name}
    targets = [
        f for f in sorted(upload_dir.iterdir())
        if f.is_file() and not f.name.startswith(".") and (not wanted or f.name in wanted)
    ]
    if not targets:
        return [], {}, ["未找到匹配的上传文件"]

    kb = get_project_kb_manager()
    chapters: list[dict] = []
    analyzed: dict[str, str] = {}
    skipped: list[str] = []

    for f in targets:
        try:
            content = kb.read_file_summary(f) or ""
        except Exception as e:
            skipped.append(f"{f.name}：读取失败（{e}）")
            continue
        if not content.strip():
            skipped.append(f"{f.name}：内容为空")
            continue

        digest = hashlib.md5(content.encode("utf-8")).hexdigest()
        if incremental and read_files.get(f.name) == digest:
            skipped.append(f"{f.name}：已分析过且内容未变化")
            continue

        parts = split_by_chapters(content)
        if parts and len(parts) >= 2:
            for i, p in enumerate(parts):
                name = p["heading"] or f"第{i + 1}部分"
                chapters.append({"name": f"{f.name} · {name}", "content": p["text"]})
        else:
            chapters.append({"name": f.name, "content": content})
        analyzed[f.name] = digest

    return chapters, analyzed, skipped


async def _run_analysis(project_id: str, req: AnalyzeRequest):
    """后台执行关系图谱分析"""
    project_dir = _get_project_dir(project_id)
    state = _load_state(project_dir)
    read_files = state.setdefault("read_files", {})
    analyzed_files: dict[str, str] = {}
    skipped: list[str] = []

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
    elif req.source == "uploads":
        # 从「文件」页已上传的文档提取文本
        chapters, analyzed_files, skipped = _collect_upload_chapters(
            project_dir, req.files, req.incremental, read_files,
        )
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

    for note in skipped[:8]:
        _push_message(project_id, f"跳过：{note}")
    if len(skipped) > 8:
        _push_message(project_id, f"…另有 {len(skipped) - 8} 个文件被跳过")

    if not chapters:
        if req.source == "uploads":
            _push_message(project_id, "没有可分析的上传文件内容")
        else:
            _push_message(project_id, "没有可分析的文本内容")
        _push_message(project_id, "分析完成")
        return

    overwrite_manual = req.mode == "overwrite_manual"
    if overwrite_manual:
        _push_message(project_id, "⚠️ 覆盖模式：手动维护的条目与关系将不再受保护")
    if req.full:
        _push_message(project_id, "全量重析：AI 不参考现有数据，只做新增（现有内容不会被改动）")

    # 各分类现有数据作为补丁基底（同时统计手动内容用于播报）
    base_map: dict[str, dict] = {}
    total_protected = 0
    for type_ in TYPES:
        local = _read_master_data(project_dir / "星图" / "关系" / type_)
        if overwrite_manual and local:
            base_map[type_] = strip_manual_protection(local)
        else:
            base_map[type_] = local if local else empty_data(type_)
        ent_n, rel_n = count_protected(local)
        total_protected += ent_n + rel_n
        if ent_n or rel_n:
            _push_message(
                project_id,
                f"「{type_}」检测到 {ent_n} 条手动条目、{rel_n} 条手动关系，将予以保护",
            )

    batch_chars = PRESET_BATCH_CHARS.get(req.preset, PRESET_BATCH_CHARS["standard"])
    if req.source == "uploads" and analyzed_files:
        _push_message(
            project_id,
            f"已读取 {len(analyzed_files)} 个上传文件，共 {len(chapters)} 个文本块；"
            f"档位「{req.preset}」单批约 {batch_chars} 字",
        )
    else:
        _push_message(
            project_id,
            f"开始分析，共 {len(chapters)} 个章节块；档位「{req.preset}」单批约 {batch_chars} 字",
        )
    _push_message(project_id, "本轮为全类型单轮解析：每个批次一次调用，同时产出 6 个分类的变更")

    analyzer = RelationGraphMultiAnalyzer(protect=not overwrite_manual)
    llm = get_llm_provider()
    llm.reset_stats()
    started = time.perf_counter()
    failed = False
    data: dict[str, dict] = {}

    try:
        data = await analyzer.analyze_batched(
            chapters,
            project_id=project_id,
            preset=req.preset,
            previous=base_map,
            ctx={
                "project_id": project_id,
                "previous_data": base_map,
                "blind": bool(req.full),
            },
            progress_cb=lambda msg: _push_message(project_id, msg),
            batch_chars=batch_chars,
            min_batch_chars=MIN_BATCH_CHARS,
            max_prompt_chars=MAX_PROMPT_CHARS,
            overhead_chars=multi_prompt_overhead(),
        )
        await analyzer.save(data, project_dir, {"previous_data": base_map})

        if analyzer.condensed_used:
            _push_message(
                project_id,
                "现有数据较大，已改用精简形式（条目名/别名/摘要/身份 + 关系端点）回传给模型，"
                "本地完整档案不受影响",
            )

        for type_ in TYPES:
            report = analyzer.totals.get(type_) or {}
            tdata = data.get(type_) or {}
            _push_message(
                project_id,
                f"「{type_}」完成：新增 {report.get('added', 0)} 项、"
                f"更新 {report.get('updated', 0)} 项、删除 {report.get('removed', 0)} 项；"
                f"现共 {len(tdata.get('entities', []))} 个条目、"
                f"{len(tdata.get('relations', []))} 条关系、"
                f"{len(tdata.get('timeline', []))} 条时间线",
            )
            if report.get("protected_entities") or report.get("protected_fields") or report.get("protected_relations"):
                _push_message(
                    project_id,
                    f"「{type_}」保护了 {report.get('protected_entities', 0)} 个手动条目、"
                    f"{report.get('protected_fields', 0)} 个手动字段、"
                    f"{report.get('protected_relations', 0)} 条手动关系未被改动",
                )
    except Exception as e:
        failed = True
        _push_message(project_id, f"分析失败：{str(e)}")

    if total_protected:
        _push_message(project_id, f"已保护 {total_protected} 项手动内容（未被 AI 覆盖）")

    # 逐次调用的耗时与 token 消耗汇总（数据同时写入 ~/.moshen/logs/llm-usage.jsonl）
    stats = llm.stats
    elapsed = time.perf_counter() - started
    cost_line = (
        f"本次任务：{stats['calls']} 次模型调用，总耗时 {_fmt_elapsed(elapsed)}"
        f"（模型往返 {_fmt_elapsed(stats['elapsed'])}）；"
        f"输入 {stats['prompt_tokens']} tokens，输出 {stats['completion_tokens']} tokens"
    )
    if stats["usage_missing"]:
        cost_line += f"（有 {stats['usage_missing']} 次服务端未返回用量）"
    if stats["failed_calls"]:
        cost_line += f"；失败 {stats['failed_calls']} 次"
    _push_message(project_id, cost_line)

    # 上传文件的增量状态：仅在整轮分析成功时记录，失败则下次可重试
    if req.source == "uploads" and analyzed_files:
        if failed:
            _push_message(project_id, "分析失败，本次文件未记入已分析状态，可稍后重试")
        else:
            read_files.update(analyzed_files)
            _push_message(project_id, f"已记录 {len(analyzed_files)} 个文件的已分析状态")

    state["read_files"] = read_files
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
        AnalyzeRequest(preset=req.preset, source="text", text=req.text, mode=req.mode),
    )


@router.get("/{project_id}/uploads")
async def list_upload_files(project_id: str):
    """列出项目已上传文件及其星图分析状态（供星图选择分析对象）"""
    project_dir = _get_project_dir(project_id)
    read_files = _load_state(project_dir).get("read_files") or {}

    upload_dir = project_dir / "uploads"
    files = []
    if upload_dir.exists():
        kb = get_project_kb_manager()
        for f in sorted(upload_dir.iterdir()):
            if not f.is_file() or f.name.startswith("."):
                continue
            stat = f.stat()
            try:
                content = kb.read_file_summary(f) or ""
            except Exception:
                content = ""
            digest = hashlib.md5(content.encode("utf-8")).hexdigest()
            recorded = read_files.get(f.name)
            files.append({
                "filename": f.name,
                "ext": f.suffix.lower(),
                "size": stat.st_size,
                "char_count": len(content),
                "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                "analyzed": recorded == digest,
                "changed": bool(recorded) and recorded != digest,
            })
    return {"files": files}


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
        manual_e, manual_r = count_protected(data)
        types.append({
            "type": type_,
            "entity_count": len(data.get("entities", [])) if data else 0,
            "relation_count": len(data.get("relations", [])) if data else 0,
            "manual_entity_count": manual_e,
            "manual_relation_count": manual_r,
        })
    return {"types": types}


@router.get("/{project_id}/data")
async def get_data(project_id: str, type: str = "角色"):
    """读取某分类的关系脉络 JSON"""
    if type not in TYPES:
        raise HTTPException(400, f"未知类型，支持：{', '.join(TYPES)}")
    project_dir = _get_project_dir(project_id)
    data = _read_master_data(project_dir / "星图" / "关系" / type)
    if data is None:
        return {"empty": True, "data": None}
    # 兼容旧数据：为历史关系补上稳定 rid，便于后续编辑/删除定位
    if any(not r.get("rid") for r in data.get("relations", []) or []):
        ensure_rids(data)
        persist_type_data(project_dir, type, data)
    return {"empty": False, "data": data}


@router.get("/{project_id}/entity/{type}/{name}")
async def get_entity(project_id: str, type: str, name: str):
    """读取条目档案 Markdown + 结构化条目数据（供编辑表单回填）"""
    if type not in TYPES:
        raise HTTPException(400, f"未知类型，支持：{', '.join(TYPES)}")
    safe = to_file_name(name)
    # 跨分类查找
    for t in [type] + [t for t in TYPES if t != type]:
        dir_ = _get_xingtu_dir(project_id) / "关系" / t
        data = _read_master_data(dir_)
        entity = None
        if data:
            entity = next(
                (e for e in data.get("entities", []) if to_file_name(e.get("id", "")) == safe),
                None,
            )
        file = dir_ / f"{safe}.md"
        if entity is not None or file.exists():
            content = file.read_text(encoding="utf-8") if file.exists() else ""
            return {"content": content, "type": t, "entity": entity, "name": name}
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


@router.get("/{project_id}/portraits/names")
async def portrait_names(project_id: str):
    """列出已有肖像图的角色名（供关系图一次性判断哪些节点显示头像）

    注意：必须声明在 /portraits/{name} 之前，否则会被当成 name="names"。
    """
    project_dir = get_project_kb_manager().get_project_dir(project_id)
    if project_dir is None:
        raise HTTPException(404, "项目不存在")
    type_dir = project_dir / "星图" / "关系" / "角色"
    names: list[str] = []
    if type_dir.exists():
        exts = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
        for f in sorted(type_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in exts:
                names.append(f.stem)
    return {"names": names}


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


# ===== 条目 / 关系的手动维护（CRUD） =====


def _check_type(t: str) -> str:
    if t not in TYPES:
        raise HTTPException(400, f"未知分类，支持：{', '.join(TYPES)}")
    return t


def _empty_type(type_: str) -> dict:
    return {
        "type": type_,
        "generatedAt": "",
        "entities": [],
        "relations": [],
        "timeline": [],
    }


def _find_entity(data: dict, name: str) -> dict | None:
    """按条目名查找（比较前统一做文件名净化，与档案落盘规则一致）"""
    safe = to_file_name(name)
    return next(
        (
            e
            for e in data.get("entities", []) or []
            if to_file_name(e.get("id", "")) == safe
        ),
        None,
    )


def _rename_refs(data: dict, old_id: str, new_id: str) -> None:
    """级联改名：同步关系与时间线中的引用（原地修改）"""
    for r in data.get("relations", []) or []:
        if r.get("from") == old_id:
            r["from"] = new_id
        if r.get("to") == old_id:
            r["to"] = new_id
    for t in data.get("timeline", []) or []:
        t["refs"] = [new_id if x == old_id else x for x in (t.get("refs") or [])]


def _drop_entity_refs(data: dict, eid: str) -> int:
    """删除指向该条目的关系与时间线引用，返回被移除的关系数"""
    before = len(data.get("relations", []) or [])
    data["relations"] = [
        r
        for r in data.get("relations", []) or []
        if r.get("from") != eid and r.get("to") != eid
    ]
    for t in data.get("timeline", []) or []:
        t["refs"] = [x for x in (t.get("refs") or []) if x != eid]
    return before - len(data["relations"])


def _dedupe_relations(data: dict) -> None:
    """清理自环与重复关系（同一对条目只保留一条）"""
    out: list[dict] = []
    seen: set[tuple] = set()
    for r in data.get("relations", []) or []:
        if r.get("from") == r.get("to"):
            continue
        key = (r.get("from"), r.get("to"))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    data["relations"] = out


def _apply_entity_fields(entity: dict, payload: EntityUpdatePayload) -> None:
    """把编辑载荷写入条目，并记录手动编辑痕迹（这些字段 AI 不得覆盖）"""
    values = {
        "aliases": payload.aliases,
        "summary": payload.summary,
        "profile": payload.profile,
        "category": payload.category,
        "weight": payload.weight,
        "gender": payload.gender,
        "age": payload.age,
        "identity": payload.identity,
        "appearance": payload.appearance,
        "personality": payload.personality,
    }
    touched = set(entity.get("edited_fields") or [])
    for key, val in values.items():
        if val is None:
            continue
        if key == "aliases":
            entity[key] = [str(a).strip() for a in val if str(a).strip()]
        elif key == "weight":
            entity[key] = val if isinstance(val, int) else 1
        else:
            entity[key] = str(val)
        touched.add(key)
    if payload.locked is not None:
        entity["locked"] = bool(payload.locked)
    entity["edited_fields"] = sorted(touched)


def _delete_portrait(project_dir: Path, entity_id: str) -> None:
    path = get_portrait_path(project_dir, entity_id)
    if path and path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def _move_portrait(project_dir: Path, old_id: str, new_id: str) -> None:
    old = get_portrait_path(project_dir, old_id)
    if not old or not old.exists():
        return
    new = old.parent / f"{to_file_name(new_id)}{old.suffix}"
    if new == old:
        return
    try:
        old.replace(new)
    except OSError:
        pass


@router.post("/{project_id}/entity")
async def create_entity(project_id: str, payload: EntityPayload, type: str = "角色"):
    """手动新增条目（标记为手动内容，AI 分析时不会被清掉）"""
    project_dir = _get_project_dir(project_id)
    _check_type(type)
    eid = payload.id.strip()
    if not eid:
        raise HTTPException(400, "条目名称不能为空")

    path = _get_xingtu_dir(project_id) / "关系" / type / "关系脉络.json"

    def mutate(data: dict) -> dict:
        entities = data.setdefault("entities", [])
        if any(to_file_name(e.get("id", "")) == to_file_name(eid) for e in entities):
            raise HTTPException(409, f"条目「{eid}」已存在")
        entities.append({
            "id": eid,
            "aliases": [str(a).strip() for a in (payload.aliases or []) if str(a).strip()],
            "summary": payload.summary or "",
            "weight": payload.weight if isinstance(payload.weight, int) else 1,
            "category": payload.category or "",
            "gender": payload.gender or "",
            "age": payload.age or "",
            "identity": payload.identity or "",
            "appearance": payload.appearance or "",
            "personality": payload.personality or "",
            "profile": payload.profile or "",
            "source": "manual",
            "locked": bool(payload.locked),
            "edited_fields": [],
        })
        return data

    data = update_json(path, mutate, default=_empty_type(type))
    persist_type_data(project_dir, type, data)
    return {"success": True, "id": eid, "type": type}


@router.put("/{project_id}/entity/{type}/{name}")
async def update_entity(
    project_id: str, type: str, name: str, payload: EntityUpdatePayload
):
    """编辑条目：支持改名、改分类（跨分类迁移）、补充档案、锁定"""
    project_dir = _get_project_dir(project_id)
    _check_type(type)
    target_type = payload.target_type or type
    _check_type(target_type)

    xingtu = _get_xingtu_dir(project_id) / "关系"
    src_path = xingtu / type / "关系脉络.json"
    if not src_path.exists():
        raise HTTPException(404, "条目不存在")

    state: dict = {}

    def mutate_src(data: dict) -> dict:
        entity = _find_entity(data, name)
        if entity is None:
            raise HTTPException(404, f"条目「{name}」不存在")

        old_id = str(entity.get("id", ""))
        _apply_entity_fields(entity, payload)
        new_id = (payload.new_id or "").strip() or old_id
        if new_id != old_id:
            dup = any(
                e is not entity and to_file_name(e.get("id", "")) == to_file_name(new_id)
                for e in data.get("entities", []) or []
            )
            if dup:
                raise HTTPException(409, f"条目「{new_id}」已存在")
            _rename_refs(data, old_id, new_id)
            entity["id"] = new_id
        _dedupe_relations(data)

        state["entity"] = dict(entity)
        state["old_id"] = old_id
        state["new_id"] = str(entity.get("id", ""))

        if target_type != type:
            eid = state["new_id"]
            data["entities"] = [
                e
                for e in data.get("entities", []) or []
                if to_file_name(e.get("id", "")) != to_file_name(eid)
            ]
            state["removed_relations"] = _drop_entity_refs(data, eid)
        return data

    src_data = update_json(src_path, mutate_src, default=_empty_type(type))
    persist_type_data(project_dir, type, src_data)

    entity = state.get("entity")
    if target_type != type and entity is not None:
        tgt_path = xingtu / target_type / "关系脉络.json"

        def mutate_tgt(data: dict) -> dict:
            entities = data.setdefault("entities", [])
            entities[:] = [
                e
                for e in entities
                if to_file_name(e.get("id", "")) != to_file_name(entity.get("id", ""))
            ]
            entities.append(entity)
            return data

        tgt_data = update_json(tgt_path, mutate_tgt, default=_empty_type(target_type))
        persist_type_data(project_dir, target_type, tgt_data)

    old_id = state.get("old_id", "")
    new_id = state.get("new_id", "")
    if old_id and new_id and old_id != new_id and target_type == "角色":
        _move_portrait(project_dir, old_id, new_id)
    if target_type != "角色":
        _delete_portrait(project_dir, old_id or new_id)

    return {
        "success": True,
        "id": new_id,
        "type": target_type,
        "removed_relations": state.get("removed_relations", 0),
    }


@router.delete("/{project_id}/entity/{type}/{name}")
async def delete_entity(project_id: str, type: str, name: str):
    """删除条目（连带其关系、时间线引用、档案与肖像）"""
    project_dir = _get_project_dir(project_id)
    _check_type(type)

    src_path = _get_xingtu_dir(project_id) / "关系" / type / "关系脉络.json"
    if not src_path.exists():
        raise HTTPException(404, "条目不存在")

    state: dict = {}

    def mutate(data: dict) -> dict:
        entity = _find_entity(data, name)
        if entity is None:
            raise HTTPException(404, f"条目「{name}」不存在")
        eid = str(entity.get("id", ""))
        data["entities"] = [
            e for e in data.get("entities", []) or [] if e is not entity
        ]
        state["removed_relations"] = _drop_entity_refs(data, eid)
        state["entity"] = eid
        return data

    data = update_json(src_path, mutate, default=_empty_type(type))
    persist_type_data(project_dir, type, data)

    eid = state.get("entity", "")
    if eid:
        _delete_portrait(project_dir, eid)
    return {
        "success": True,
        "id": eid,
        "removed_relations": state.get("removed_relations", 0),
    }


@router.post("/{project_id}/relation")
async def create_relation(project_id: str, payload: RelationPayload, type: str = "角色"):
    """手动新增关系（标记为手动内容）"""
    project_dir = _get_project_dir(project_id)
    _check_type(type)
    f = payload.from_id.strip()
    t = payload.to_id.strip()
    if not f or not t:
        raise HTTPException(400, "起点与终点不能为空")
    if f == t:
        raise HTTPException(400, "起点与终点不能相同")

    path = _get_xingtu_dir(project_id) / "关系" / type / "关系脉络.json"

    def mutate(data: dict) -> dict:
        rels = data.setdefault("relations", [])
        if any(r.get("from") == f and r.get("to") == t for r in rels):
            raise HTTPException(409, "该关系已存在")
        rels.append({
            "from": f,
            "to": t,
            "type": payload.type or "关联",
            "detail": payload.detail or "",
            "time": payload.time or "",
            "rid": new_rid(),
            "source": "manual",
            "locked": bool(payload.locked),
            "edited_fields": [],
        })
        return data

    data = update_json(path, mutate, default=_empty_type(type))
    persist_type_data(project_dir, type, data)
    return {"success": True}


@router.put("/{project_id}/relation/{rid}")
async def update_relation(
    project_id: str, rid: str, payload: RelationUpdatePayload, type: str = "角色"
):
    """编辑关系（按 rid 定位）"""
    project_dir = _get_project_dir(project_id)
    _check_type(type)

    path = _get_xingtu_dir(project_id) / "关系" / type / "关系脉络.json"
    if not path.exists():
        raise HTTPException(404, "关系不存在")

    def mutate(data: dict) -> dict:
        rel = next(
            (r for r in data.get("relations", []) or [] if r.get("rid") == rid), None
        )
        if rel is None:
            raise HTTPException(404, "关系不存在")

        touched = set(rel.get("edited_fields") or [])
        if payload.from_id is not None:
            rel["from"] = payload.from_id.strip()
            touched.add("from")
        if payload.to_id is not None:
            rel["to"] = payload.to_id.strip()
            touched.add("to")
        if payload.type is not None:
            rel["type"] = payload.type
            touched.add("type")
        if payload.detail is not None:
            rel["detail"] = payload.detail
            touched.add("detail")
        if payload.time is not None:
            rel["time"] = payload.time
            touched.add("time")
        if payload.locked is not None:
            rel["locked"] = bool(payload.locked)
        if not rel.get("from") or not rel.get("to") or rel.get("from") == rel.get("to"):
            raise HTTPException(400, "起点与终点不能为空且不能相同")
        rel["edited_fields"] = sorted(touched)
        return data

    data = update_json(path, mutate, default=_empty_type(type))
    persist_type_data(project_dir, type, data)
    return {"success": True, "rid": rid}


@router.delete("/{project_id}/relation/{rid}")
async def delete_relation(project_id: str, rid: str, type: str = "角色"):
    """删除关系（按 rid 定位）"""
    project_dir = _get_project_dir(project_id)
    _check_type(type)

    path = _get_xingtu_dir(project_id) / "关系" / type / "关系脉络.json"
    if not path.exists():
        raise HTTPException(404, "关系不存在")

    def mutate(data: dict) -> dict:
        rels = data.get("relations", []) or []
        found = next((r for r in rels if r.get("rid") == rid), None)
        if found is None:
            raise HTTPException(404, "关系不存在")
        data["relations"] = [r for r in rels if r is not found]
        return data

    data = update_json(path, mutate, default=_empty_type(type))
    persist_type_data(project_dir, type, data)
    return {"success": True, "rid": rid}

