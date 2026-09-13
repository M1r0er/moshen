"""
墨参 · 关系图谱分析器
从小说文本中提取 6 大类型条目（角色/世界/势力/地点/事件/道具）、关系与时间线。
移植自星图 server/analyzer.js + prompts.js
"""
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from core.safe_io import atomic_write_json, atomic_write_text
from analysis.base import BaseTextAnalyzer
from knowledge.relation_graph_render import (
    render_master_markdown,
    render_entity_markdown,
    to_file_name,
)

# 六大类型
TYPES = ["角色", "世界", "势力", "地点", "事件", "道具"]

# 每种类型的收录边界
TYPE_RULES = {
    "角色": """「角色」只收录有具体名字的具名人物（有名有姓，或全文唯一指代某个特定个人的称呼）。
严禁把以下内容建为「角色」条目：
- 纯职位/官职/称号（如"大将军""皇帝""丞相""城主""队长""统领"）——除非它全文只指代某一个特定个人，此时应并入该人物的 aliases，不单独建无名字的职位条目；
- 群体/组织（如"新联邦""军团""佣兵团""军队"）——归「势力」类型；
- 地名、概念、物品——归相应类型。""",
    "势力": """「势力」收录组织、国家、团体、军队、门派、家族等集体实体（如"新联邦""青云剑派""联邦军""佣兵团"）。
- 个人不收录（含职位个人，如"大将军""司令"）；职位只作为人物与势力之间的隶属关系（relations）出现，不建条目。""",
    "世界": """「世界」收录世界观设定类条目：力量体系、种族、规则、货币、历史纪年、科技/超凡设定等（如"灵力体系""机甲""联邦纪年"）。
- 不收录具体个体、组织、地点（分别归角色/势力/地点）。""",
    "地点": """「地点」收录可具体指认的地点（城、国、山、宗门驻地等，如"长安城""青云剑派山门"）。
- 泛称（如"城里""军营""王宫"）不单独建条目，除非该处是反复出现且重要的特定场所。""",
    "事件": """「事件」收录有明确发生节点、在剧情中构成转折或背景的重要事件（如"××大战""××政变""××登基"）。
- 日常行为、过程性描写不建事件条目。""",
    "道具": """「道具」收录具体物品（武器、法宝、信物、科技装备等，如"青龙偃月刀""联邦通讯器"）。
- 泛指物品（如"剑""枪"）不建条目。""",
}

# 用户可编辑的条目字段（用于字段级保护）
ENTITY_FIELDS = [
    "id", "aliases", "summary", "weight", "category",
    "gender", "age", "identity", "appearance", "personality",
]

# 纯用户所有字段：AI 永不写入、永不覆盖（档案正文由用户维护）
USER_OWNED_FIELDS = ["profile"]


def new_rid() -> str:
    """生成关系条目的稳定唯一标识"""
    return uuid.uuid4().hex


def is_protected(entry: dict) -> bool:
    """手动创建或显式锁定的条目/关系：整条以本地版本为准"""
    return bool(entry.get("locked")) or entry.get("source") == "manual"


def has_manual_content(entry: dict) -> bool:
    """条目/关系中是否含有用户手动维护的内容（不得被 AI 删除）"""
    return is_protected(entry) or bool(entry.get("edited_fields"))


def rel_key(r: dict) -> tuple:
    """关系的业务唯一键（同一对条目只保留一条关系）"""
    return (r.get("from", ""), r.get("to", ""))


def ensure_rids(data: dict) -> None:
    """为缺失 rid 的关系补齐稳定标识（原地修改）"""
    for r in data.get("relations", []) or []:
        if not r.get("rid"):
            r["rid"] = new_rid()


