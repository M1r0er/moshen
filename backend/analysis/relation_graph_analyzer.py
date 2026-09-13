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


def _str_list(value: Any) -> list[str]:
    """把任意输入规范化为去重后的字符串列表"""
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for v in value:
        s = str(v).strip()
        if s and s not in out:
            out.append(s)
    return out


def ensure_rids(data: dict) -> None:
    """为缺失 rid 的关系补齐稳定标识（原地修改）"""
    for r in data.get("relations", []) or []:
        if not r.get("rid"):
            r["rid"] = new_rid()


# ===== 更新模式：变更补丁 =====

# 补丁允许出现的键（缺省视为空）
PATCH_KEYS = [
    "entities_add", "entities_update", "entities_remove",
    "relations_add", "relations_update", "relations_remove",
    "timeline_add",
]

# 关系可被 AI 修改的字段
RELATION_FIELDS = ["type", "detail", "time"]


def empty_data(type_: str) -> dict:
    """空的关系脉络数据"""
    return {
        "type": type_,
        "generatedAt": "",
        "entities": [],
        "relations": [],
        "timeline": [],
    }


def field_blocked(entry: dict, field: str, protect: bool) -> bool:
    """字段是否被"用户内容"保护（AI 不可改写）"""
    if not protect:
        return False
    if field in USER_OWNED_FIELDS:
        return True
    return field in (entry.get("edited_fields") or [])


def _set_relation_fields(rel: dict, values: dict, protect: bool, report: dict) -> None:
    """把 AI 提出的关系字段变更写入关系，跳过受保护字段"""
    changed = False
    for key in RELATION_FIELDS:
        if key not in (values or {}):
            continue
        if field_blocked(rel, key, protect):
            report["protected_fields"] += 1
            continue
        if rel.get(key) == values[key]:
            continue
        rel[key] = values[key]
        changed = True
    if changed:
        report["updated"] += 1
    else:
        report["noop"] += 1


def _set_entity_fields(
    ent: dict, values: dict, aliases_add: list, protect: bool, report: dict
) -> None:
    """把 AI 提出的条目字段变更写入条目，跳过受保护字段"""
    changed = False
    for key, val in (values or {}).items():
        if key == "id" or key == "aliases" or key not in ENTITY_FIELDS:
            continue
        if field_blocked(ent, key, protect):
            report["protected_fields"] += 1
            continue
        if ent.get(key) == val:
            continue
        ent[key] = val
        changed = True

    if aliases_add:
        if field_blocked(ent, "aliases", protect):
            report["protected_fields"] += 1
        else:
            merged = list(ent.get("aliases") or [])
            for a in aliases_add:
                if a and a not in merged:
                    merged.append(a)
                    changed = True
            ent["aliases"] = merged

    if changed:
        report["updated"] += 1
    else:
        report["noop"] += 1


