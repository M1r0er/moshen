"""
墨参 · 对话管理器
核心编排模块：意图识别 → 上下文组装 → LLM调用 → 干预评估 → 流式返回
"""
import json
import os
import re
import time
from typing import AsyncGenerator
from pathlib import Path

from core.llm_provider import get_llm_provider
from core.prompt_loader import get_prompt_loader
from core.context_manager import build_core_layer, create_context
from core.context_builder import Section, assemble_context
from core.config import get_config_manager
from engines.intent_router import get_intent_router
from engines.intervention import get_intervention_engine
from knowledge.project_kb import get_project_kb_manager

# 项目记忆层（注入 system 的项目上下文）的总字符预算，可用环境变量覆盖
CONTEXT_BUDGET_CHARS = int(os.environ.get("MOSHEN_CONTEXT_BUDGET", "14000"))

# 知识库存写标记的正则：[[KB_SAVE:标题:类型]]内容[[/KB_SAVE]]
_KB_SAVE_PATTERN = re.compile(
    r'\[\[KB_SAVE:([^:\]]+):([^\]]+)\]\](.*?)\[\[/KB_SAVE\]\]',
    re.DOTALL
)

# 设定存写标记的正则：[[SETTING_SAVE:标题:类别]]内容[[/SETTING_SAVE]]
# 可选父节点：[[SETTING_SAVE:标题:类别:父标题]]内容[[/SETTING_SAVE]]
_SETTING_SAVE_PATTERN = re.compile(
    r'\[\[SETTING_SAVE:([^:\]]+):([^\]:]+)(?::([^:\]]+))?\]\](.*?)\[\[/SETTING_SAVE\]\]',
    re.DOTALL
)

# 设定更新标记的正则：[[SETTING_UPDATE:标题]]新内容[[/SETTING_UPDATE]]
_SETTING_UPDATE_PATTERN = re.compile(
    r'\[\[SETTING_UPDATE:([^:\]]+)\]\](.*?)\[\[/SETTING_UPDATE\]\]',
    re.DOTALL
)

# 大纲存写标记的正则：[[OUTLINE_SAVE:标题:类型]]内容[[/OUTLINE_SAVE]]
# 可选前置节点和连线说明：[[OUTLINE_SAVE:标题:类型:前置标题:连线说明]]内容[[/OUTLINE_SAVE]]
_OUTLINE_SAVE_PATTERN = re.compile(
    r'\[\[OUTLINE_SAVE:([^:\]]+):([^\]:]+)(?::([^:\]]+))?(?::([^:\]]+))?\]\](.*?)\[\[/OUTLINE_SAVE\]\]',
    re.DOTALL
)

# 大纲更新标记的正则：[[OUTLINE_UPDATE:标题]]新内容[[/OUTLINE_UPDATE]]
_OUTLINE_UPDATE_PATTERN = re.compile(
    r'\[\[OUTLINE_UPDATE:([^:\]]+)\]\](.*?)\[\[/OUTLINE_UPDATE\]\]',
    re.DOTALL
)

# 大纲连线标记的正则：[[OUTLINE_LINK:源标题:目标标题]]连线说明[[/OUTLINE_LINK]]
_OUTLINE_LINK_PATTERN = re.compile(
    r'\[\[OUTLINE_LINK:([^:\]]+):([^:\]]+)\]\](.*?)\[\[/OUTLINE_LINK\]\]',
    re.DOTALL
)