def protect_entry(prev: dict, ai: dict) -> dict:
    """把本地条目中受保护的内容叠加到 AI 结果上（手动优先）

    - source=="manual" 或 locked==True：整条以本地版本为准
    - edited_fields 中列出的字段：保留本地值，其余字段允许 AI 更新
    - profile（档案正文）：始终以本地内容为准
    """
    if is_protected(prev):
        merged = dict(prev)
        merged["locked"] = bool(prev.get("locked"))
        merged["source"] = prev.get("source", "manual")
        merged["edited_fields"] = list(prev.get("edited_fields") or [])
        return merged

    merged = dict(ai)
    merged["source"] = "ai"
    merged["locked"] = bool(prev.get("locked"))
    merged["edited_fields"] = list(prev.get("edited_fields") or [])
    if prev.get("rid"):
        merged["rid"] = prev["rid"]
    for f in merged["edited_fields"]:
        if f in prev:
            merged[f] = prev[f]
    for f in USER_OWNED_FIELDS:
        if prev.get(f):
            merged[f] = prev[f]
    return merged


def merge_manual(previous: Any, ai_data: dict) -> dict:
    """把 previous 中受保护的手动内容叠加到 AI 结果上，手动内容优先。

    - 手动创建/锁定的条目与关系，AI 既不能删除也不能改写
    - 用户手动编辑过的字段保留本地值
    返回合并后的完整数据（不修改入参）。
    """
    if not previous or not isinstance(previous, dict):
        return ai_data

    prev_entities = {
        e.get("id"): e for e in previous.get("entities", []) or [] if e.get("id")
    }
    out_entities: list[dict] = []
    seen: set[str] = set()
    for e in ai_data.get("entities", []) or []:
        eid = e.get("id")
        if not eid or eid in seen:
            continue
        seen.add(eid)
        pe = prev_entities.get(eid)
        out_entities.append(protect_entry(pe, e) if pe else e)
    # AI 漏掉的手动条目补回（保证手动内容不被"分析掉"）
    for eid, pe in prev_entities.items():
        if eid not in seen and has_manual_content(pe):
            out_entities.append(dict(pe))
            seen.add(eid)

    prev_rels = previous.get("relations", []) or []
    ai_rels = ai_data.get("relations", []) or []
    prev_by_key: dict[tuple, list[dict]] = {}
    for r in prev_rels:
        prev_by_key.setdefault(rel_key(r), []).append(r)

    out_rels: list[dict] = []
    consumed: set[int] = set()
    for r in ai_rels:
        candidates = [p for p in prev_by_key.get(rel_key(r), []) if id(p) not in consumed]
        if not candidates:
            out_rels.append(r)
            continue
        prot = next((p for p in candidates if has_manual_content(p)), None)
        if prot is not None:
            consumed.add(id(prot))
            out_rels.append(dict(prot))
        else:
            p = candidates[0]
            consumed.add(id(p))
            out_rels.append(protect_entry(p, r))
    for p in prev_rels:
        if id(p) not in consumed and has_manual_content(p):
            out_rels.append(dict(p))

    merged = dict(ai_data)
    merged["entities"] = out_entities
    merged["relations"] = out_rels
    return merged


def count_protected(data: Any) -> tuple[int, int]:
    """统计受用户保护（手动创建/编辑/锁定）的条目数与关系数"""
    if not data or not isinstance(data, dict):
        return (0, 0)
    ents = sum(1 for e in data.get("entities", []) or [] if has_manual_content(e))
    rels = sum(1 for r in data.get("relations", []) or [] if has_manual_content(r))
    return (ents, rels)


def strip_manual_protection(previous: Any) -> Any:
    """解除全部手动保护标记（"覆盖手动内容"模式使用，需用户显式确认）"""
    if not previous or not isinstance(previous, dict):
        return previous
    out = dict(previous)
    out["entities"] = [
        {**e, "source": "ai", "locked": False, "edited_fields": []}
        for e in previous.get("entities", []) or []
    ]
    out["relations"] = [
        {**r, "source": "ai", "locked": False, "edited_fields": []}
        for r in previous.get("relations", []) or []
    ]
    return out