def validate_patch(type_: str, raw: Any) -> dict:
    """校验并清洗 LLM 返回的「变更补丁」

    兼容两种输入：
    - 新协议：entities_add / entities_update / ... 补丁结构
    - 旧格式（entities/relations/timeline 全量）：安全降级为"仅允许新增"，
      不允许改动已有内容（避免一次格式误判把整份设定库重写掉）
    """
    src = raw if isinstance(raw, dict) else {}
    patch: dict[str, Any] = {k: [] for k in PATCH_KEYS}
    patch["type"] = type_

    if not any(k in src for k in PATCH_KEYS):
        if "entities" in src or "relations" in src or "timeline" in src:
            patch["legacy_fallback"] = True
            src = {
                "entities_add": src.get("entities") or [],
                "relations_add": src.get("relations") or [],
                "timeline_add": src.get("timeline") or [],
            }

    # ---- 新增条目 ----
    seen: set[str] = set()
    for e in src.get("entities_add") or []:
        if not isinstance(e, dict):
            continue
        eid = str(e.get("id", "")).strip()
        if not eid or eid in seen:
            continue
        seen.add(eid)
        item = {"id": eid}
        item["aliases"] = _str_list(e.get("aliases"))
        item["summary"] = str(e.get("summary", ""))
        item["weight"] = e.get("weight") if isinstance(e.get("weight"), int) else 1
        item["category"] = str(e.get("category", "")).strip()
        for f in ("gender", "age", "identity", "appearance", "personality"):
            item[f] = str(e.get(f, "")).strip()
        item["reason"] = str(e.get("reason", ""))
        patch["entities_add"].append(item)

    # ---- 更新条目：{id, set:{...}, aliases_add:[...], reason} ----
    for u in src.get("entities_update") or []:
        if not isinstance(u, dict):
            continue
        eid = str(u.get("id", "")).strip()
        if not eid:
            continue
        raw_set = u.get("set") if isinstance(u.get("set"), dict) else {}
        clean: dict[str, Any] = {}
        for k, v in raw_set.items():
            k = str(k)
            if k not in ENTITY_FIELDS or k in ("id", "aliases"):
                continue
            clean[k] = v if k == "weight" and isinstance(v, int) else str(v)
        patch["entities_update"].append({
            "id": eid,
            "set": clean,
            "aliases_add": _str_list(u.get("aliases_add")),
            "reason": str(u.get("reason", "")),
        })

    # ---- 删除条目 ----
    for r in src.get("entities_remove") or []:
        if not isinstance(r, dict):
            continue
        eid = str(r.get("id", "")).strip()
        if eid:
            patch["entities_remove"].append({"id": eid, "reason": str(r.get("reason", ""))})

    # ---- 新增关系 ----
    for r in src.get("relations_add") or []:
        if not isinstance(r, dict):
            continue
        f = str(r.get("from", "")).strip()
        t = str(r.get("to", "")).strip()
        if not f or not t or f == t:
            continue
        patch["relations_add"].append({
            "from": f, "to": t,
            "type": str(r.get("type", "关联")),
            "detail": str(r.get("detail", "")),
            "time": str(r.get("time", "")),
            "reason": str(r.get("reason", "")),
        })

    # ---- 更新关系 ----
    for r in src.get("relations_update") or []:
        if not isinstance(r, dict):
            continue
        f = str(r.get("from", "")).strip()
        t = str(r.get("to", "")).strip()
        if not f or not t:
            continue
        raw_set = r.get("set") if isinstance(r.get("set"), dict) else {}
        clean = {k: str(raw_set[k]) for k in RELATION_FIELDS if k in raw_set}
        patch["relations_update"].append({
            "from": f, "to": t, "set": clean, "reason": str(r.get("reason", "")),
        })

    # ---- 删除关系 ----
    for r in src.get("relations_remove") or []:
        if not isinstance(r, dict):
            continue
        f = str(r.get("from", "")).strip()
        t = str(r.get("to", "")).strip()
        if f and t:
            patch["relations_remove"].append({
                "from": f, "to": t, "reason": str(r.get("reason", "")),
            })

    # ---- 新增时间线 ----
    for t in src.get("timeline_add") or []:
        if not isinstance(t, dict):
            continue
        event = str(t.get("event", "")).strip()
        if not event:
            continue
        patch["timeline_add"].append({
            "time": str(t.get("time", "")).strip(),
            "event": event,
            "refs": _str_list(t.get("refs")),
        })

    return patch


