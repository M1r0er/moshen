"""
墨参 · 关系图谱 Markdown 渲染器
将关系图谱数据渲染为可读的 Markdown（主脉络 + 条目档案）。
移植自星图 server/render.js
"""
import re

# 各分类的详情区块标题
SECTION_TITLES = {
    "角色": {"intro": "身份简介", "story": "故事脉络"},
    "世界": {"intro": "设定概述", "story": "设定要点"},
    "势力": {"intro": "势力概述", "story": "组织信息"},
    "地点": {"intro": "地点概述", "story": "详情信息"},
    "事件": {"intro": "事件概述", "story": "经过与影响"},
    "道具": {"intro": "道具概述", "story": "详情信息"},
}


def to_file_name(name: str) -> str:
    """将条目名转为安全的文件名"""
    return re.sub(r'[\\/:*?"<>|]', "_", str(name))


def _display_width(s: str) -> int:
    """计算显示宽度（中文按 2 计）"""
    width = 0
    for ch in str(s):
        if "\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f" or "\uff00" <= ch <= "\uffef" or ch in "\u2014\u2026":
            width += 2
        else:
            width += 1
    return width


def pad_display(s: str, width: int = 14) -> str:
    """按显示宽度补齐"""
    return str(s) + " " * max(0, width - _display_width(s))


def render_master_markdown(data: dict) -> str:
    """渲染关系脉络主文件 Markdown

    data 结构: {"type": "...", "generatedAt": "...", "entities": [...], "relations": [...], "timeline": [...]}
    """
    lines = []
    lines.append(f"# {data.get('type', '')}关系脉络")
    lines.append(f"> 生成时间：{data.get('generatedAt', '')}")
    lines.append("")
    lines.append("## 条目一览")
    for e in data.get("entities", []):
        alias = f"（别名：{'、'.join(e.get('aliases', []))}）" if e.get("aliases") else ""
        cat = f"【{e['category']}】" if e.get("category") else ""
        lines.append(f"- {pad_display(e.get('id', ''))}{cat}：{e.get('summary', '')}{alias}")
    lines.append("")
    lines.append("## 关系清单")
    for r in data.get("relations", []):
        detail = f"：{r['detail']}" if r.get("detail") else ""
        lines.append(f"- {r.get('from', '')} —({r.get('type', '')})→ {r.get('to', '')}{detail}")
    lines.append("")
    lines.append("## 时间线")
    for t in data.get("timeline", []):
        lines.append(f"- {t.get('time', '')}：{t.get('event', '')}")
    return "\n".join(lines) + "\n"


def render_entity_markdown(type_: str, entity: dict, relations: list[dict], timeline: list[dict]) -> str:
    """渲染单个条目档案 Markdown"""
    titles = SECTION_TITLES.get(type_, SECTION_TITLES["角色"])
    lines = []
    lines.append(f"# {entity.get('id', '')}")
    if entity.get("category"):
        lines.append(f"> 定位：{entity['category']}")
    if entity.get("locked") or entity.get("source") == "manual":
        lines.append("> 来源：手动条目（AI 不覆盖）")
    elif entity.get("edited_fields"):
        lines.append("> 来源：含手动编辑内容（AI 不覆盖）")
    lines.append("")
    lines.append(f"## {titles['intro']}")
    lines.append(entity.get("summary") or "（暂无简介）")

    # 基础信息（人物专属）
    if type_ == "角色":
        resume = [
            entity.get("gender") and f"性别：{entity['gender']}",
            entity.get("age") and f"年龄：{entity['age']}",
            entity.get("identity") and f"身份：{entity['identity']}",
            entity.get("appearance") and f"外貌：{entity['appearance']}",
            entity.get("personality") and f"性格：{entity['personality']}",
            entity.get("aliases") and f"曾用名：{'、'.join(entity['aliases'])}",
        ]
        resume = [r for r in resume if r]
        if resume:
            lines.append("")
            lines.append("## 基础信息")
            for r in resume:
                lines.append(f"- {r}")

    lines.append("")
    lines.append(f"## {titles['story']}")
    tls = [t for t in (timeline or []) if entity.get("id") in (t.get("refs") or [])]
    story_lines = []
    body = entity.get("profile") or entity.get("story")
    if body:
        story_lines.extend(str(body).split("\n"))
    for t in tls:
        story_lines.append(f"- {t.get('time', '')}：{t.get('event', '')}")
    if story_lines:
        for s in story_lines:
            lines.append(s)
    else:
        lines.append("（暂无按时间记录的事件）")

    lines.append("")
    lines.append("## 与其他条目的联系")
    rels = [r for r in (relations or []) if r.get("from") == entity.get("id") or r.get("to") == entity.get("id")]
    if rels:
        for r in rels:
            other = r["to"] if r["from"] == entity.get("id") else r["from"]
            detail = f"——{r['detail']}" if r.get("detail") else ""
            lines.append(f"- {other}：{r.get('type', '')}{detail}")
    else:
        lines.append("（暂无已知联系）")

    return "\n".join(lines) + "\n"
