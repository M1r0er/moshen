"""
墨参 · 写作页路由
卷/章管理、正文保存、AI 编辑审稿、保存后自动扫描（伏笔 + 条目观察）
"""
import hashlib
import json
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from knowledge.project_kb import get_project_kb_manager
from knowledge.flow import get_flow_manager
from knowledge.rules_kb import RulesKB

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


# ===== AI 编辑审稿（单章 / 全书）=====

class ReviewRequest(BaseModel):
    scope: str = "chapter"   # chapter | project
    vol_id: str = ""
    ch_id: str = ""
    task_id: str = ""        # 通用任务进度通道（前端订阅用，可空）


REVIEW_DIMENSIONS = """1. 设定一致性：正文是否与「本书设定树」中的世界观、力量体系、地理/组织/规则相矛盾。
2. 人物一致性（OOC）：人物的言行、动机、语气是否偏离人物档案与既定性格；配角是否沦为工具人。
3. 逻辑与因果：事件是否有足够的动机与因果支撑；时间线、空间连续、信息获取渠道是否合理。
4. 叙事与节奏：场景目标是否明确，冲突张力是否到位，信息释放是否节制，有无注水与拖沓。
5. 文风与表达：是否存在 AI 指纹（套路连接词、万能形容词、排比堆砌）、翻译腔、形容词堆砌。
6. 伏笔与呼应：新埋的钩子是否自然，回收是否有据，有无断裂或被遗忘的线索。"""

REVIEW_FORMAT = """请严格按以下 Markdown 结构输出，不要输出任何额外说明或客套话：

## 总体评价
3-5 句，先给结论（可直接进入修稿 / 建议先改再推进），并点出最突出的优点与最致命的问题。

## 问题清单
按严重度从高到低排列。每条都必须写清「位置 → 问题 → 依据 → 建议」，依据要引用具体设定或前文，不得凭空断言：
- 【严重】位置：… ｜ 问题：… ｜ 依据：… ｜ 建议：…
- 【中等】…
- 【轻微】…

## 修改优先级
列出最该先改的 1-3 处，以及为什么优先改它们。"""


def _strip_html(text: str) -> str:
    """去掉富文本标签，保留纯文本"""
    return re.sub(r"<[^>]+>", "", text or "")


def _clip(text: str, limit: int) -> str:
    """超长文本按「头 65% + 尾 35%」截断，保留开篇与最新章节"""
    if len(text) <= limit:
        return text
    head = int(limit * 0.65)
    tail = limit - head
    return text[:head] + "\n\n…（中间内容已省略）…\n\n" + text[-tail:]


def _settings_brief(project_id: str, per_node: int = 400, budget: int = 6000) -> str:
    """把设定树压成审稿上下文（世界观/人物/力量体系等），按预算截断"""
    kb = _kb()
    path = kb.get_settings_dir(project_id) / "settings_tree.json"
    if not path.exists():
        return ""
    try:
        tree = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return ""

    lines: list[str] = []

    def walk(nodes: list, level: int = 1) -> None:
        for node in nodes or []:
            title = str(node.get("title", "")).strip()
            content = str(node.get("content", "")).strip()
            cat = node.get("category", "other")
            if content:
                content = content[:per_node] + ("…" if len(content) > per_node else "")
                lines.append(f"{'  ' * (level - 1)}- [{cat}] {title}：{content}")
            elif title:
                lines.append(f"{'  ' * (level - 1)}- [{cat}] {title}")
            walk(node.get("children", []), level + 1)

    walk(tree.get("nodes", []))
    if not lines:
        return ""
    return _clip("\n".join(lines), budget)