class DialogueManager:
    """对话管理器 - 系统的核心编排中心"""

    def __init__(self):
        self.llm = get_llm_provider()
        self.prompt_loader = get_prompt_loader()
        self.config_mgr = get_config_manager()
        self.intent_router = get_intent_router()
        self.intervention = get_intervention_engine()
        self.project_kb = get_project_kb_manager()
        # 核心层（人格 + 创作规范）是静态内容，构造一次后缓存
        self._core_layer_cache: str | None = None

    def _build_memory_layer(self, project_id: str) -> str:
        """组装某个项目的记忆层文本（知识库/设定/大纲/灵感/全局知识库）

        按"预算 + 优先级"组装，并把被截断 / 未注入的片段显式写入缺口说明，
        而不是静默丢弃。每次调用都按当前磁盘内容重新组装，不写任何共享状态。
        """
        if not project_id:
            return ""

        sections: list[Section] = []

        # 知识库摘要（项目内模板/上传/工作区文档，最稳定、最优先）
        try:
            kb_summary = self.project_kb.get_project_summary(project_id)
            if kb_summary:
                sections.append(Section("知识库", f"### 知识库\n{kb_summary}", priority=10, max_chars=6000))
        except Exception:
            pass

        # 设定树摘要
        try:
            from routes.settings_writer import get_settings_summary
            settings_summary = get_settings_summary(project_id)
            if settings_summary:
                sections.append(Section("设定页", f"### 设定页\n{settings_summary}", priority=20, max_chars=4500))
        except Exception:
            pass

        # 大纲摘要
        try:
            from routes.outline import get_outline_summary
            outline_summary = get_outline_summary(project_id)
            if outline_summary:
                sections.append(Section("大纲", f"### 大纲\n{outline_summary}", priority=30, max_chars=3500))
        except Exception:
            pass

        # 全局知识库条目（AI/用户存入的知识）
        try:
            from routes.knowledge import get_knowledge_summary
            global_kb = get_knowledge_summary()
            if global_kb:
                sections.append(Section("全局知识库", f"### 全局知识库\n{global_kb}", priority=40, max_chars=3500))
        except Exception:
            pass

        # 灵感文件夹文件列表（易变，优先级最低）
        try:
            from routes.workspace import get_inspiration_path
            insp_path = get_inspiration_path()
            if insp_path:
                from pathlib import Path
                insp_dir = Path(insp_path)
                if insp_dir.exists() and insp_dir.is_dir():
                    file_names = []
                    for f in insp_dir.iterdir():
                        if f.is_file() and not f.name.startswith(".") and f.suffix.lower() in (".txt", ".md", ".docx", ".doc", ".markdown", ".csv", ".json"):
                            file_names.append(f.name)
                    if file_names:
                        sections.append(Section(
                            "灵感文件夹",
                            f"### 灵感文件夹（{insp_path}）\n用户在该文件夹中存有以下文件，如需查看内容请告知用户在灵感页打开：\n"
                            + "\n".join(f"- {n}" for n in sorted(file_names)),
                            priority=60,
                            max_chars=600,
                        ))
        except Exception:
            pass

        return assemble_context(sections, total_budget=CONTEXT_BUDGET_CHARS).text

    def _core_layer_text(self) -> str:
        """核心层文本（助手人格 + 创作规范库 + 不可覆盖的系统合同）"""
        if self._core_layer_cache is None:
            try:
                persona = self.prompt_loader.load_raw("system_persona")
            except FileNotFoundError:
                persona = "你是墨参，一位资深网文编辑兼创作教练。"
            text = build_core_layer(persona, self._rules_text())
            contract = self._system_contract()
            if contract:
                # 系统合同始终位于最后并声明不可覆盖，优先级高于角色定位与用户指导
                text = f"{text}\n\n---\n\n{contract}"
            self._core_layer_cache = text
        # 写作语言随设置变化，单独拼接在缓存之外
        return f"{self._core_layer_cache}\n\n---\n\n{self._language_directive()}"

    def _language_directive(self) -> str:
        """写作语言指令（界面语言与写作语言相互独立）"""
        try:
            from routes.workspace import get_writing_language
            lang = get_writing_language()
        except Exception:
            lang = "zh-CN"
        label = {"zh-CN": "简体中文", "en-US": "English"}.get(lang, lang)
        return (
            "## 写作语言\n"
            f"本项目写作语言：{label}。请使用该语言进行创作与回复；"
            "作者原文与引用资料保持原样，不要翻译或改写。"
        )

    def _system_contract(self) -> str:
        """加载不可被覆盖的系统合同（缺失时降级为空）"""
        try:
            return self.prompt_loader.load_raw("system_contract")
        except FileNotFoundError:
            return ""

    @staticmethod
    def _rules_text() -> str:
        """获取创作规范库文本（失败时降级为空，不阻断对话）"""
        try:
            from knowledge.rules_kb import RulesKB
            return RulesKB.get_all_rules()
        except Exception:
            return ""

    async def chat_stream(
        self,
        user_input: str,
        history: list[dict] | None = None,
        project_id: str | None = None,
        model_override: str | None = None,
        role_override: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        """流式对话

        Args:
            user_input: 用户输入
            history: 对话历史 [{role, content}, ...]
            project_id: 项目ID
            model_override: 指定模型名称。None/"auto" 为自动选择
            role_override: 指定职能角色。None/"auto" 为自动选择（通过意图识别）

        Yields:
            SSE 事件字典 {"event": ..., "data": ...}（由 EventSourceResponse 序列化）
        """
        # 核心层（助手人格 + 创作规范库）
        core_layer = self._core_layer_text()

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

        # 按本次请求构建独立上下文，不写入任何跨请求共享的状态，
        # 这样并发请求 / 多项目切换不会互相覆盖上下文。
        memory_layer = self._build_memory_layer(project_id) if project_id else ""
        focus = f"用户正在讨论：{user_input[:200]}"
        ctx = create_context(
            core_layer=core_layer,
            memory_layer=memory_layer,
            working_layer=focus,
        )
        messages = ctx.build_messages_with_history(history or [], user_input)

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

            # 干预评估（复用本次请求已组装的记忆层，保证信息口径一致）
            intervention = await self._evaluate_intervention(
                user_input, full_response, project_id, memory_layer
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

            # 设定存写：检测 SETTING_SAVE 标记并自动保存到设定页
            setting_matches = _SETTING_SAVE_PATTERN.findall(full_response)
            if setting_matches:
                from routes.settings_writer import save_setting_entry

                pid = project_id
                if pid:
                    saved_settings = []
                    blocked_settings = []
                    for match in setting_matches:
                        s_title = match[0].strip()
                        s_category = match[1].strip()
                        s_parent = match[2].strip() if match[2] else None
                        s_content = match[3].strip()

                        result = save_setting_entry(
                            project_id=pid,
                            title=s_title,
                            content=s_content,
                            category=s_category,
                            parent_title=s_parent,
                        )
                        if result and result.get("blocked"):
                            blocked_settings.append(result.get("title", s_title))
                        elif result:
                            saved_settings.append(result)

                    if saved_settings:
                        yield self._sse("setting_saved", {"entries": saved_settings})

                    if blocked_settings:
                        # 作者事实保护：AI 试图覆盖作者维护的设定，已被拦截
                        yield self._sse("setting_conflict", {"titles": blocked_settings})

                    if saved_settings or blocked_settings:
                        # 清理标记块
                        clean_response = _SETTING_SAVE_PATTERN.sub('', full_response)
                        clean_response = re.sub(r'\n{3,}', '\n\n', clean_response).strip()
                        yield self._sse("setting_clean", {"clean_content": clean_response})
                        full_response = clean_response

            # 设定更新：检测 SETTING_UPDATE 标记并修改已有设定
            update_matches = _SETTING_UPDATE_PATTERN.findall(full_response)
            if update_matches:
                from routes.settings_writer import update_setting_entry

                pid = project_id
                if pid:
                    updated_settings = []
                    failed_updates = []
                    blocked_settings = []
                    for match in update_matches:
                        u_title = match[0].strip()
                        u_content = match[1].strip()

                        result = update_setting_entry(
                            project_id=pid,
                            title=u_title,
                            content=u_content,
                        )
                        if result and result.get("blocked"):
                            blocked_settings.append(result.get("title", u_title))
                        elif result:
                            updated_settings.append(result)
                        else:
                            failed_updates.append(u_title)

                    if updated_settings:
                        yield self._sse("setting_updated", {"entries": updated_settings})

                    if blocked_settings:
                        yield self._sse("setting_conflict", {"titles": blocked_settings})

                    if failed_updates:
                        yield self._sse("setting_update_failed", {"titles": failed_updates})

                    if updated_settings or failed_updates or blocked_settings:
                        # 清理标记块
                        clean_response = _SETTING_UPDATE_PATTERN.sub('', full_response)
                        clean_response = re.sub(r'\n{3,}', '\n\n', clean_response).strip()
                        yield self._sse("setting_clean", {"clean_content": clean_response})
                        full_response = clean_response

            # 大纲存写：检测 OUTLINE_SAVE 标记并自动保存到大纲页
            outline_matches = _OUTLINE_SAVE_PATTERN.findall(full_response)
            if outline_matches:
                from routes.outline import save_outline_node

                pid = project_id
                if pid:
                    saved_outlines = []
                    blocked_outlines = []
                    for match in outline_matches:
                        o_title = match[0].strip()
                        o_type = match[1].strip()
                        o_after = match[2].strip() if match[2] else None
                        o_edge_label = match[3].strip() if match[3] else ""
                        o_content = match[4].strip()

                        node_type = "branch" if o_type in ("branch", "支线", "支") else "main"

                        result = save_outline_node(
                            project_id=pid,
                            title=o_title,
                            content=o_content,
                            node_type=node_type,
                            after_title=o_after,
                            edge_label=o_edge_label,
                        )
                        if result and result.get("blocked"):
                            blocked_outlines.append(result.get("title", o_title))
                        elif result:
                            saved_outlines.append(result)

                    if saved_outlines:
                        yield self._sse("outline_saved", {"entries": saved_outlines})

                    if blocked_outlines:
                        # 作者事实保护：AI 试图覆盖作者维护的大纲节点，已被拦截
                        yield self._sse("outline_conflict", {"titles": blocked_outlines})

                    if saved_outlines or blocked_outlines:
                        clean_response = _OUTLINE_SAVE_PATTERN.sub('', full_response)
                        clean_response = re.sub(r'\n{3,}', '\n\n', clean_response).strip()
                        yield self._sse("outline_clean", {"clean_content": clean_response})
                        full_response = clean_response

            # 大纲更新：检测 OUTLINE_UPDATE 标记并修改已有节点
            outline_update_matches = _OUTLINE_UPDATE_PATTERN.findall(full_response)
            if outline_update_matches:
                from routes.outline import update_outline_node

                pid = project_id
                if pid:
                    updated_outlines = []
                    failed_outline_updates = []
                    blocked_outlines = []
                    for match in outline_update_matches:
                        ou_title = match[0].strip()
                        ou_content = match[1].strip()

                        result = update_outline_node(
                            project_id=pid,
                            title=ou_title,
                            content=ou_content,
                        )
                        if result and result.get("blocked"):
                            blocked_outlines.append(result.get("title", ou_title))
                        elif result:
                            updated_outlines.append(result)
                        else:
                            failed_outline_updates.append(ou_title)

                    if updated_outlines:
                        yield self._sse("outline_updated", {"entries": updated_outlines})

                    if blocked_outlines:
                        yield self._sse("outline_conflict", {"titles": blocked_outlines})

                    if failed_outline_updates:
                        yield self._sse("outline_update_failed", {"titles": failed_outline_updates})

                    if updated_outlines or failed_outline_updates or blocked_outlines:
                        clean_response = _OUTLINE_UPDATE_PATTERN.sub('', full_response)
                        clean_response = re.sub(r'\n{3,}', '\n\n', clean_response).strip()
                        yield self._sse("outline_clean", {"clean_content": clean_response})
                        full_response = clean_response

            # 大纲连线：检测 OUTLINE_LINK 标记并创建/更新连线
            outline_link_matches = _OUTLINE_LINK_PATTERN.findall(full_response)
            if outline_link_matches:
                from routes.outline import save_outline_edge

                pid = project_id
                if pid:
                    linked_outlines = []
                    failed_links = []
                    for match in outline_link_matches:
                        ol_from = match[0].strip()
                        ol_to = match[1].strip()
                        ol_label = match[2].strip()

                        result = save_outline_edge(
                            project_id=pid,
                            from_title=ol_from,
                            to_title=ol_to,
                            label=ol_label,
                        )
                        if result:
                            linked_outlines.append(result)
                        else:
                            failed_links.append(f"{ol_from} → {ol_to}")

                    if linked_outlines:
                        yield self._sse("outline_linked", {"entries": linked_outlines})

                    if failed_links:
                        yield self._sse("outline_link_failed", {"pairs": failed_links})

                    if linked_outlines or failed_links:
                        clean_response = _OUTLINE_LINK_PATTERN.sub('', full_response)
                        clean_response = re.sub(r'\n{3,}', '\n\n', clean_response).strip()
                        yield self._sse("outline_clean", {"clean_content": clean_response})
                        full_response = clean_response

        except Exception as e:
            yield self._sse("error", {"message": str(e)})

        # 完成
        yield self._sse("done", {
            "full_response": full_response,
            "intent": intent_result.intent if intent_result else "manual",
            "model_role": model_role,
            "model": actual_cfg.model if actual_cfg else "",
            # 结束原因：length 表示被最大长度截断，前端据此提示用户
            "finish_reason": getattr(self.llm, "last_finish_reason", None),
        })

    async def _evaluate_intervention(
        self,
        user_input: str,
        assistant_response: str,
        project_id: str | None,
        project_context: str = "",
    ) -> dict | None:
        """评估是否需要主动干预

        直接复用本次请求已组装的记忆层（project_context），保证干预评估
        看到的信息与写作模型一致，且不依赖任何跨请求共享状态。
        """
        if not project_id:
            return None

        if not project_context:
            project_context = self.project_kb.get_project_summary(project_id)
        if not project_context:
            return None

        try:
            return await self.intervention.evaluate(
                user_input=user_input,
                assistant_response=assistant_response,
                project_context=project_context,
            )
        except Exception:
            return None

    def _sse(self, event: str, data: dict) -> dict:
        """构造 SSE 事件（由 EventSourceResponse 统一序列化）"""
        return {"event": event, "data": json.dumps(data, ensure_ascii=False)}


# 全局单例
_manager: DialogueManager | None = None


def get_dialogue_manager() -> DialogueManager:
    global _manager
    if _manager is None:
        _manager = DialogueManager()
    return _manager
