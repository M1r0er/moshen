"""
墨参 · 文本分析器基类
所有文本分析器的统一父类，封装工程能力（LLM 调用、重试、JSON 解析、分批、增量、进度回调），
子类只需实现业务差异（prompt、输出结构、存储位置）。
"""
from abc import ABC, abstractmethod
from pathlib import Path
import time
from typing import Any, Callable, Optional

from core.llm_provider import get_llm_provider
from core.utils import parse_json_response
from analysis.chapter_split import split_by_chapters


class BaseTextAnalyzer(ABC):
    """文本分析器抽象基类

    共性能力（基类实现，子类无需关心）：
    - LLM 调用 + 重试（复用 LLMProvider）
    - JSON 解析（复用 parse_json_response，去 markdown fence）
    - 长文本分批（按章节边界切，不切断章节；前批结果作为后批 previous）
    - 增量去重（文件 hash 记录）
    - 进度回调
    - 超时 / 异常处理

    子类必须实现：
    - name: str                    分析器名称
    - get_role() -> str            使用的 LLM 角色
    - build_prompt(text, prev, ctx) -> list[dict]  构建 messages
    - parse_result(raw) -> Any     解析 LLM 输出
    - validate(data) -> bool       校验结果结构
    - save(data, project_dir, ctx) 持久化结果

    子类可选实现：
    - merge(prev, new) -> Any      合并 previous 与新结果（默认覆盖）
    """

    name: str = "BaseTextAnalyzer"

    def __init__(self):
        self.llm = get_llm_provider()

    # ===== 子类必须实现的抽象方法 =====

    @abstractmethod
    def get_role(self) -> str:
        """返回使用的 LLM 角色（如 STRUCTURE_ANALYST、NOVEL_ANALYZER）"""
        ...

    @abstractmethod
    def build_prompt(self, text: str, previous: Any, ctx: dict) -> list[dict]:
        """构建 LLM messages

        Args:
            text: 当前批次的文本
            previous: 上一批次的分析结果（None 表示首次）
            ctx: 上下文（含 project_id、type 等子类自定义字段）
        """
        ...

    @abstractmethod
    def parse_result(self, raw: str) -> Any:
        """解析 LLM 原始输出为结构化数据"""
        ...

    @abstractmethod
    def validate(self, data: Any) -> bool:
        """校验结果结构合法性"""
        ...

    @abstractmethod
    async def save(self, data: Any, project_dir: Path, ctx: dict) -> None:
        """持久化结果到项目目录"""
        ...

    # ===== 子类可选实现的方法 =====

    def merge(self, previous: Any, new: Any) -> Any:
        """合并 previous 与新结果

        默认实现：直接用新结果覆盖。
        子类可重写为增量合并（如关系图谱需要合并实体/关系）。
        """
        return new

    def get_response_format(self) -> dict | None:
        """返回 response_format（如 {"type": "json_object"}），子类可覆盖。
        默认 None，表示不强制格式。
        """
        return None

    def prompt_previous_size(self, previous: Any) -> int:
        """previous 在 prompt 中实际占用的字符数

        子类可覆盖（例如把过大的现有数据精简后再回传），保证分批预算按真实请求体积计算。
        """
        return len(str(previous)) if previous else 0

    # ===== 基类提供的能力 =====

    async def _call_llm(
        self,
        messages: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 4096,
        response_format: Optional[dict] = None,
    ) -> str:
        """调用 LLM 并返回原始文本

        Args:
            response_format: 如 {"type": "json_object"}，用于强制 JSON 输出
        """
        result = await self.llm.generate(
            messages,
            role=self.get_role(),
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )
        return result

    def _parse_json(self, raw: str) -> Any:
        """解析 LLM 输出为 JSON（去 markdown fence）"""
        return parse_json_response(raw)

    def split_chapters(self, content: str) -> list[dict] | None:
        """按章节边界分割文本"""
        return split_by_chapters(content)

    async def analyze(
        self,
        text: str,
        project_id: str,
        preset: str = "standard",
        previous: Any = None,
        ctx: Optional[dict] = None,
        progress_cb: Optional[Callable[..., None]] = None,
    ) -> Any:
        """主分析入口（单批文本）

        Args:
            text: 待分析文本
            project_id: 项目 ID
            preset: 分析档位 fast/standard/deep
            previous: 上一批结果（用于累积）
            ctx: 上下文
            progress_cb: 进度回调
        """
        ctx = ctx or {}
        ctx.setdefault("project_id", project_id)
        ctx.setdefault("preset", preset)

        messages = self.build_prompt(text, previous, ctx)
        raw = await self._call_llm(messages, response_format=self.get_response_format())

        # 解析失败时重试一次（在 prompt 中追加强调）
        try:
            data = self.parse_result(raw)
        except (ValueError, Exception):
            retry_msg = "上一次返回的内容无法解析为 JSON。请只输出纯 JSON，不要有 markdown 围栏、解释文字或思考过程。直接以 { 开头。"
            retry_messages = messages + [{"role": "assistant", "content": raw[:500]}, {"role": "user", "content": retry_msg}]
            raw2 = await self._call_llm(retry_messages, response_format=self.get_response_format())
            data = self.parse_result(raw2)

        if not self.validate(data):
            raise ValueError(f"{self.name} 分析结果校验失败")

        merged = self.merge(previous, data) if previous is not None else data
        return merged

    async def analyze_batched(
        self,
        chapters: list[dict],
        project_id: str,
        preset: str = "standard",
        previous: Any = None,
        ctx: Optional[dict] = None,
        progress_cb: Optional[Callable[..., None]] = None,
        batch_chars: int = 8000,
        min_batch_chars: int = 3000,
        max_prompt_chars: int = 100000,
        overhead_chars: int = 0,
    ) -> Any:
        """分批分析（按章节边界合并批次，前批结果作为后批 previous）

        Args:
            chapters: [{"name": "...", "content": "..."}]
            batch_chars: 目标批大小（字符）。越小批次越多、粒度越细，代价是调用次数与花费上升
            min_batch_chars: 批次下限；现有数据过大时也不会把批次压到比这更小
            max_prompt_chars: 单次请求上下文上限（字符）；据此自动收缩批次，避免超出模型上下文
            overhead_chars: 每次请求中固定部分（系统提示词等）的大致字符数
        """
        ctx = ctx or {}
        ctx.setdefault("project_id", project_id)
        ctx.setdefault("preset", preset)

        # 批次预算：以 batch_chars 为目标，但不超过上下文上限（扣掉现有数据与固定提示词开销）
        prev_size = self.prompt_previous_size(previous)
        allowed = max_prompt_chars - prev_size - overhead_chars - 1000
        budget = max(min_batch_chars, min(batch_chars, allowed))
        if budget < batch_chars and progress_cb:
            progress_cb(
                f"现有数据较大（约 {prev_size // 1000}K 字符），单批自动收缩为 {budget} 字符以适配模型上下文"
            )

        # 按 budget 合并章节为批次
        batches: list[list[dict]] = []
        current: list[dict] = []
        size = 0
        for ch in chapters:
            current.append(ch)
            size += len(ch.get("content", ""))
            if size >= budget:
                batches.append(current)
                current = []
                size = 0
        if current:
            batches.append(current)

        if not batches:
            return previous

        data = previous
        total = len(batches)
        for i, batch in enumerate(batches):
            if progress_cb:
                progress_cb(
                    f"批次 {i + 1}/{total} 分析中…",
                    meta={
                        "completed": i,
                        "total": total,
                        "unit": "批",
                        "phase": "batch",
                    },
                )
            text = "\n\n".join(
                f"【{c.get('name', '')}】\n{c.get('content', '')}" for c in batch
            )
            started = time.perf_counter()
            data = await self.analyze(
                text, project_id, preset, previous=data, ctx=ctx, progress_cb=progress_cb
            )
            if progress_cb:
                progress_cb(
                    f"批次 {i + 1}/{total} 完成，用时 {time.perf_counter() - started:.1f}s",
                    meta={
                        "completed": i + 1,
                        "total": total,
                        "unit": "批",
                        "phase": "batch",
                    },
                )

        return data
