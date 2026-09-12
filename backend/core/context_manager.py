"""
墨参 · 四层上下文管理器
核心层（助手人格）+ 记忆层（项目知识库）+ 工作层（当前焦点）+ 历史层（对话历史）

注意：本管理器承载的是"单次请求"的上下文。请求级上下文必须按请求创建
（见 create_context），不要跨请求复用一个实例，否则并发/多项目会互相串台。
"""
from typing import Optional


class ContextManager:
    """四层上下文组装器（请求级，不应跨请求共享可变状态）"""

    def __init__(self, core_layer: str = "", memory_layer: str = "", working_layer: str = ""):
        self._core_layer: str = core_layer      # 助手人格 + 创作规范
        self._memory_layer: str = memory_layer  # 项目知识库摘要
        self._working_layer: str = working_layer  # 当前讨论焦点 + 检索结果
        self._history: list[dict] = []          # 对话历史

    def set_core_layer(self, persona: str, rules: str = ""):
        """设置核心层：助手人格设定 + 创作规范"""
        self._core_layer = build_core_layer(persona, rules)

    def set_memory_layer(self, project_summary: str):
        """设置记忆层：项目知识库摘要"""
        self._memory_layer = project_summary

    def get_memory_layer(self) -> str:
        """获取记忆层文本（供干预评估等复用同一份项目上下文）"""
        return self._memory_layer

    def get_core_layer(self) -> str:
        """获取核心层文本"""
        return self._core_layer

    def set_working_layer(self, focus: str, retrieved_knowledge: str = ""):
        """设置工作层：当前讨论焦点 + 检索到的网文知识"""
        parts = []
        if focus:
            parts.append(f"## 当前讨论焦点\n{focus}")
        if retrieved_knowledge:
            parts.append(f"## 检索到的网文知识\n{retrieved_knowledge}")
        self._working_layer = "\n\n".join(parts)

    def add_message(self, role: str, content: str):
        """添加对话历史"""
        self._history.append({"role": role, "content": content})
        # 保留最近 20 轮对话
        if len(self._history) > 40:
            self._history = self._history[-40:]

    def clear_history(self):
        self._history.clear()

    def build_messages(self, user_input: str) -> list[dict]:
        """组装完整的 LLM 消息列表"""
        messages = []

        # 核心层 → system 消息
        system_parts = []
        if self._core_layer:
            system_parts.append(self._core_layer)
        if self._memory_layer:
            system_parts.append(f"\n---\n\n## 当前项目知识\n{self._memory_layer}")
        if self._working_layer:
            system_parts.append(f"\n---\n\n## 本次对话上下文\n{self._working_layer}")

        if system_parts:
            messages.append({"role": "system", "content": "\n\n".join(system_parts)})

        # 历史层 → 历史消息
        messages.extend(self._history)

        # 用户输入
        messages.append({"role": "user", "content": user_input})

        return messages

    def build_messages_with_history(self, history: list[dict], user_input: str) -> list[dict]:
        """使用外部历史记录组装消息（用于 API 无状态调用）"""
        messages = []

        system_parts = []
        if self._core_layer:
            system_parts.append(self._core_layer)
        if self._memory_layer:
            system_parts.append(f"\n---\n\n## 当前项目知识\n{self._memory_layer}")
        if self._working_layer:
            system_parts.append(f"\n---\n\n## 本次对话上下文\n{self._working_layer}")

        if system_parts:
            messages.append({"role": "system", "content": "\n\n".join(system_parts)})

        messages.extend(history)
        messages.append({"role": "user", "content": user_input})

        return messages


def build_core_layer(persona: str, rules: str = "") -> str:
    """把助手人格与创作规范拼成核心层文本（纯函数，便于按需缓存）"""
    parts = [persona]
    if rules:
        parts.append(f"\n---\n\n## 创作规范参考\n{rules}")
    return "\n\n".join(parts)


def create_context(
    core_layer: str = "",
    memory_layer: str = "",
    working_layer: str = "",
) -> ContextManager:
    """为单次请求创建一个独立的上下文实例

    绝不要跨请求复用同一实例：记忆层/工作层都是随请求变化的可变状态，
    共享会导致并发或多项目场景下上下文互相覆盖。
    """
    return ContextManager(core_layer, memory_layer, working_layer)
