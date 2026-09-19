"""墨参 · 角色阶段解析

把角色的成长轨迹拆成「阶段节点 + 事件连线」，供角色页的阶段图使用。

两点设计取舍：
1. **只喂相关章节**：先按角色名/别名筛出提到过该角色的章节，再送模型，避免把整本书灌进去。
2. **档位不规定阶段数量**：不同体量、不同变化节奏的小说，合理阶段数差异极大（可能 3 个，
   也可能十几个），所以档位只描述"切分粒度"，由模型按正文自行决定切几刀。
"""
import json
import re
import uuid

# 阶段图自动排布：横向流淌，奇偶行错开，避免节点连成一串看不出差别
_STAGE_DX = 240
_STAGE_DY = 120
_STAGE_GAP_Y = 110


def collect_character_text(kb, project_id: str, char: dict, limit: int = 24000) -> str:
    """收集提到该角色的章节正文（按顺序拼接，超长时保留首尾）"""
    terms = [char.get("name", "")] + list(char.get("aliases") or [])
    terms = [t.strip() for t in terms if t and t.strip()]
    if not terms:
        return ""

    chunks: list[str] = []
    try:
        chapters = kb.get_all_chapters_text(project_id)
    except Exception:
        chapters = []

    for c in chapters:
        text = (c.get("content") or "").strip()
        if not text:
            continue
        if not any(t in text for t in terms):
            continue
        head = f"### 第{c.get('ch_number')}章 {c.get('ch_title') or ''}"
        chunks.append(f"{head}\n{text}")

    if not chunks:
        return ""

    joined = "\n\n".join(chunks)
    if len(joined) <= limit:
        return joined
    head_len = int(limit * 0.6)
    tail_len = limit - head_len
    return joined[:head_len] + "\n\n…（中间章节已省略）…\n\n" + joined[-tail_len:]


def build_stage_messages(char: dict, text: str, level: str) -> list[dict]:
    level = level if level in ("coarse", "standard", "fine") else "standard"
    level_hint = {
        "coarse": "只保留改变角色命运 / 身份 / 阵营的重大转折；同一大阶段里的能力与关系变化合并进去。",
        "standard": "按台阶式的成长阶段切分：能力、地位、关系出现明显变化时就切一刀。",
        "fine": "尽量细分：每次关键能力的获得、每次重要关系或立场的变化，都单独成一个阶段。",
    }[level]

    aliases = "、".join(char.get("aliases") or []) or "（无）"
    system = (
        "你是资深网文编辑，负责把角色的成长轨迹拆成若干「阶段」，并给出阶段之间的关键事件。\n"
        "你只输出一个合法 JSON 对象，不输出任何解释、前后缀或 Markdown 围栏。"
    )
    user = (
        f"角色：{char.get('name', '')}\n"
        f"别名：{aliases}\n"
        f"已有档案：{char.get('profile') or '（无）'}\n\n"
        f"切分档位：{level_hint}\n"
        "注意：档位只影响切分**粒度**，不规定阶段数量。阶段数由正文实际内容决定，"
        "可能是 3 个，也可能十几个；不要为了凑数把同一阶段拆成两段，也不要为了省事把两个阶段压成一个。\n\n"
        "硬性要求：\n"
        "1. 阶段顺序必须与正文时间线一致（从早到晚）。\n"
        "2. 阶段标题要短（≤14 字），建议用「身份/境界 + 特征」的形式，例如「轮海叶凡」「北境军主帅」。\n"
        "3. note 用 1-2 句写清该阶段的关键状态（能力、处境、关系、目标）。\n"
        "4. events 描述「从一个阶段到下一个阶段经历了什么」，label 必须具体到事件"
        "（如「得青莲地心火，破入道宫」），禁止写「成长了」「变强了」这类空话。\n"
        "5. 只依据给定正文，不得编造正文未支持的内容。\n"
        "6. 章节回填到 chapter 字段（如「第37章 初遇」），不确定就留空字符串。\n\n"
        "输出结构：\n"
        '{"stages":[{"title":"","note":"","chapter":""}],'
        '"events":[{"from_index":0,"to_index":1,"label":""}]}\n'
        "（from_index / to_index 是 stages 数组的下标，只能连接相邻阶段。）\n\n"
        "以下是该角色相关的正文：\n---\n"
        f"{text}\n---"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _extract_json(raw: str) -> dict:
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


def layout_stages(stages: list[dict]) -> list[dict]:
    """给阶段节点排一个从左到右、上下错开的初始位置（之后可手动拖拽）"""
    for i, s in enumerate(stages):
        if not s.get("x") and not s.get("y"):
            s["x"] = 40 + i * _STAGE_DX
            s["y"] = _STAGE_GAP_Y + (i % 2) * _STAGE_DY
    return stages


def parse_stage_result(raw: str, char: dict) -> dict:
    """解析模型输出 → {stages:[...], events:[...]}（阶段 id 在此处生成）"""
    data = _extract_json(raw)
    raw_stages = data.get("stages")
    if not isinstance(raw_stages, list):
        return {"stages": [], "events": []}

    stages: list[dict] = []
    for s in raw_stages:
        if not isinstance(s, dict):
            continue
        title = str(s.get("title", "")).strip()
        if not title:
            continue
        stages.append({
            "title": title[:24],
            "note": str(s.get("note", "") or "").strip()[:600],
            "chapter": str(s.get("chapter", "") or "").strip()[:60],
        })

    if not stages:
        return {"stages": [], "events": []}

    # id 由后端生成，模型只给下标（避免模型编造 id 造成连线错乱）
    ids = [f"stage_{uuid.uuid4().hex[:8]}" for _ in range(len(stages))]
    for s, sid in zip(stages, ids):
        s["id"] = sid

    events: list[dict] = []
    for e in (data.get("events") or []):
        if not isinstance(e, dict):
            continue
        try:
            a, b = int(e.get("from_index")), int(e.get("to_index"))
        except (TypeError, ValueError):
            continue
        if a < 0 or b < 0 or a >= len(stages) or b >= len(stages) or a == b:
            continue
        label = str(e.get("label", "") or "").strip()
        if not label:
            continue
        events.append({"from": ids[a], "to": ids[b], "label": label[:120]})

    return {"stages": layout_stages(stages), "events": events}
