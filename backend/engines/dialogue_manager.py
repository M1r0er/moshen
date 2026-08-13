"""
墨参 · 对话管理器
核心编排模块：意图识别 → 上下文组装 → LLM调用 → 干预评估 → 流式返回
"""
import json
import re
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

# 知识库存写标记的正则：[[KB_SAVE:标题:类型]]内容[[/KB_SAVE]]
_KB_SAVE_PATTERN = re.compile(
    r'\[\[KB_SAVE:([^:\]]+):([^\]]+)\]\](.*?)\[\[/KB_SAVE\]\]',
    re.DOTALL
)


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

            # 知识库存写：检测 KB_SAVE 标记并自动保存
            kb_matches = _KB_SAVE_PATTERN.findall(full_response)
            if kb_matches:
                # 延迟导入避免循环依赖
                from routes.knowledge import save_knowledge_entry

                saved_entries = []
                for match in kb_matches:
                    title = match[0].strip()
                    kb_type = match[1].strip().lower()
                    content = match[2].strip()

                    # 类型映射
                    type_map = {
                        "世界观": "world", "world": "world",
                        "人物": "character", "character": "character",
                        "设定": "setting", "setting": "setting",
                        "剧情": "plot", "plot": "plot",
                        "文风": "style", "style": "style",
                        "参考": "reference", "reference": "reference",
                    }
                    kb_type = type_map.get(kb_type, "other")

                    result = save_knowledge_entry(title, content, kb_type)
                    if result:
                        saved_entries.append({
                            "id": result["id"],
                            "title": result["title"],
                            "type": kb_type,
                        })

                if saved_entries:
                    # 发送保存成功事件
                    yield self._sse("kb_saved", {"entries": saved_entries})

                    # 发送清理后的文本（移除标记块）
                    clean_response = _KB_SAVE_PATTERN.sub('', full_response)
                    # 清理多余的空行
                    clean_response = re.sub(r'\n{3,}', '\n\n', clean_response).strip()
                    yield self._sse("kb_clean", {"clean_content": clean_response})
                    full_response = clean_response

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
