"""
墨参 · 提示词模板加载器
每个提示词存放在独立文件夹的 prompt.txt 中，支持变量替换
"""
import os
from pathlib import Path


class PromptLoader:
    """提示词模板加载器"""

    def __init__(self, prompts_dir: str | None = None):
        if prompts_dir is None:
            from core.resource_path import get_prompts_dir
            prompts_dir = str(get_prompts_dir())
        self.prompts_dir = prompts_dir
        self._cache: dict[str, str] = {}

    def load(self, name: str, **kwargs) -> str:
        """加载提示词模板并替换变量

        Args:
            name: 提示词名称（对应 prompts/ 下的文件夹名）
            **kwargs: 模板变量

        Returns:
            替换变量后的提示词文本
        """
        template = self._cache.get(name)
        if template is None:
            template_path = os.path.join(self.prompts_dir, name, "prompt.txt")
            if not os.path.exists(template_path):
                raise FileNotFoundError(f"提示词模板不存在: {template_path}")
            with open(template_path, "r", encoding="utf-8") as f:
                template = f.read()
            self._cache[name] = template

        if kwargs:
            try:
                return template.format(**kwargs)
            except KeyError as e:
                raise KeyError(f"提示词模板 {name} 缺少变量: {e}")
        return template

    def load_raw(self, name: str) -> str:
        """加载原始模板（不替换变量）"""
        if name in self._cache:
            return self._cache[name]
        template_path = os.path.join(self.prompts_dir, name, "prompt.txt")
        if not os.path.exists(template_path):
            raise FileNotFoundError(f"提示词模板不存在: {template_path}")
        with open(template_path, "r", encoding="utf-8") as f:
            content = f.read()
        self._cache[name] = content
        return content

    def clear_cache(self):
        self._cache.clear()

    def list_prompts(self) -> list[str]:
        """列出所有可用提示词"""
        if not os.path.exists(self.prompts_dir):
            return []
        return [
            d for d in os.listdir(self.prompts_dir)
            if os.path.isdir(os.path.join(self.prompts_dir, d))
            and os.path.exists(os.path.join(self.prompts_dir, d, "prompt.txt"))
        ]


# 全局单例
_loader: PromptLoader | None = None


def get_prompt_loader() -> PromptLoader:
    global _loader
    if _loader is None:
        _loader = PromptLoader()
    return _loader