def _character_brief(project_id: str, budget: int = 4000) -> str:
    """把星图「角色」条目压成人物卡（OOC 判定的依据）"""
    kb = _kb()
    project_dir = kb.get_project_dir(project_id)
    if not project_dir:
        return ""
    data_file = project_dir / "星图" / "关系" / "角色" / "关系脉络.json"
    if not data_file.exists():
        return ""
    try:
        data = json.loads(data_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return ""

    cards: list[str] = []
    for e in data.get("entities", []) or []:
        name = str(e.get("id", "")).strip()
        if not name:
            continue
        fields = []
        for label, key in (("身份", "identity"), ("性别", "gender"), ("年龄", "age"),
                           ("外貌", "appearance"), ("性格", "personality")):
            val = str(e.get(key, "") or "").strip()
            if val:
                fields.append(f"{label}：{val}")
        summary = str(e.get("summary", "") or "").strip()
        if summary:
            fields.append(f"简介：{summary}")
        aliases = [str(a) for a in (e.get("aliases") or []) if str(a).strip()]
        if aliases:
            fields.append("别名：" + "、".join(aliases))
        if fields:
            cards.append(f"- {name} ｜ " + " ｜ ".join(fields))

    if not cards:
        return ""
    return _clip("\n".join(cards), budget)


def _outline_brief(project_id: str, budget: int = 2500) -> str:
    """把大纲节点压成上下文"""
    kb = _kb()
    path = kb.get_outline_path(project_id)
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return ""

    lines = []
    for n in data.get("nodes", []) or []:
        title = str(n.get("title", "")).strip()
        if not title:
            continue
        tag = "支线" if n.get("type") == "branch" else "主线"
        content = str(n.get("content", "") or "").strip()
        if content:
            content = content[:240] + ("…" if len(content) > 240 else "")
            lines.append(f"- [{tag}] {title}：{content}")
        else:
            lines.append(f"- [{tag}] {title}")
    if not lines:
        return ""
    return _clip("\n".join(lines), budget)


def _build_review_messages(project_id: str, scope: str, title: str, body: str) -> list[dict]:
    """构建审稿 prompt：设定树 + 人物档案 + 大纲 + 待审正文"""
    sections = []

    settings = _settings_brief(project_id)
    if settings:
        sections.append("## 本书设定树\n" + settings)

    chars = _character_brief(project_id)
    if chars:
        sections.append("## 人物档案（星图）\n" + chars)

    outline = _outline_brief(project_id)
    if outline:
        sections.append("## 大纲\n" + outline)

    context = "\n\n".join(sections)
    if not context:
        context = (
            "（本书尚未维护设定树 / 人物档案 / 大纲。请仅依据正文内部一致性判断，"
            "并在总体评价中提醒作者先补齐设定与人物档案，以便后续做 OOC 与设定一致性审查。）"
        )

    scope_desc = "本章" if scope == "chapter" else "全书"
    system = (
        "你是墨参，一位资深网络小说编辑，职责是替作者做「审稿」，而不是代笔。\n"
        "你只输出结论文本（Markdown），不寒暄、不复述原文、不重写正文。\n"
        "判断要克制：只有存在明确依据时才判定为问题；依据不足的写成「待确认」，绝不编造设定或前文。"
    )
    user = (
        f"{context}\n\n"
        "---\n"
        f"## 待审内容（{scope_desc}：{title}）\n{body}\n"
        "---\n\n"
        "## 审查维度\n"
        f"{REVIEW_DIMENSIONS}\n\n"
        "## 输出要求\n"
        f"{REVIEW_FORMAT}\n\n"
        "## 创作规范参考\n"
        f"{RulesKB.get_ai_fingerprint_checklist()}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


@router.post("/{project_id}/review")
async def review_writing(project_id: str, req: ReviewRequest):
    """AI 编辑审稿：对作者已写正文做单章 / 全书审查

    输出逻辑一致性、人物 OOC、节奏与文风问题清单，报告落盘 reports/ 并写入知识库。
    单步阻塞任务：进度按 0/1 → 1/1 上报。
    """
    from analysis.tasks import SingleStep
    from core.llm_provider import get_llm_provider

    kb = _kb()
    project = kb.get_project(project_id)
    if not project:
        raise HTTPException(404, "项目不存在")

    step = SingleStep(req.task_id, "writing.review", "AI 审稿")
    step.begin("正在整理设定、人物档案与正文…")

    scope = "project" if req.scope == "project" else "chapter"
    if scope == "chapter":
        if not req.vol_id or not req.ch_id:
            step.fail("请先选择章节")
            raise HTTPException(400, "请先选择章节")
        raw = kb.get_chapter_content(project_id, req.vol_id, req.ch_id)
        if raw is None:
            step.fail("章节不存在")
            raise HTTPException(404, "章节不存在")
        body = _clip(_strip_html(raw).strip(), 20000)
        if not body:
            step.fail("本章正文为空")
            raise HTTPException(400, "本章正文为空，无法审查")

        vol = next(
            (v for v in (kb.get_writing_index(project_id).get("volumes") or []) if v.get("id") == req.vol_id),
            None,
        )
        ch = next((c for c in (vol or {}).get("chapters", []) if c.get("id") == req.ch_id), None)
        title = f"第{(ch or {}).get('number', '')}章 {(ch or {}).get('title', '')}".strip()
    else:
        chapters = kb.get_all_chapters_text(project_id)
        chunks = [
            f"### 第{c.get('ch_number')}章 {c.get('ch_title') or ''}\n{c.get('content', '').strip()}"
            for c in chapters
            if (c.get("content") or "").strip()
        ]
        if not chunks:
            step.fail("本书还没有可审的正文")
            raise HTTPException(400, "本书还没有章节正文")
        body = _clip("\n\n".join(chunks), 28000)
        title = "全书"

    step.begin("正在请求模型审稿…")
    messages = _build_review_messages(project_id, scope, title, body)
    try:
        report = await get_llm_provider().generate(
            messages, role="STRUCTURE_ANALYST", max_tokens=4096
        )
    except Exception as e:
        step.fail(f"{type(e).__name__}: {str(e)[:200]}")
        raise

    report = (report or "").strip()
    if not report:
        step.fail("模型未返回审稿内容")
        raise HTTPException(500, "模型未返回审稿内容")

    report_name = kb.save_diagnosis_report(project_id, report)
    project_dir = kb.get_project_dir(project_id)
    report_path = str(project_dir / "reports" / report_name) if (project_dir and report_name) else ""

    knowledge_title = f"《{project.get('name', '')}》{title}审稿报告"
    knowledge_id = ""
    try:
        from routes.knowledge import _persist_knowledge

        entry = _persist_knowledge(
            title=knowledge_title,
            content=f"# {knowledge_title}\n\n{report}",
            source="analysis",
            source_detail=title,
            ktype="reference",
        )
        if entry:
            knowledge_id = entry["id"]
    except Exception:
        knowledge_id = ""

    step.ok("审稿报告已生成")

    return {
        "report": report,
        "scope": scope,
        "title": title,
        "saved_as": report_name,
        "saved_path": report_path,
        "knowledge_id": knowledge_id,
        "knowledge_title": knowledge_title if knowledge_id else "",
    }


# ===== 保存后自动扫描：伏笔 + 条目观察（合并为一次调用）=====

class LiveScanRequest(BaseModel):
    vol_id: str
    ch_id: str


def _mention_block(index: list[dict], limit: int = 60) -> str:
    """标蓝词表：已知角色 + 别名 + 已分阶段，供模型把正文里的人对上号"""
    lines = []
    for item in index[:limit]:
        aliases = "、".join(item.get("aliases") or []) or "（无）"
        stages = "、".join(s.get("title", "") for s in (item.get("stages") or []))
        lines.append(f"- {item.get('name', '')}（别名：{aliases}）｜已有阶段：{stages or '（尚未分阶段）'}")
    return "\n".join(lines) if lines else "（角色页还没有条目）"


def _live_scan_messages(chapter_label: str, text: str, want_fs: bool, want_entries: bool,
                        fs_names: list[str], char_index: list[dict]) -> list[dict]:
    fs_block = "、".join(fs_names[:80]) if fs_names else "（暂无）"
    if not want_fs:
        fs_task = "A. 伏笔：**本次不检查**，foreshadowings 一律返回空数组 []。"
        fs_schema = '"foreshadowings": [],'
    else:
        fs_task = (
            "A. 伏笔：找出本章**新埋**的伏笔（is_new=true, is_resolution=false），"
            "以及本章对**已有伏笔**的回收/呼应（is_new=false, is_resolution=true）。"
        )
        fs_schema = ('"foreshadowings":[{"name":"短名","content":"引用原文关键句或概括",'
                     '"chapter":"章节标题","is_new":true,"is_resolution":false}],')

    if not want_entries:
        entries_task = "B. 条目观察：**本次不检查**，mentions 一律返回空数组 []。"
        entries_schema = '"mentions": []'
    else:
        entries_task = (
            "B. 条目观察：找出本章提到的**已知角色**，记录其状态变化、疑似新别名、疑似所属阶段；"
            "若出现明显是主要人物但不属于已知角色的，单独标 is_new_character=true（不要硬塞给已有角色）。"
        )
        entries_schema = (
            '"mentions":[{"character":"已知角色名","alias":"本章出现的具体称呼",'
            '"snippet":"引用原文关键句（≤120字）",'
            '"suggest_stage":"疑似所属阶段名，没有就留空",'
            '"is_new_alias":false,"is_new_character":false}]'
        )

    system = (
        "你是网文编辑助理，负责在作者保存章节后快速做两件事：核对本章的伏笔，"
        "以及记录本章对已知角色的提及与状态变化。\n"
        "你只输出一个合法 JSON 对象，不输出任何解释、前后缀或 Markdown 围栏。\n"
        "宁可少报也不要编造：没有把握的条目直接不输出。"
    )
    user = (
        f"本章：{chapter_label}\n\n"
        f"## 已知角色（标蓝词表）\n{_mention_block(char_index)}\n\n"
        f"## 已知伏笔\n{fs_block}\n\n"
        "## 本章正文\n---\n"
        f"{text}\n---\n\n"
        "## 任务\n"
        f"{fs_task}\n"
        f"{entries_task}\n\n"
        "输出结构（两个键都必须出现）：\n"
        "{" + fs_schema + entries_schema + "}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _extract_json_obj(raw: str) -> dict:
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def _apply_foreshadowings(kb, project_id: str, items: list, chapter_label: str) -> dict:
    """把扫描出的伏笔写进伏笔库（新建 / 回收），按名字去重避免重复消耗"""
    existing = {f.get("name"): f for f in kb.list_foreshadowings(project_id)}
    created, resolved = 0, 0

    for it in items or []:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name", "") or "").strip()
        content = str(it.get("content", "") or "").strip()
        if not name:
            continue
        chapter = str(it.get("chapter", "") or "").strip() or chapter_label

        if it.get("is_resolution"):
            target = existing.get(name)
            if target is None:
                continue
            kb.add_entry(project_id, target["id"], content or "（回收）", chapter_id="", chapter_title=chapter)
            if target.get("status") != "resolved":
                kb.update_foreshadowing(project_id, target["id"], None, "resolved", chapter)
                resolved += 1
            continue

        if name in existing:
            continue
        fs = kb.create_foreshadowing(project_id, name, content, "", chapter)
        if fs:
            existing[name] = fs
            created += 1

    return {"created": created, "resolved": resolved}


@router.post("/{project_id}/live-scan")
async def live_scan(project_id: str, req: LiveScanRequest):
    """保存章节后的自动扫描（后台静默调用，失败不影响保存）

    一次调用同时产出「伏笔」与「条目观察」，两类结果分开落盘：
    - 伏笔 → 直接写入伏笔库（新埋新增、回收标记）
    - 条目观察 → 追加到角色页的「AI 观察」收件箱，等作者采纳，绝不改动正式阶段与设定页

    同一章节内容未变化时直接跳过，避免重复消耗 token。
    """
    from core.llm_provider import get_llm_provider
    from knowledge.character_kb import get_character_kb
    from routes.workspace import writing_prefs

    kb = _kb()
    if not kb.get_project(project_id):
        raise HTTPException(404, "项目不存在")

    prefs = writing_prefs()
    want_fs = bool(prefs.get("auto_foreshadowing_detect"))
    want_entries = bool(prefs.get("live_entries_enabled"))
    if not want_fs and not want_entries:
        return {"scanned": False, "skipped": "disabled"}

    raw_content = kb.get_chapter_content(project_id, req.vol_id, req.ch_id)
    if raw_content is None:
        raise HTTPException(404, "章节不存在")
    plain = _strip_html(raw_content).strip()
    if not plain:
        return {"scanned": False, "skipped": "empty"}

    char_kb = get_character_kb()
    scan_key = f"{req.vol_id}/{req.ch_id}"
    digest = hashlib.md5(plain.encode("utf-8")).hexdigest()
    state = char_kb.get_scan(project_id, scan_key)
    if state.get("hash") == digest:
        return {"scanned": False, "skipped": "unchanged"}

    # 章节标题
    index = kb.get_writing_index(project_id)
    vol = next((v for v in (index.get("volumes") or []) if v.get("id") == req.vol_id), None)
    ch = next((c for c in (vol or {}).get("chapters", []) if c.get("id") == req.ch_id), None)
    chapter_label = f"第{(ch or {}).get('number', '')}章 {(ch or {}).get('title', '')}".strip()

    char_index = char_kb.mention_index(project_id) if want_entries else []
    fs_names = [f.get("name", "") for f in kb.list_foreshadowings(project_id)] if want_fs else []

    messages = _live_scan_messages(
        chapter_label, _clip(plain, 12000), want_fs, want_entries, fs_names, char_index
    )
    try:
        raw = await get_llm_provider().generate(messages, role="STRUCTURE_ANALYST", max_tokens=2048)
    except Exception as e:
        raise HTTPException(500, f"扫描失败: {type(e).__name__}: {str(e)[:160]}")

    data = _extract_json_obj(raw)

    fs_result = {"created": 0, "resolved": 0}
    if want_fs:
        fs_result = _apply_foreshadowings(kb, project_id, data.get("foreshadowings") or [], chapter_label)

    obs_added = 0
    new_characters: list[str] = []
    if want_entries:
        by_term: dict[str, dict] = {}
        for item in char_index:
            for term in item.get("terms") or []:
                by_term.setdefault(term, item)

        grouped: dict[str, list[dict]] = {}
        for m in (data.get("mentions") or []):
            if not isinstance(m, dict):
                continue
            snippet = str(m.get("snippet", "") or "").strip()
            if not snippet:
                continue
            name = str(m.get("character", "") or "").strip()
            alias = str(m.get("alias", "") or "").strip()
            hit = by_term.get(name) or by_term.get(alias)

            if hit is None:
                if m.get("is_new_character") and name and name not in new_characters:
                    new_characters.append(name)
                continue

            # 观察只认已知角色；疑似新角色不自动建档（角色页条目必须由作者添加）
            suggest = str(m.get("suggest_stage", "") or "").strip()
            matched_stage = ""
            matched_stage_id = ""
            if suggest:
                for s in (hit.get("stages") or []):
                    if s.get("title") == suggest:
                        matched_stage = s.get("title", "")
                        matched_stage_id = s.get("id", "")
                        break

            grouped.setdefault(hit["cid"], []).append({
                "chapter": chapter_label,
                "vol_id": req.vol_id,
                "ch_id": req.ch_id,
                "snippet": snippet,
                "hit_alias": alias or name,
                "matched_stage": matched_stage,
                "matched_stage_id": matched_stage_id,
                "suggest_stage": suggest,
                "is_new_alias": bool(m.get("is_new_alias")),
            })

        for cid, items in grouped.items():
            obs_added += char_kb.add_observations(project_id, cid, items)

    char_kb.set_scan(project_id, scan_key, digest)

    return {
        "scanned": True,
        "chapter": chapter_label,
        "foreshadowings": fs_result,
        "observations": obs_added,
        "new_characters": new_characters[:10],
    }
