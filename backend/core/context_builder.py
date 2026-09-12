"""
墨参 · 上下文集装器

把来自不同来源的上下文片段（知识库、设定、大纲、全局知识库、灵感文件……）
按"预算 + 优先级"组装成一段文本，并把被截断 / 未注入的部分**显式列出**。

设计要点：
- 必需区（required）始终注入；可选区按优先级依次注入，预算耗尽即停止。
- 任何被截断或省略的内容都会写入"缺口"说明，而不是静默丢弃——
  让模型与用户都知道"这次没有看到什么"。
- 片段顺序按"稳定 → 易变"排列（优先级即顺序），便于上层把稳定内容
  放在提示词前部、提升 LLM 上下文缓存命中率。
"""
from dataclasses import dataclass, field


@dataclass
class Section:
    """一个上下文片段"""
    name: str
    text: str
    priority: int = 100          # 数值越小越靠前、越优先注入
    max_chars: int | None = None # 该片段的最大字符数（超出则截断）
    required: bool = False       # 必需区：无论如何都注入


@dataclass
class AssemblyResult:
    text: str
    included: list[str] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)
    omitted: list[str] = field(default_factory=list)

    @property
    def has_gaps(self) -> bool:
        return bool(self.truncated or self.omitted)


def _truncate(text: str, max_chars: int | None) -> tuple[str, bool]:
    if max_chars is None or len(text) <= max_chars:
        return text, False
    return text[:max_chars].rstrip() + "\n...(已按预算截断)", True


def assemble_context(sections: list[Section], total_budget: int = 14000) -> AssemblyResult:
    """按预算与优先级组装上下文片段

    Args:
        sections: 片段列表
        total_budget: 总字符预算

    Returns:
        AssemblyResult：组装后的文本 + 被截断/未注入的片段名（用于缺口说明）
    """
    result = AssemblyResult(text="")
    ordered = sorted([s for s in sections if s.text and s.text.strip()], key=lambda s: s.priority)

    parts: list[str] = []
    used = 0

    # 先处理必需区（始终注入）
    for section in ordered:
        if not section.required:
            continue
        body, was_truncated = _truncate(section.text, section.max_chars)
        parts.append(body)
        used += len(body)
        result.included.append(section.name)
        if was_truncated:
            result.truncated.append(section.name)

    # 再按优先级注入可选区
    for section in ordered:
        if section.required:
            continue
        remaining = total_budget - used
        if remaining <= 0:
            result.omitted.append(section.name)
            continue
        budget_for_section = section.max_chars if section.max_chars is not None else remaining
        budget_for_section = min(budget_for_section, remaining)
        body, was_truncated = _truncate(section.text, budget_for_section)
        if not body.strip():
            result.omitted.append(section.name)
            continue
        parts.append(body)
        used += len(body)
        result.included.append(section.name)
        if was_truncated:
            result.truncated.append(section.name)

    result.text = "\n\n".join(parts)

    # 缺口显式化
    gap_lines = []
    if result.truncated:
        gap_lines.append(f"{'、'.join(result.truncated)}（已截断）")
    if result.omitted:
        gap_lines.append(f"{'、'.join(result.omitted)}（未注入）")
    if gap_lines:
        result.text += (
            "\n\n> 提示：本次上下文受长度预算限制，以下内容未能完整提供："
            + "；".join(gap_lines)
            + "。如需查看，请显式说明或缩小讨论范围。"
        )

    return result
