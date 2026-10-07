"""墨参 · @ 任务解析（v0.11.0）

把前端提交的结构化引用（references）、章节范围（chapter_scope）与主任务（task），
解析成可注入模型的材料文本 + 覆盖报告。

设计要点：
- 材料由后端按稳定 ID 读取，不信任前端传入的标题与路径；
- 用户原话仍然单独作为对话输入（意图识别与记忆焦点只看原话）；
- 解析在建立 SSE 流之前完成，任何非法输入直接以 HTTP 状态码返回。
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import HTTPException

from knowledge.project_kb import get_project_kb_manager

# 允许出现的引用类型
_REF_TYPES = {"character", "setting", "inspiration", "foreshadowing", "plot_point", "project", "chapter"}

# 章节范围交互上限（不等于模型上下文一定能容纳这么多章全文）
SCOPE_MAX_CHAPTERS = 20

# 任务注册表：key -> 元信息
TASKS = {
    "setting_logic_check": {
        "name": "逻辑检查",
        "target_types": {"setting"},
        "default_scope": {"mode": "latest", "n": 5},
    },
    "setting_derive": {
        "name": "设定衍生",
        "target_types": {"setting"},
        "default_scope": {"mode": "latest", "n": 5},
    },
    "inspiration_introduce": {
        "name": "引入剧情",
        "target_types": {"inspiration"},
        "default_scope": None,
    },
    "plot_point_reuse": {
        "name": "节奏复用",
        "target_types": {"plot_point"},
        "default_scope": {"mode": "latest", "n": 5},
    },
}


class MentionTaskError(HTTPException):
    """带业务错误码的解析错误，在建立 SSE 流之前以 HTTP 状态码返回"""

    def __init__(self, status_code: int, message: str, code: str = ""):
        super().__init__(status_code, message)
        self.code = code
        self.message = message


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _kb():
    return get_project_kb_manager()


# ===== 引用解析 =====

def _resolve_setting(project_id: str, ref_id: str) -> str | None:
    from routes.settings_writer import load_tree, _find_node, CATEGORIES

    node = _find_node(load_tree(project_id)["nodes"], ref_id)
    if not node:
        return None
    lines = [f"【设定】{node.get('title', '')}（分类：{CATEGORIES.get(node.get('category'), '其他')}）"]
    if node.get("content"):
        lines.append(str(node["content"]))
    children = node.get("children") or []
    if children:
        lines.append("包含子条目：" + "、".join(str(c.get("title", "")) for c in children))
    return "\n".join(lines)


def _resolve_character(project_id: str, ref_id: str) -> str | None:
    from knowledge.character_kb import get_character_kb

    c = get_character_kb().get(project_id, ref_id)
    if not c:
        return None
    lines = [f"【角色】{c.get('name', '')}"]
    if c.get("aliases"):
        lines.append("别名：" + "、".join(c["aliases"]))
    if c.get("profile"):
        lines.append("档案：" + str(c["profile"]))
    for s in c.get("stages") or []:
        seg = f"- {s.get('title', '')}"
        if s.get("note"):
            seg += f"：{s['note']}"
        lines.append(seg)
    return "\n".join(lines)


def _resolve_inspiration(ref_id: str) -> str | None:
    from routes.workspace import get_inspiration_path, read_file_safe

    insp = get_inspiration_path()
    if not insp:
        return None
    base = Path(insp).resolve()
    try:
        filepath = (base / ref_id).resolve()
        filepath.relative_to(base)
    except (ValueError, OSError):
        return None
    content = read_file_safe(filepath)
    if content is None:
        return None
    return f"【灵感】{filepath.name}\n{content}"


def _resolve_foreshadowing(project_id: str, ref_id: str) -> str | None:
    f = _kb().get_foreshadowing(project_id, ref_id)
    if not f:
        return None
    lines = [f"【伏笔】{f.get('name', '')}", f"状态：{'已回收' if f.get('status') == 'resolved' else '未回收'}"]
    if f.get("resolution_chapter"):
        lines.append("回收章节：" + str(f["resolution_chapter"]))
    for e in f.get("entries") or []:
        lines.append(f"- [{e.get('chapter_title', '')}] {e.get('content', '')}")
    return "\n".join(lines)


def _resolve_plot_point(project_id: str, ref_id: str) -> str | None:
    from knowledge.plot_points import get_plot_point_manager

    p = get_plot_point_manager().get_point(project_id, ref_id)
    if not p:
        return None
    lines = [
        f"【爽爆点】{p.get('title', '')}",
        f"类型：{p.get('type', '')}　套路：{p.get('trope') or '无'}",
        f"已用次数：{p.get('used_count') or 0}（疲劳阈值 {p.get('fatigue_threshold') or '项目默认'}）",
    ]
    if p.get("segment"):
        lines.append("段落：" + str(p["segment"]))
    if p.get("content"):
        lines.append("内容：" + str(p["content"]))
    for n in (p.get("usage_notes") or [])[-5:]:
        lines.append(f"- 使用记录：[{n.get('chapter_key', '')}] {n.get('note', '')}")
    va = (p.get("value_analyses") or [])
    if va:
        last = va[-1]
        lines.append("【此前价值分析快照】" + (last.get("summary") or ""))
        if last.get("rhythm_pattern"):
            lines.append("节奏模式：" + str(last["rhythm_pattern"]))
        if last.get("reuse_conditions"):
            lines.append("复用条件：" + "、".join(str(x) for x in last["reuse_conditions"]))
    return "\n".join(lines)


def _resolve_project(project_id: str) -> str | None:
    meta = _kb().get_project(project_id) or {}
    desc = (meta.get("description") or "").strip()
    if not desc:
        return None
    return f"【项目梗概】{meta.get('name', '')}\n{desc}"


def _resolve_chapter_ref(project_id: str, ref_id: str) -> str | None:
    if "/" not in ref_id:
        return None
    vol_id, ch_id = ref_id.split("/", 1)
    html = _kb().get_chapter_content(project_id, vol_id, ch_id)
    if html is None:
        return None
    plain = _strip_html(html)
    if not plain:
        return None
    return f"【章节引用】{vol_id}/{ch_id}\n{plain}"


def _resolve_reference(project_id: str, ref: dict) -> tuple[dict, str | None]:
    """返回 (覆盖信息, 材料文本)。材料文本为 None 表示读取失败/缺失。"""
    rtype = str(ref.get("type", "")).strip()
    rid = str(ref.get("id", "")).strip()
    title = str(ref.get("title", "")).strip()
    if rtype not in _REF_TYPES or not rid:
        raise MentionTaskError(400, f"引用不合法：{rtype}", "INVALID_REFERENCE")

    if rtype == "setting":
        block = _resolve_setting(project_id, rid)
    elif rtype == "character":
        block = _resolve_character(project_id, rid)
    elif rtype == "inspiration":
        block = _resolve_inspiration(rid)
    elif rtype == "foreshadowing":
        block = _resolve_foreshadowing(project_id, rid)
    elif rtype == "plot_point":
        block = _resolve_plot_point(project_id, rid)
    elif rtype == "project":
        block = _resolve_project(project_id)
    elif rtype == "chapter":
        block = _resolve_chapter_ref(project_id, rid)
    else:
        block = None

    state = "full" if block else "missing"
    return {"type": rtype, "id": rid, "title": title, "state": state}, block


# ===== 章节范围解析与冻结 =====

def _flatten_chapters(index: dict) -> list[dict]:
    out: list[dict] = []
    seq = 0
    for v in index.get("volumes", []) or []:
        for c in v.get("chapters", []) or []:
            seq += 1
            out.append({
                "vol_id": v.get("id"),
                "ch_id": c.get("id"),
                "sequence": seq,
                "vol_number": v.get("number"),
                "vol_title": v.get("title"),
                "number": c.get("number"),
                "title": c.get("title"),
                "words": c.get("words", 0),
            })
    return out


def _resolve_scope(project_id: str, scope: dict | None) -> tuple[list[dict], bool]:
    """返回 (选中的章节列表, 是否因超上限而被截断)。"""
    if not scope:
        return [], False
    mode = str(scope.get("mode", "")).strip()
    all_ch = _flatten_chapters(_kb().get_writing_index(project_id))

    if mode == "latest":
        n = int(scope.get("n") or scope.get("count") or 5)
        n = max(1, min(n, SCOPE_MAX_CHAPTERS))
        sel = all_ch[-n:] if all_ch else []
    elif mode == "range":
        start = scope.get("start")
        end = scope.get("end")
        if start is None or end is None:
            raise MentionTaskError(400, "章节范围不完整", "INVALID_CHAPTER_SCOPE")
        start, end = int(start), int(end)
        if start > end:
            raise MentionTaskError(400, "章节范围起点大于终点", "INVALID_CHAPTER_SCOPE")
        if end - start + 1 > SCOPE_MAX_CHAPTERS:
            raise MentionTaskError(400, f"章节范围最多 {SCOPE_MAX_CHAPTERS} 章", "INVALID_CHAPTER_SCOPE")
        sel = [c for c in all_ch if start <= c["sequence"] <= end]
    else:
        raise MentionTaskError(400, "未知的章节范围模式", "INVALID_CHAPTER_SCOPE")

    if not sel:
        raise MentionTaskError(400, "章节范围为空或不存在", "INVALID_CHAPTER_SCOPE")
    return sel, False


def _build_chapter_material(project_id: str, sel: list[dict]) -> tuple[str, dict]:
    """生成章节材料文本 + 覆盖信息。<=5 章给全文；6~20 章给「末 2 章全文 + 其余开头摘录」。"""
    total = len(sel)
    full_blocks: list[str] = []
    excerpt_blocks: list[str] = []
    resolved: list[dict] = []
    empty_chapters: list[str] = []
    summarized: list[str] = []
    excerpted: list[str] = []

    for i, c in enumerate(sel):
        html = _kb().get_chapter_content(project_id, c["vol_id"], c["ch_id"])
        plain = _strip_html(html or "")
        label = f"第{c['sequence']}章 {c.get('title') or ''}".strip()
        if not plain:
            empty_chapters.append(label)
            resolved.append({**{k: c[k] for k in ("vol_id", "ch_id", "sequence", "title", "words")}, "state": "empty"})
            continue
        if total <= 5 or i >= total - 2:
            full_blocks.append(f"### {label}\n{plain}")
            resolved.append({**{k: c[k] for k in ("vol_id", "ch_id", "sequence", "title", "words")}, "state": "full"})
        else:
            excerpt_blocks.append(f"### {label}（摘录）\n{plain[:220]}")
            resolved.append({**{k: c[k] for k in ("vol_id", "ch_id", "sequence", "title", "words")}, "state": "excerpt"})
            excerpted.append(label)

    parts: list[str] = []
    if full_blocks:
        parts.append("--- 章节正文 ---\n" + "\n\n".join(full_blocks))
    if excerpt_blocks:
        parts.append("--- 章节摘录（仅开头片段）---\n" + "\n\n".join(excerpt_blocks))
    text = "\n\n".join(parts)

    coverage = {
        "resolved_chapters": resolved,
        "empty_chapters": empty_chapters,
        "summarized": summarized,
        "excerpted": excerpted,
    }
    return text, coverage


# ===== 任务指令模板 =====

def _task_instruction(key: str, target_titles: list[str], has_scope: bool) -> str:
    titles = "、".join(t for t in target_titles if t) or "目标条目"
    if key == "setting_logic_check":
        return (
            f"# 任务：设定逻辑检查（目标：{titles}）\n"
            "请检查该设定是否与本书其他设定、目前情节存在逻辑冲突。\n"
            "输出要求：\n"
            "1. 逐条列出冲突点，每条标注严重程度（硬冲突 / 软冲突 / 潜在风险）；\n"
            "2. 给出冲突对象（另一条设定或具体章节）；\n"
            "3. 引用原文片段作为证据，不要凭空断言；\n"
            "4. 每条给出最小改动建议；\n"
            "5. 若在已提供材料内未发现冲突，请明确说明「未发现明确冲突」并列出未覆盖的材料。"
        )
    if key == "setting_derive":
        return (
            f"# 任务：设定衍生（目标：{titles}）\n"
            "请基于该设定与当前情节，衍生出 3~6 条合理的新设定候选（如可以出现的势力、物品、功法、组织等）。\n"
            "输出要求：每条包含「名称 / 分类 / 内容 / 衍生依据 / 可登场时机」，避免与已有设定重复。"
        )
    if key == "inspiration_introduce":
        base = (
            "# 任务：灵感引入剧情\n"
            "请分析如何把这条灵感引入本小说。\n"
            "输出要求：\n"
            "1. 给出 2~3 种引入路径（长线铺垫 / 支线 / 主线转折）；\n"
            "2. 说明与项目梗概、设定体系的契合点与冲突点；\n"
            "3. 引用具体场景时必须来自实际章节，不要编造章号、台词或事件。"
        )
        if has_scope:
            base += "\n4. 作者希望尽快引入，请进一步给出「在第几章哪个场景之后插入」的具体落点与衔接草案。"
        return base
    if key == "plot_point_reuse":
        return (
            f"# 任务：爽爆点节奏复用（目标：{titles}）\n"
            "请分析这段剧情节奏 / 爆点类型能否在最新章节中复用。\n"
            "输出要求：\n"
            "1. 先给出结论：适合 / 谨慎 / 不建议，并说明理由（参考疲劳度、上次使用距今章数、与近章节奏相似度）；\n"
            "2. 若适合，给出在目标章节中复用的节奏拆解（铺垫 / 蓄力 / 引爆 / 余波分别落在哪里）；\n"
            "3. 指出需要变形的要素，避免读者觉得重复套路；\n"
            "4. 同时说明不适合直接复用的风险。"
        )
    return ""


# ===== 对外入口 =====

def prepare_task_context(
    project_id: str,
    references: list[dict] | None,
    task: dict | None,
    chapter_scope: dict | None,
) -> dict:
    """解析引用 / 章节 / 任务，返回 {"material": str, "coverage": dict}。"""
    if not project_id or not _kb().get_project(project_id):
        raise MentionTaskError(404, "项目不存在", "PROJECT_NOT_FOUND")

    refs = list(references or [])

    # 1) 任务校验
    task_key = ""
    target_titles: list[str] = []
    target_ids: list[str] = []
    if task:
        task_key = str(task.get("key", "")).strip()
        spec = TASKS.get(task_key)
        if not spec:
            raise MentionTaskError(400, f"未知任务：{task_key}", "INVALID_TASK")
        target_ids = [str(x) for x in (task.get("target_ids") or [])]
        ref_by_id = {str(r.get("id")): r for r in refs}
        for tid in target_ids:
            r = ref_by_id.get(tid)
            if not r:
                raise MentionTaskError(400, f"任务目标不在引用中：{tid}", "INVALID_TASK")
            if str(r.get("type")) not in spec["target_types"]:
                raise MentionTaskError(400, "任务与条目类型不匹配", "INVALID_TASK")
            if r.get("title"):
                target_titles.append(str(r["title"]))
        if not target_ids:
            raise MentionTaskError(400, "任务缺少目标条目", "INVALID_TASK")

    # 2) 解析引用材料
    ref_blocks: list[str] = []
    ref_coverage: list[dict] = []
    missing: list[str] = []
    for r in refs:
        cov, block = _resolve_reference(project_id, r)
        ref_coverage.append(cov)
        if block:
            ref_blocks.append(block)
        else:
            missing.append(f"{cov['type']}:{cov['title'] or cov['id']}")

    # 3) 章节范围（任务默认值兜底）
    scope = chapter_scope
    if not scope and task_key:
        scope = TASKS.get(task_key, {}).get("default_scope")
    chapter_text, ch_cov = ("", {"resolved_chapters": [], "empty_chapters": [], "summarized": [], "excerpted": []})
    if scope:
        sel, _ = _resolve_scope(project_id, scope)
        chapter_text, ch_cov = _build_chapter_material(project_id, sel)
    elif task_key == "plot_point_reuse":
        raise MentionTaskError(400, "节奏复用需要章节范围", "SCOPE_REQUIRED")

    # 4) 组装材料
    parts: list[str] = []
    if ref_blocks:
        parts.append("--- 引用条目 ---\n" + "\n\n".join(ref_blocks))
    if chapter_text:
        parts.append(chapter_text)
    if task_key:
        parts.append(_task_instruction(task_key, target_titles, bool(ch_cov["resolved_chapters"])))
    material = "\n\n".join(parts)

    warnings: list[str] = []
    if missing:
        warnings.append("以下引用未读到内容：" + "、".join(missing))
    if ch_cov["empty_chapters"]:
        warnings.append("以下章节为空：" + "、".join(ch_cov["empty_chapters"]))
    if task_key == "plot_point_reuse":
        from knowledge.plot_points import get_plot_point_manager

        has_va = any(
            get_plot_point_manager().latest_value_analysis(project_id, tid)
            for tid in target_ids
        )
        if not has_va:
            warnings.append("未找到此前价值分析快照，结论基于当前材料，请声明局限；可在爽爆点面板保存为价值分析。")

    coverage = {
        "task": task_key or None,
        "resolved_chapters": ch_cov["resolved_chapters"],
        "references": ref_coverage,
        "missing": missing,
        "empty_chapters": ch_cov["empty_chapters"],
        "summarized": ch_cov["summarized"],
        "excerpted": ch_cov["excerpted"],
        "truncated": bool(ch_cov["excerpted"]),
        "warnings": warnings,
    }
    return {"material": material, "coverage": coverage}
