"""
墨参 · 四层上下文管理器
核心层（助手人格）+ 记忆层（项目知识库）+ 工作层（当前焦点）+ 历史层（对话历史）
"""
from typing import Optional


class ContextManager:
    """四层上下文组装器"""

    def __init__(self):
        self._core_layer: str = ""        # 助手人格 + 创作规范
        self._memory_layer: str = ""      # 项目知识库摘要
        self._working_layer: str = ""     # 当前讨论焦点 + 检索结果
        self._history: list[dict] = []    # 对话历史

    def set_core_layer(self, persona: str, rules: str = ""):
        """设置核心层：助手人格设定 + 创作规范"""
        parts = [persona]
        if rules:
            parts.append(f"\n---\n\n## 创作规范参考\n{rules}")
        self._core_layer = "\n\n".join(parts)

    def set_memory_layer(self, project_summary: str):
        """设置记忆层：项目知识库摘要"""
        self._memory_layer = project_summary

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


# 全局单例
_context_manager: ContextManager | None = None


def get_context_manager() -> ContextManager:
    global _context_manager
    if _context_manager is None:
        _context_manager = ContextManager()
    return _context_manager
