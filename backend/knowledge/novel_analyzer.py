"""
墨参 · 拆书引擎（可选模块）
对参考小说进行结构化拆解，提取叙事模式
这是可选功能：不使用拆书，系统其他功能完全正常运行
"""
import json
import time
from typing import Optional
from core.llm_provider import get_llm_provider
from core.prompt_loader import get_prompt_loader


class NovelAnalyzer:
    """拆书引擎 - 可选模块

    用途：
    - 学习某个作者的节奏把控、行文风格、剧情设计
    - 从参考小说中提取可迁移的叙事模式
    - 构建个人化的网文知识库

    注意：本模块为可选功能，系统核心功能不依赖此模块。
    """

    def __init__(self):
        self.llm = get_llm_provider()
        self.prompt_loader = get_prompt_loader()

    async def analyze_chapters(
        self,
        chapters: list[dict],
        project_id: str = "",
        max_concurrent: int = 3,
    ) -> list[dict]:
        """对章节列表执行单章事实卡提取

        Args:
            chapters: [{"title": "第1章 xxx", "content": "..."}]
            project_id: 项目ID（用于存储结果）
            max_concurrent: 最大并发数

        Returns:
            [{"chapter": 1, "title": "...", "summary": "...", "rhythm": "..."}]
        """
        results = []
        total = len(chapters)

        for i, chapter in enumerate(chapters):
            title = chapter.get("title", f"第{i+1}章")
            content = chapter.get("content", "")

            # 截取内容避免过长
            if len(content) > 6000:
                content = content[:6000]

            try:
                card = await self._extract_chapter_card(i + 1, title, content)
                results.append(card)
            except Exception as e:
                results.append({
                    "chapter": i + 1,
                    "title": title,
                    "error": str(e),
                })

            # 每章之间稍微停顿，避免 API 限流
            if i < total - 1:
                await self._async_sleep(0.5)

        return results

    async def _extract_chapter_card(
        self, chapter_num: int, title: str, content: str
    ) -> dict:
        """提取单章事实卡"""
        prompt = f"""你是一个专业的网文结构分析师。请分析以下章节并提取结构化事实卡。

## 章节信息
章节号：第{chapter_num}章
标题：{title}

## 章节正文
{content}

请返回 JSON 格式的事实卡：
```json
{{
  "chapter": {chapter_num},
  "title": "{title}",
  "chapter_outline": "章纲，约300字，写清本章发生了什么",
  "rhythm": {{
    "core_content": "章节核心内容，短语组合",
    "emotion_tone": "章节情绪基调",
    "beat_detail": "节奏拆解"
  }},
  "story_line": "故事线，用→连接关键事件",
  "highlights": ["亮点1", "亮点2"],
  "conflict_level": "低/中/高/峰值",
  "satisfaction_points": ["爽点1", "爽点2"],
  "foreshadowing": ["埋设的伏笔1", "埋设的伏笔2"]
}}
```

只返回 JSON，不要输出其他内容。"""

        messages = [{"role": "user", "content": prompt}]
        result = await self.llm.generate(
            messages, role="KNOWLEDGE_BUILDER", temperature=0.3, max_tokens=2048
        )

        # 解析 JSON
        result = result.strip()
        if result.startswith("```json"):
            result = result[7:]
        if result.startswith("```"):
            result = result[3:]
        if result.endswith("```"):
            result = result[:-3]
        result = result.strip()

        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return {
                "chapter": chapter_num,
                "title": title,
                "chapter_outline": result[:500],
                "parse_error": True,
            }

    async def extract_story_arcs(
        self, chapter_cards: list[dict], window_size: int = 8
    ) -> list[dict]:
        """从单章事实卡中提取故事情节单元

        Args:
            chapter_cards: 单章事实卡列表
            window_size: 滑动窗口大小

        Returns:
            故事情节单元列表
        """
        arcs = []
        total = len(chapter_cards)

        for start in range(0, total, window_size):
            end = min(start + window_size, total)
            window_cards = chapter_cards[start:end]

            # 组装窗口内的事实卡摘要
            cards_text = "\n\n".join([
                f"第{c.get('chapter', i+1)}章 {c.get('title', '')}: {c.get('chapter_outline', '')}"
                for i, c in enumerate(window_cards)
            ])

            prompt = f"""你是一个专业的网文结构分析师。请根据以下章节事实卡，识别自然形成的"故事情节单元"。

不按固定章数切分，而是根据情节功能和阶段闭环判断自然边界（通常2-8章）。

## 章节事实卡（第{start+1}章 ~ 第{end}章）
{cards_text}

请返回 JSON：
```json
{{
  "arcs": [
    {{
      "title": "情节单元标题",
      "start_chapter": N,
      "end_chapter": M,
      "narrative_function": "在全书中的功能",
      "structure": "触发→推进→转折→收束",
      "emotion_curve": "情绪变化链",
      "satisfaction_point": "核心爽点",
      "character_changes": "人物关系变化",
      "gains_costs": "收获与代价",
      "foreshadowing": "保留的线索/危机"
    }}
  ]
}}
```

只返回 JSON。"""

            messages = [{"role": "user", "content": prompt}]
            result = await self.llm.generate(
                messages, role="KNOWLEDGE_BUILDER", temperature=0.3, max_tokens=2048
            )

            # 解析并追加
            parsed = self._parse_json_safe(result)
            if parsed and "arcs" in parsed:
                arcs.extend(parsed["arcs"])

            await self._async_sleep(0.5)

        return arcs

    async def extract_narrative_patterns(self, story_arcs: list[dict]) -> list[dict]:
        """从故事情节单元中抽象叙事模式

        这是拆书最核心的步骤：提取可迁移的结构规律

        Args:
            story_arcs: 故事情节单元列表

        Returns:
            叙事模式列表（去除具体内容，只保留可迁移的结构）
        """
        arcs_text = json.dumps(story_arcs[:20], ensure_ascii=False, indent=2)

        prompt = f"""你是一个专业的叙事模式分析师。请把以下故事情节单元抽象成可迁移的"叙事模式"。

## 故事情节单元
{arcs_text}

要求：
- 只提取可迁移的结构功能：情节功能、冲突结构、信息差结构、情绪曲线、爽点机制、转折方式
- 不保留具体人物名、地名、势力名、历史事件
- 输出可复用的叙事模式

返回 JSON：
```json
{{
  "patterns": [
    {{
      "pattern_id": "PM_001",
      "narrative_function": "情节功能",
      "emotion_curve": "情绪曲线",
      "satisfaction_type": "爽点类型",
      "key_techniques": ["手法1", "手法2"],
      "applicable_scenarios": ["适用场景1"],
      "structure": "触发→推进→转折→收束",
      "notes": "使用注意事项"
    }}
  ]
}}
```

只返回 JSON。"""

        messages = [{"role": "user", "content": prompt}]
        result = await self.llm.generate(
            messages, role="KNOWLEDGE_BUILDER", temperature=0.4, max_tokens=4096
        )

        parsed = self._parse_json_safe(result)
        if parsed and "patterns" in parsed:
            return parsed["patterns"]
        return []

    def _parse_json_safe(self, text: str) -> dict | None:
        """安全解析 JSON"""
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    async def _async_sleep(self, seconds: float):
        """异步休眠"""
        import asyncio
        await asyncio.sleep(seconds)


# 全局单例
_analyzer: NovelAnalyzer | None = None


def get_novel_analyzer() -> NovelAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = NovelAnalyzer()
    return _analyzer
