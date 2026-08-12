"""
墨参 · 主动干预引擎
三级行动体系：L1建议 / L2质询 / L3否决
在每次助手回复后评估是否需要追加干预内容
"""
import json
from typing import Optional
from core.llm_provider import get_llm_provider
from core.prompt_loader import get_prompt_loader


class InterventionEngine:
    """主动干预引擎"""

    def __init__(self):
        self.llm = get_llm_provider()
        self.prompt_loader = get_prompt_loader()

    async def evaluate(
        self,
        user_input: str,
        assistant_response: str,
        project_context: str,
    ) -> dict:
        """评估是否需要主动干预

        Args:
            user_input: 用户输入
            assistant_response: 助手已生成的回复
            project_context: 项目知识库摘要

        Returns:
            {
                "need_intervention": bool,
                "level": "L1" | "L2" | "L3" | None,
                "content": str | None
            }
        """
        # 如果回复已经很简短（如问候），跳过干预
        if len(assistant_response) < 50:
            return {"need_intervention": False, "level": None, "content": None}

        # 如果项目上下文为空，跳过干预
        if not project_context or len(project_context) < 20:
            return {"need_intervention": False, "level": None, "content": None}

        # 构建干预评估提示
        eval_prompt = self._build_eval_prompt(
            user_input, assistant_response, project_context
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个干预评估器。你的任务是判断在一轮对话后，"
                    "是否需要追加主动干预内容（建议/质询/否决）。"
                    "只返回 JSON，不要输出其他内容。"
                ),
            },
            {"role": "user", "content": eval_prompt},
        ]

        try:
            result = await self.llm.generate(
                messages, role="STRUCTURE_ANALYST", temperature=0.3, max_tokens=1024
            )
            return self._parse_result(result)
        except Exception:
            return {"need_intervention": False, "level": None, "content": None}

    def _build_eval_prompt(
        self, user_input: str, assistant_response: str, project_context: str
    ) -> str:
        """构建干预评估提示词"""
        # 截取避免过长
        ctx = project_context[:2000] if len(project_context) > 2000 else project_context
        resp = assistant_response[:1500] if len(assistant_response) > 1500 else assistant_response

        return f"""请评估以下对话是否需要追加主动干预。

## 用户输入
{user_input[:500]}

## 助手回复（摘要）
{resp}

## 项目知识库摘要
{ctx}

## 检查项

1. **因果律检查**：用户提出的情节是否有前文因果支撑？如果没有 → L2质询
2. **代价守恒检查**：用户提出的主角增益是否有相称代价？如果没有 → L1建议
3. **角色烙印检查**：用户描述的角色行为是否违背其灵魂烙印？如果违背 → L3否决
4. **信息解密检查**：用户是否在错误时机揭示核心秘密？如果是 → L3否决
5. **节奏检查**：用户描述的连续情节是否缺少缓冲？如果是 → L1建议
6. **叙事边界检查**：用户是否提前剧透了未来情节？如果是 → L2质询

注意：只在确实发现问题时才干预。如果助手回复中已经充分讨论了相关问题，则不需要重复干预。

返回 JSON：
```json
{{
  "need_intervention": false,
  "level": null,
  "content": null
}}
```

或需要干预时：
```json
{{
  "need_intervention": true,
  "level": "L2",
  "content": "具体的干预内容，以编辑的口吻提出"
}}
```"""

    def _parse_result(self, result: str) -> dict:
        """解析 LLM 返回的 JSON 结果"""
        # 尝试提取 JSON
        result = result.strip()
        if result.startswith("```json"):
            result = result[7:]
        if result.startswith("```"):
            result = result[3:]
        if result.endswith("```"):
            result = result[:-3]
        result = result.strip()

        try:
            data = json.loads(result)
            if "need_intervention" not in data:
                return {"need_intervention": False, "level": None, "content": None}
            return data
        except json.JSONDecodeError:
            return {"need_intervention": False, "level": None, "content": None}


# 全局单例
_engine: InterventionEngine | None = None


def get_intervention_engine() -> InterventionEngine:
    global _engine
    if _engine is None:
        _engine = InterventionEngine()
    return _engine
