"""
墨参 · 伏笔检测分析器
从小说章节中识别伏笔（钩子）及其回收情况。
迁移自 routes/foreshadowing.py 中的检测逻辑。
"""
from pathlib import Path
from typing import Any

from analysis.base import BaseTextAnalyzer


class ForeshadowingAnalyzer(BaseTextAnalyzer):
    """伏笔检测分析器

    两种模式：
    - detect: 识别新伏笔 + 检测已有伏笔的回收
    - recover_check: 仅检查已有伏笔是否被回收
    """

    name = "伏笔检测"

    def __init__(self, mode: str = "detect"):
        """
        Args:
            mode: detect | recover_check
        """
        super().__init__()
        self.mode = mode
        self._existing_names: list[str] = []

    def get_role(self) -> str:
        return "STRUCTURE_ANALYST"

    def set_existing(self, names: list[str]):
        """设置已有伏笔名列表（用于 detect 模式）"""
        self._existing_names = names

    def build_prompt(self, text: str, previous: Any, ctx: dict) -> list[dict]:
        if self.mode == "recover_check":
            return self._build_recover_prompt(text, ctx)
        return self._build_detect_prompt(text, ctx)

    def _build_detect_prompt(self, text: str, ctx: dict) -> list[dict]:
        """构建伏笔检测提示词"""
        scope = ctx.get("scope", "single")
        existing = "、".join(self._existing_names) if self._existing_names else "（暂无）"
        focus = "请特别注意章节末尾部分，很多伏笔会在章尾留下。" if scope == "single" else "请通读全部内容，全面梳理。"

        system = "你是专业的小说编辑，擅长识别和梳理故事中的伏笔（钩子）。请分析给定的小说内容，找出其中埋下的伏笔。"
        user = f"""以下是小说内容：

{text}

已知的伏笔列表：{existing}

任务：从上述内容中识别伏笔（钩子）。
{focus}

要求：
1. 找出可能是伏笔的情节、物品、人物设定、预言、谜团等
2. 每条伏笔包含：name（简短伏笔名）、content（具体内容，引用原文关键句或概括）、chapter（所在章节标题）、is_new（是否为新伏笔，true/false）
3. 如果内容中出现了对已有伏笔的呼应/回收，也请标注出来，is_new=false，并说明在哪个章节回收
4. 只返回 JSON 数组，不要有其他文字，格式：
[{{"name": "...", "content": "...", "chapter": "...", "is_new": true/false, "is_resolution": true/false}}]
"""
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def _build_recover_prompt(self, text: str, ctx: dict) -> list[dict]:
        """构建回收检测提示词"""
        existing = ctx.get("existing_foreshadowings", [])
        fs_list = "\n".join([
            f"- {f['name']}（出现{f.get('entry_count', 0)}次，状态：{'未回收' if f.get('status') == 'active' else '已回收'}）"
            for f in existing
        ])

        messages = [
            {"role": "system", "content": "你是专业的小说编辑，负责检查伏笔是否已经被回收（呼应、揭秘、解决）。"},
            {"role": "user", "content": f"""以下是已有的伏笔列表：
{fs_list}

以下是小说章节内容：
{text}

请检查：在上述章节内容中，哪些伏笔已经被回收/呼应/揭秘了？

要求：
1. 只返回已确认回收的伏笔
2. 每条包含：name（伏笔名）、content（回收的具体内容）、chapter（回收所在章节标题）
3. 只返回 JSON 数组，不要有其他文字，格式：
[{{"name": "...", "content": "...", "chapter": "..."}}]
"""},
        ]
        return messages

    def parse_result(self, raw: str) -> Any:
        data = self._parse_json(raw)
        if data is None:
            raise ValueError("LLM 返回内容无法解析为 JSON")
        return data if isinstance(data, list) else []

    def validate(self, data: Any) -> bool:
        return isinstance(data, list)

    def merge(self, previous: Any, new: Any) -> Any:
        # 伏笔检测不需要合并 previous，每次返回新结果
        return new

    async def save(self, data: Any, project_dir: Path, ctx: dict) -> None:
        # 伏笔结果由调用方（路由层）写入项目伏笔库，此处不做持久化
        pass
