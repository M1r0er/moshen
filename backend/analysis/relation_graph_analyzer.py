"""
墨参 · 关系图谱分析器
从小说文本中提取 6 大类型条目（角色/世界/势力/地点/事件/道具）、关系与时间线。
移植自星图 server/analyzer.js + prompts.js
"""
import json
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


def master_prompt(type_: str, previous: Any, chapters_text: str) -> list[dict]:
    """构建关系图谱分析的 prompt"""
    if previous:
        prev = f'以下是之前已分析出的"{type_}"数据（JSON）：\n{json.dumps(previous, ensure_ascii=False, indent=2)}\n\n'
    else:
        prev = "这是首次分析，还没有历史数据。\n\n"

    type_rule = TYPE_RULES.get(type_, f'只收录与「{type_}」相关的条目。')

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


def changed_entity_ids(prev: dict | None, new: dict) -> set[str]:
    """增量检测：仅返回相比 previous 有变化的实体 id"""
    ids: set[str] = set()
    prev_ids = set(e["id"] for e in (prev.get("entities", []) if prev else []))
    for e in new.get("entities", []):
        if e["id"] not in prev_ids:
            ids.add(e["id"])

    prev_rels = prev.get("relations", []) if prev else []
    prev_rel_keys = set(
        json.dumps([r["from"], r["to"], r["type"], r["detail"], r["time"]], ensure_ascii=False)
        for r in prev_rels
    )
    for r in new.get("relations", []):
        key = json.dumps([r["from"], r["to"], r["type"], r["detail"], r["time"]], ensure_ascii=False)
        if key not in prev_rel_keys:
            ids.add(r["from"])
            ids.add(r["to"])

    prev_tls = prev.get("timeline", []) if prev else []
    prev_tl_keys = set(
        json.dumps([t["time"], t["event"], t.get("refs", [])], ensure_ascii=False)
        for t in prev_tls
    )
    for t in new.get("timeline", []):
        key = json.dumps([t["time"], t["event"], t.get("refs", [])], ensure_ascii=False)
        if key not in prev_tl_keys:
            for ref in t.get("refs", []):
                ids.add(ref)

    return ids


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
        """关系图谱需要合并：保留旧实体/关系，补充新发现"""
        # validate_data 已经做了合并逻辑（基于 previous 更新），
        # 这里直接返回 new（因为 new 是 LLM 基于 previous 输出的完整结果）
        return new

    async def save(self, data: dict, project_dir: Path, ctx: dict) -> None:
        """保存关系脉络主文件 + 条目档案子文件"""
        type_dir = project_dir / "星图" / "关系" / self.type
        type_dir.mkdir(parents=True, exist_ok=True)

        # 主文件
        master_json = type_dir / "关系脉络.json"
        master_md = type_dir / "关系脉络.md"
        atomic_write_json(master_json, data)
        atomic_write_text(master_md, render_master_markdown(data))

        # 增量更新条目档案
        previous = ctx.get("previous_data")
        changed = changed_entity_ids(previous, data)
        output_root = project_dir / "星图" / "关系"

        for entity in data.get("entities", []):
            if entity["id"] not in changed:
                continue
            # 同一条目在不同分类只生成一份档案
            existing = None
            for t in TYPES:
                f = output_root / t / f"{to_file_name(entity['id'])}.md"
                if f.exists() and t != self.type:
                    existing = f
                    break
            if existing:
                continue

            rels = [r for r in data.get("relations", []) if r["from"] == entity["id"] or r["to"] == entity["id"]]
            tls = [t for t in data.get("timeline", []) if entity["id"] in (t.get("refs") or [])]
            md = render_entity_markdown(self.type, entity, rels, tls)
            atomic_write_text(type_dir / f"{to_file_name(entity['id'])}.md", md)