def master_prompt(type_: str, previous: Any, chapters_text: str) -> list[dict]:
    """构建关系图谱分析的 prompt"""
    if previous:
        prev = f'以下是之前已分析出的"{type_}"数据（JSON）：\n{json.dumps(previous, ensure_ascii=False, indent=2)}\n\n'
    else:
        prev = "这是首次分析，还没有历史数据。\n\n"

    # 明确告知模型哪些条目被用户锁定（禁止改动）
    locked_rule = ""
    if isinstance(previous, dict):
        locked_e = [
            str(e.get("id")) for e in previous.get("entities", []) or [] if has_manual_content(e)
        ]
        locked_r = [
            f'{r.get("from")}→{r.get("to")}'
            for r in previous.get("relations", []) or [] if has_manual_content(r)
        ]
        if locked_e or locked_r:
            notes = []
            if locked_e:
                notes.append("用户手动维护的条目（禁止改名/改分类/删除，必须原样保留）：" + "、".join(locked_e))
            if locked_r:
                notes.append("用户手动维护的关系（禁止修改或删除，必须原样保留）：" + "、".join(locked_r))
            locked_rule = (
                "8. 以上数据中下列内容由用户手动维护，属最高优先级：\n   - "
                + "\n   - ".join(notes)
                + "\n   严禁删除、改名、改分类或改描述；必须把它们的 id、aliases、summary、"
                "category 与关系条目原样保留在输出中。"
            )

    type_rule = TYPE_RULES.get(type_, f'只收录与「{type_}」相关的条目。')
    if locked_rule:
        type_rule = type_rule + "\n" + locked_rule

    system = f"""你是资深小说文本分析引擎。你只输出合法 JSON，不输出任何其他文字、解释或 Markdown 围栏。
你的任务：分析小说章节文本，提取与「{type_}」相关的条目与关系，输出 JSON，结构固定如下：
{{
  "entities": [{{"id": "条目名", "aliases": ["曾用名/化名"], "summary": "一句话概括", "weight": 1, "category": "本质归属分类", "gender": "", "age": "", "identity": "", "appearance": "", "personality": ""}}],
  "relations": [{{"from": "条目A", "to": "条目B", "type": "关系类别", "detail": "具体经过描述", "time": ""}}],
  "timeline": [{{"time": "时间", "event": "事件描述", "refs": ["涉及条目id"]}}]
}}
收录边界（重要）：
{type_rule}
规则：
1. 必须基于新章节文本更新/合并上一轮数据，保留所有旧条目与旧关系（除非新文本推翻），再补充新发现。
2. 关系按最直接类别记录；同一对条目只保留一条关系，取最新的。
3. id 使用条目规范名；同一条目在不同章节的别名、职位、称号并入 aliases，不新建条目。
4. category 标注条目的本质归属：人物/世界/势力/地点/事件/道具 之一。
5. 人物 id 使用小说中最新使用的名字；曾用名并入 aliases。
6. gender/age/identity/appearance/personality 仅对「人物」类条目填写，其他类型留空字符串。
7. 判断不了归属或信息不足时：宁缺毋滥，不硬造条目；summary 写"待补充"，time 填空字符串。"""

    user = f"{prev}以下是新章节文本：\n---\n{chapters_text}\n---\n请输出更新后的完整 JSON。"

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def validate_data(type_: str, raw: Any) -> dict:
    """校验并清洗 LLM 返回的关系图谱数据"""
    src = raw if isinstance(raw, dict) else {}

    # 实体
    entities = []
    seen = set()
    for e in src.get("entities", []) or []:
        eid = str(e.get("id", "")).strip()
        if not eid or eid in seen:
            continue
        seen.add(eid)
        entities.append({
            "id": eid,
            "aliases": [str(a) for a in (e.get("aliases") or [])],
            "summary": str(e.get("summary", "")),
            "weight": e.get("weight") if isinstance(e.get("weight"), int) else 1,
            "category": str(e.get("category", "")).strip(),
            "gender": str(e.get("gender", "")).strip(),
            "age": str(e.get("age", "")).strip(),
            "identity": str(e.get("identity", "")).strip(),
            "appearance": str(e.get("appearance", "")).strip(),
            "personality": str(e.get("personality", "")).strip(),
            # 以下字段由用户维护，AI 输出一律以默认值占位（真正的保护在 merge_manual）
            "profile": "",
            "source": "ai",
            "locked": False,
            "edited_fields": [],
        })

    # 关系
    relations = []
    for r in src.get("relations", []) or []:
        from_ = str(r.get("from", "")).strip()
        to = str(r.get("to", "")).strip()
        if not from_ or not to or from_ == to:
            continue
        relations.append({
            "from": from_,
            "to": to,
            "type": str(r.get("type", "关联")),
            "detail": str(r.get("detail", "")),
            "time": str(r.get("time", "")),
            "rid": "",
            "source": "ai",
            "locked": False,
        })

    # 时间线
    timeline = []
    for t in src.get("timeline", []) or []:
        event = str(t.get("event", "")).strip()
        if not event:
            continue
        timeline.append({
            "time": str(t.get("time", "")).strip(),
            "event": event,
            "refs": [str(r) for r in (t.get("refs") or [])],
        })

    return {
        "type": type_,
        "generatedAt": datetime.now().isoformat(),
        "entities": entities,
        "relations": relations,
        "timeline": timeline,
    }


