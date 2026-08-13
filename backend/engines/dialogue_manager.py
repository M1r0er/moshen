"""
墨参 · 对话管理器
核心编排模块：意图识别 → 上下文组装 → LLM调用 → 干预评估 → 流式返回
"""
import json
import time
from typing import AsyncGenerator
from pathlib import Path

from core.llm_provider import get_llm_provider
from core.prompt_loader import get_prompt_loader
from core.context_manager import get_context_manager
from core.config import get_config_manager
from engines.intent_router import get_intent_router
from engines.intervention import get_intervention_engine
from knowledge.project_kb import get_project_kb_manager


class DialogueManager:
    """对话管理器 - 系统的核心编排中心"""

    def __init__(self):
        self.llm = get_llm_provider()
        self.prompt_loader = get_prompt_loader()
        self.context_mgr = get_context_manager()
        self.config_mgr = get_config_manager()
        self.intent_router = get_intent_router()
        self.intervention = get_intervention_engine()
        self.project_kb = get_project_kb_manager()
        self._current_project_id: str | None = None

    def set_project(self, project_id: str | None):
        """设置当前活跃项目"""
        self._current_project_id = project_id
        if project_id:
            self._refresh_project_context()

    def _refresh_project_context(self):
        """刷新项目知识库上下文"""
        if not self._current_project_id:
            self.context_mgr.set_memory_layer("")
            return

        summary = self.project_kb.get_project_summary(self._current_project_id)
        self.context_mgr.set_memory_layer(summary)

    def _init_core_layer(self):
        """初始化核心层（助手人格）"""
        try:
            persona = self.prompt_loader.load_raw("system_persona")
            self.context_mgr.set_core_layer(persona)
        except FileNotFoundError:
            self.context_mgr.set_core_layer("你是墨参，一位资深网文编辑兼创作教练。")

    async def chat_stream(
        self,
        user_input: str,
        history: list[dict] | None = None,
        project_id: str | None = None,
        model_override: str | None = None,
        role_override: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """流式对话

        Args:
            user_input: 用户输入
            history: 对话历史 [{role, content}, ...]
            project_id: 项目ID
            model_override: 指定模型名称。None/"auto" 为自动选择
            role_override: 指定职能角色。None/"auto" 为自动选择（通过意图识别）

        Yields:
            SSE 格式的数据块
        """
        # 确保核心层已初始化
        self._init_core_layer()

        # 设置项目上下文
        if project_id and project_id != self._current_project_id:
            self.set_project(project_id)
        elif project_id and not self._current_project_id:
            self.set_project(project_id)

        # 确定使用哪个职能角色
        if role_override and role_override != "auto":
            # 用户手动指定了职能
            model_role = role_override
            intent_result = None
            intent_desc = f"手动指定: {model_role}"
        else:
            # auto 模式：通过意图识别自动选择
            intent = self.intent_router.detect(user_input)
            model_role = intent.model_role
            intent_result = intent
            intent_desc = self.intent_router.get_intent_description(intent.intent)

        # 发送意图信息
        yield self._sse("intent", {
            "intent": intent_result.intent if intent_result else "manual",
            "description": intent_desc,
            "model_role": model_role,
            "confidence": intent_result.confidence if intent_result else 1.0,
        })

        # 发送实际使用的模型信息
        actual_cfg = self.config_mgr.get_model(
            model_role, model_name=model_override,
            user_input=user_input,
            intent=intent_result.intent if intent_result else "",
        )
        if actual_cfg:
            yield self._sse("model_info", {
                "role": model_role,
                "model": actual_cfg.model,
                "auto_selected": (not model_override or model_override == "auto"),
            })

        # 设置工作层：当前讨论焦点
        focus = f"用户正在讨论：{user_input[:200]}"
        self.context_mgr.set_working_layer(focus)

        # 组装消息
        messages = self.context_mgr.build_messages_with_history(
            history or [], user_input
        )

        # 流式生成
        full_response = ""
        try:
            async for chunk in self.llm.generate_stream(
                messages, role=model_role,
                model_override=model_override,
                user_input=user_input,
                intent=intent_result.intent if intent_result else "",
            ):
                full_response += chunk
                yield self._sse("chunk", {"content": chunk})

            # 干预评估
            intervention = await self._evaluate_intervention(
                user_input, full_response, project_id
            )

            if intervention and intervention.get("need_intervention"):
                yield self._sse("intervention", intervention)

        except Exception as e:
            yield self._sse("error", {"message": str(e)})

        # 完成
        yield self._sse("done", {
            "full_response": full_response,
            "intent": intent_result.intent if intent_result else "manual",
            "model_role": model_role,
            "model": actual_cfg.model if actual_cfg else "",
        })

    async def _evaluate_intervention(
        self,
        user_input: str,
        assistant_response: str,
        project_id: str | None,
    ) -> dict | None:
        """评估是否需要主动干预"""
        if not project_id:
            return None

        # 获取项目知识库摘要用于干预评估
        kb_summary = self.project_kb.get_project_summary(project_id)
        if not kb_summary:
            return None

        try:
            return await self.intervention.evaluate(
                user_input=user_input,
                assistant_response=assistant_response,
                project_context=kb_summary,
            )
        except Exception:
            return None

    def _sse(self, event: str, data: dict) -> str:
        """格式化 SSE 数据"""
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# 全局单例
_manager: DialogueManager | None = None


def get_dialogue_manager() -> DialogueManager:
    global _manager
    if _manager is None:
        _manager = DialogueManager()
    return _manager