def apply_patch(base: Any, patch: dict, protect: bool = True) -> tuple[dict, dict]:
    """把「变更补丁」增量应用到现有数据上。

    与旧的全量覆盖不同：**现有数据是骨架**，AI 只提交增 / 改 / 删。
    AI 没有提到的条目、字段、关系一律原样保留，因此：
    - 随剧情推进的更新不会丢失此前累积的细节
    - 删除变成显式动作（entities_remove / relations_remove）
    - 手动创建 / 编辑 / 锁定的内容不会被触碰

    Returns: (新数据, 变更报告)
    """
    src = base if isinstance(base, dict) else {}
    data = {
        "type": patch.get("type") or src.get("type", ""),
        "generatedAt": datetime.now().isoformat(),
        "entities": [dict(e) for e in src.get("entities", []) or []],
        "relations": [dict(r) for r in src.get("relations", []) or []],
        "timeline": [dict(t) for t in src.get("timeline", []) or []],
    }
    report: dict[str, Any] = {
        "added": 0, "updated": 0, "removed": 0, "noop": 0,
        "protected_entities": 0, "protected_relations": 0, "protected_fields": 0,
        "skipped": [],
    }
    legacy = bool(patch.get("legacy_fallback"))

    by_id = {e.get("id"): e for e in data["entities"] if e.get("id")}

    # ---- 新增条目 ----
    for e in patch.get("entities_add") or []:
        eid = e.get("id")
        if not eid:
            continue
        cur = by_id.get(eid)
        if cur is None:
            ent = {k: e.get(k) for k in ENTITY_FIELDS}
            ent["id"] = eid
            ent["profile"] = ""
            ent["source"] = "ai"
            ent["locked"] = False
            ent["edited_fields"] = []
            data["entities"].append(ent)
            by_id[eid] = ent
            report["added"] += 1
        elif legacy:
            # 旧格式降级：已存在的条目一律不改，避免整库被重写
            report["skipped"].append("旧格式响应，忽略对已有条目「%s」的改动" % eid)
        else:
            # 幂等：上一批次已创建，则本次按字段更新处理
            vals = {k: e[k] for k in ENTITY_FIELDS if k in e and k != "id"}
            _set_entity_fields(cur, vals, e.get("aliases"), protect, report)

    # ---- 更新条目 ----
    for u in patch.get("entities_update") or []:
        ent = by_id.get(u.get("id"))
        if ent is None:
            report["skipped"].append("更新未命中条目「%s」" % u.get("id"))
            continue
        if protect and is_protected(ent):
            report["protected_entities"] += 1
            continue
        _set_entity_fields(ent, u.get("set") or {}, u.get("aliases_add") or [], protect, report)

    # ---- 删除条目（连带清理引用）----
    removed_ids: list[str] = []
    for r in patch.get("entities_remove") or []:
        eid = r.get("id")
        ent = by_id.get(eid)
        if ent is None:
            continue
        if protect and has_manual_content(ent):
            report["protected_entities"] += 1
            continue
        data["entities"] = [x for x in data["entities"] if x is not ent]
        by_id.pop(eid, None)
        removed_ids.append(eid)
        report["removed"] += 1

    if removed_ids:
        data["relations"] = [
            r for r in data["relations"]
            if r.get("from") not in removed_ids and r.get("to") not in removed_ids
        ]
        for t in data["timeline"]:
            t["refs"] = [x for x in (t.get("refs") or []) if x not in removed_ids]

    def find_rel(from_: str, to: str):
        return next(
            (r for r in data["relations"]
             if r.get("from") == from_ and r.get("to") == to),
            None,
        )

    # ---- 新增关系 ----
    for r in patch.get("relations_add") or []:
        f, t = r.get("from"), r.get("to")
        if not f or not t or f == t:
            continue
        cur = find_rel(f, t)
        if cur is None:
            data["relations"].append({
                "from": f, "to": t,
                "type": r.get("type") or "关联",
                "detail": r.get("detail", ""),
                "time": r.get("time", ""),
                "rid": new_rid(), "source": "ai", "locked": False, "edited_fields": [],
            })
            report["added"] += 1
        elif legacy:
            report["skipped"].append("旧格式响应，忽略对已有关系「%s→%s」的改动" % (f, t))
        else:
            _set_relation_fields(cur, {k: r[k] for k in RELATION_FIELDS if k in r}, protect, report)

    # ---- 更新关系 ----
    for u in patch.get("relations_update") or []:
        cur = find_rel(u.get("from"), u.get("to"))
        if cur is None:
            report["skipped"].append("更新未命中关系「%s→%s」" % (u.get("from"), u.get("to")))
            continue
        if protect and is_protected(cur):
            report["protected_relations"] += 1
            continue
        _set_relation_fields(cur, u.get("set") or {}, protect, report)

    # ---- 删除关系 ----
    for r in patch.get("relations_remove") or []:
        cur = find_rel(r.get("from"), r.get("to"))
        if cur is None:
            continue
        if protect and has_manual_content(cur):
            report["protected_relations"] += 1
            continue
        data["relations"] = [x for x in data["relations"] if x is not cur]
        report["removed"] += 1

    # ---- 新增时间线（按 时间+事件 去重）----
    existing_tl = {(t.get("time"), t.get("event")) for t in data["timeline"]}
    for t in patch.get("timeline_add") or []:
        key = (t.get("time"), t.get("event"))
        if key in existing_tl:
            continue
        data["timeline"].append({
            "time": t.get("time", ""),
            "event": t.get("event", ""),
            "refs": list(t.get("refs") or []),
        })
        existing_tl.add(key)
        report["added"] += 1

    return data, report


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
    """构建关系图谱分析的 prompt（更新模式：只输出变更补丁）"""
    if previous:
        prev = (
            f'以下是当前"{type_}"设定库的现状（JSON）：\n'
            f'{json.dumps(previous, ensure_ascii=False, indent=2)}\n\n'
        )
    else:
        prev = "本次不提供现有数据，请只提交新增内容。\n\n"

    # 明确告知模型哪些条目被用户手动维护（禁止改动）
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
                notes.append("用户手动维护的条目：" + "、".join(locked_e))
            if locked_r:
                notes.append("用户手动维护的关系：" + "、".join(locked_r))
            locked_rule = (
                "10. 以下内容由作者本人手动维护，属最高优先级：\n   - "
                + "\n   - ".join(notes)
                + "\n   **禁止**把它们放进 entities_update / entities_remove / "
                "relations_update / relations_remove；只有当它们尚未存在（即不在现有数据里）时，"
                "才可以用 entities_add / relations_add 补充。"
            )

    type_rule = TYPE_RULES.get(type_, f'只收录与「{type_}」相关的条目。')
    if locked_rule:
        type_rule = type_rule + "\n" + locked_rule

    system = f"""你是资深小说文本分析引擎，负责长期维护一份「{type_}」设定库。
你只输出合法 JSON，不输出任何其他文字、解释或 Markdown 围栏。

你的任务**不是重写整份设定库**，而是只提交一份「变更补丁」：
把从新章节里读到的**新增、修订、作废**，以最小改动的方式写出来。
未被你提及的内容会被原样保留，因此不必、也禁止为了"完整"而回传旧内容。

输出结构（所有键都必须出现，没有内容的写空数组 []）：
{{
  "entities_add": [{{"id": "条目名", "aliases": ["别名"], "summary": "一句话概括", "weight": 1, "category": "本质归属", "gender": "", "age": "", "identity": "", "appearance": "", "personality": "", "reason": "新增依据"}}],
  "entities_update": [{{"id": "已有条目名", "set": {{"identity": "新的身份"}}, "aliases_add": ["新别名"], "reason": "依据（章节/情节）"}}],
  "entities_remove": [{{"id": "条目名", "reason": "作废依据"}}],
  "relations_add": [{{"from": "条目A", "to": "条目B", "type": "关系类别", "detail": "具体经过", "time": "", "reason": ""}}],
  "relations_update": [{{"from": "条目A", "to": "条目B", "set": {{"detail": "新的描述"}}, "reason": ""}}],
  "relations_remove": [{{"from": "条目A", "to": "条目B", "reason": ""}}],
  "timeline_add": [{{"time": "时间", "event": "事件描述", "refs": ["涉及条目id"]}}]
}}

收录边界（重要）：
{type_rule}

硬性规则：
1. **只写变化**。与现有数据一致的条目、字段、关系一律不要出现在输出里；四类数组可以同时为空。
2. **禁止整体重写**。要修订已有条目，只能放进 entities_update，且 set 里**只放真正发生变化的字段**。
   例：某角色升官，只写 {{"id":"某某","set":{{"identity":"新任官职"}},"reason":"第12章受封"}}，不要附带其它字段。
3. **不要回传未变化的旧内容**：你没提到的字段会被原样保留，不会丢失，这一点无需担心。
4. **删除要克制且写明依据**。只有新文本明确推翻时才用 entities_remove / relations_remove
   （人物死亡、身份彻底转变、设定被废弃、上一轮确属误建），并在 reason 里写清依据；拿不准就不要删。
5. entities_update / entities_remove / relations_update / relations_remove 的 id 与 from/to，
   必须与「现有数据」中的写法完全一致。
6. 关系按最直接类别记录；同一对条目只保留一条关系（同一对要改就放 relations_update）。
7. category 标注条目的本质归属：人物/世界/势力/地点/事件/道具 之一。
8. gender/age/identity/appearance/personality 仅对「人物」类条目填写，其他类型留空字符串。
9. 判断不了归属或信息不足时：宁缺毋滥，不硬造条目；summary 写"待补充"。
   另外 entities_add 中**不要**填写 profile 字段（档案正文由作者本人维护）。"""

    user = (
        f"{prev}以下是新章节文本：\n---\n{chapters_text}\n---\n"
        "请只输出变更补丁 JSON（没有变化就四个数组都留空）。"
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


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
    """关系图谱分析器（更新模式）

    分析小说章节文本，产出「变更补丁」，再增量应用到现有设定库上。
    """

    name = "关系图谱分析"

    def __init__(self, type_: str = "角色", protect: bool = True):
        super().__init__()
        self.type = type_
        # protect=False 时解除手动内容保护（"覆盖手动内容"模式，需用户显式确认）
        self.protect = protect
        # 最近一次合并的变更报告（供进度流播报）
        self.last_report: dict = {}

    def get_role(self) -> str:
        return "NOVEL_ANALYZER"

    def get_response_format(self) -> dict | None:
        """不强制 response_format，部分 API 提供商不兼容"""
        return None

    def build_prompt(self, text: str, previous: Any, ctx: dict) -> list[dict]:
        # blind 模式（全量重析）：不把现有数据交给模型，只让它提交新增
        ctx = ctx or {}
        return master_prompt(self.type, None if ctx.get("blind") else previous, text)

    def parse_result(self, raw: str) -> Any:
        # LLM 返回空内容时，返回空补丁（可能本批无变化）
        if not raw or not raw.strip():
            return validate_patch(self.type, {})
        data = self._parse_json(raw)
        if data is None:
            snippet = raw[:500] if raw else "(空)"
            import logging
            logging.warning(f"{self.name} JSON解析失败，原始内容前500字: {snippet}")
            raise ValueError(f"LLM 返回内容无法解析为 JSON（前200字: {snippet[:200]}）")
        return validate_patch(self.type, data)

    def validate(self, data: Any) -> bool:
        """校验补丁结构：至少含一个已知补丁键"""
        return isinstance(data, dict) and any(k in data for k in PATCH_KEYS)

    def merge(self, previous: Any, new: Any) -> Any:
        """更新模式：把变更补丁增量应用到现有数据上（现有数据为骨架）"""
        data, report = apply_patch(previous, new, protect=self.protect)
        self.last_report = report
        return data

    async def save(self, data: dict, project_dir: Path, ctx: dict) -> None:
        """保存关系脉络主文件 + 条目档案子文件"""
        persist_type_data(project_dir, self.type, data)
