"""
墨参 · LLM 调用封装
OpenAI 兼容接口，支持流式输出、重试、超时
"""
import asyncio
import json
import time
from typing import AsyncGenerator
import httpx
from .config import get_config_manager, ModelConfig


class LLMProvider:
    """LLM 调用提供器，封装 OpenAI 兼容接口"""

    def __init__(self):
        self.config_manager = get_config_manager()
        self.timeout = 300  # 默认超时 5 分钟
        self.max_retries = 2
        self._prompt_log: list[dict] = []

    def _build_headers(self, cfg: ModelConfig) -> dict:
        return {
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(
        self,
        cfg: ModelConfig,
        messages: list[dict],
        stream: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        payload = {
            "model": cfg.model,
            "messages": messages,
            "stream": stream,
            "temperature": temperature if temperature is not None else cfg.temperature,
            "max_tokens": max_tokens or cfg.max_tokens,
        }
        return payload

    async def generate(
        self,
        messages: list[dict],
        role: str = "DIALOGUE_PARTNER",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """同步生成（非流式），返回完整文本"""
        cfg = self.config_manager.get_model(role)
        if cfg is None:
            raise RuntimeError("没有可用的 LLM 模型配置，请先在设置中配置 API Key")

        payload = self._build_payload(cfg, messages, stream=False, temperature=temperature, max_tokens=max_tokens)
        url = f"{cfg.base_url.rstrip('/')}/chat/completions"

        self._log_prompt(role, messages)

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(url, headers=self._build_headers(cfg), json=payload)
                    if resp.status_code in (401, 402, 403):
                        raise RuntimeError(f"认证失败 ({resp.status_code})，请检查 API Key")
                    resp.raise_for_status()
                    data = resp.json()
                    return data["choices"][0]["message"]["content"]
            except httpx.HTTPStatusError as e:
                last_error = e
                if resp.status_code in (401, 402, 403):
                    raise
                if attempt < self.max_retries:
                    await asyncio.sleep(1.5 * (attempt + 1))
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    await asyncio.sleep(1.5 * (attempt + 1))

        raise RuntimeError(f"LLM 调用失败（重试 {self.max_retries} 次后仍失败）: {last_error}")

    async def generate_stream(
        self,
        messages: list[dict],
        role: str = "DIALOGUE_PARTNER",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[str, None]:
        """流式生成，逐块返回文本"""
        cfg = self.config_manager.get_model(role)
        if cfg is None:
            raise RuntimeError("没有可用的 LLM 模型配置，请先在设置中配置 API Key")

        payload = self._build_payload(cfg, messages, stream=True, temperature=temperature, max_tokens=max_tokens)
        url = f"{cfg.base_url.rstrip('/')}/chat/completions"

        self._log_prompt(role, messages)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST", url, headers=self._build_headers(cfg), json=payload
            ) as resp:
                if resp.status_code in (401, 402, 403):
                    body = await resp.aread()
                    raise RuntimeError(f"认证失败 ({resp.status_code})，请检查 API Key")
                resp.raise_for_status()

                async for line in resp.aiter_lines():
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except json.JSONDecodeError:
                            continue

    def _log_prompt(self, role: str, messages: list[dict]):
        """记录提示词调用日志（仅保留最近 50 条）"""
        self._prompt_log.append({
            "role": role,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "message_count": len(messages),
            "total_chars": sum(len(m.get("content", "")) for m in messages),
        })
        if len(self._prompt_log) > 50:
            self._prompt_log = self._prompt_log[-50:]

    def get_log(self) -> list[dict]:
        return self._prompt_log


# 全局单例
_provider: LLMProvider | None = None


def get_llm_provider() -> LLMProvider:
    global _provider
    if _provider is None:
        _provider = LLMProvider()
    return _provider