def persist_type_data(project_dir: Path, type_: str, data: dict) -> None:
    """持久化某个分类的关系脉络：主 JSON + 主 Markdown + 每个条目的档案 Markdown。

    档案 Markdown 是纯派生产物，始终全量重渲染，避免与 JSON 数据源脱节；
    同时清理已删除条目的孤儿档案。
    """
    type_dir = project_dir / "星图" / "关系" / type_
    type_dir.mkdir(parents=True, exist_ok=True)

    ensure_rids(data)

    atomic_write_json(type_dir / "关系脉络.json", data)
    atomic_write_text(type_dir / "关系脉络.md", render_master_markdown(data))

    names = {to_file_name(e["id"]) for e in data.get("entities", []) or [] if e.get("id")}
    for entity in data.get("entities", []) or []:
        eid = entity.get("id")
        if not eid:
            continue
        rels = [
            r for r in data.get("relations", []) or []
            if r.get("from") == eid or r.get("to") == eid
        ]
        tls = [
            t for t in data.get("timeline", []) or []
            if eid in (t.get("refs") or [])
        ]
        atomic_write_text(
            type_dir / f"{to_file_name(eid)}.md",
            render_entity_markdown(type_, entity, rels, tls),
        )

    # 清理本分类中已不存在条目的孤儿档案
    for f in type_dir.glob("*.md"):
        if f.name == "关系脉络.md":
            continue
        if f.stem not in names:
            try:
                f.unlink()
            except OSError:
                pass


class RelationGraphAnalyzer(BaseTextAnalyzer):
    """关系图谱分析器

    提取小说中的角色/世界/势力/地点/事件/道具条目及其关系与时间线。
    """

    name = "关系图谱分析"

    def __init__(self, type_: str = "角色"):
        super().__init__()
        self.type = type_

    def get_role(self) -> str:
        return "NOVEL_ANALYZER"

    def get_response_format(self) -> dict | None:
        """不强制 response_format，部分 API 提供商不兼容"""
        return None

    def build_prompt(self, text: str, previous: Any, ctx: dict) -> list[dict]:
        return master_prompt(self.type, previous, text)

    def parse_result(self, raw: str) -> Any:
        # LLM 返回空内容时，返回空结果（可能该类型无条目）
        if not raw or not raw.strip():
            return validate_data(self.type, {})
        data = self._parse_json(raw)
        if data is None:
            snippet = raw[:500] if raw else "(空)"
            import logging
            logging.warning(f"{self.name} JSON解析失败，原始内容前500字: {snippet}")
            raise ValueError(f"LLM 返回内容无法解析为 JSON（前200字: {snippet[:200]}）")
        return validate_data(self.type, data)

    def validate(self, data: Any) -> bool:
        return isinstance(data, dict) and "entities" in data and "relations" in data

    def merge(self, previous: Any, new: Any) -> Any:
        """关系图谱需要合并：保留旧实体/关系，补充新发现。

        previous 已在 prompt 中作为上下文交给 LLM，LLM 返回的是完整结果，
        因此这里直接返回 new；手动内容的保护由 merge_manual 在落盘前执行。
        """
        return new

    async def save(self, data: dict, project_dir: Path, ctx: dict) -> None:
        """保存关系脉络主文件 + 条目档案子文件"""
        persist_type_data(project_dir, self.type, data)
