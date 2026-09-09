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
        response_format: dict | None = None,
    ) -> dict:
        payload = {
            "model": cfg.model,
            "messages": messages,
            "stream": stream,
            "temperature": temperature if temperature is not None else cfg.temperature,
            "max_tokens": max_tokens or cfg.max_tokens,
        }
        if response_format:
            payload["response_format"] = response_format
        return payload

    async def generate(
        self,
        messages: list[dict],
        role: str = "DIALOGUE_PARTNER",
        temperature: float | None = None,
        max_tokens: int | None = None,
        model_override: str | None = None,
        user_input: str = "",
        intent: str = "",
        response_format: dict | None = None,
    ) -> str:
        """同步生成（非流式），返回完整文本

        Args:
            model_override: 指定模型名称。None 或 "auto" 表示自动选择
            user_input: 用户输入（auto模式下用于任务判断）
            intent: 意图（auto模式下用于任务判断）
            response_format: 响应格式，如 {"type": "json_object"} 强制 JSON 输出
        """
        cfg = self.config_manager.get_model(
            role, model_name=model_override, user_input=user_input, intent=intent
        )
        if cfg is None:
            raise RuntimeError("没有可用的 LLM 模型配置，请先在设置中配置 API Key")

        payload = self._build_payload(
            cfg, messages, stream=False, temperature=temperature,
            max_tokens=max_tokens, response_format=response_format,
        )
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
        model_override: str | None = None,
        user_input: str = "",
        intent: str = "",
    ) -> AsyncGenerator[str, None]:
        """流式生成，逐块返回文本

        Args:
            model_override: 指定模型名称。None 或 "auto" 表示自动选择
            user_input: 用户输入（auto模式下用于任务判断）
            intent: 意图（auto模式下用于任务判断）
        """
        cfg = self.config_manager.get_model(
            role, model_name=model_override, user_input=user_input, intent=intent
        )
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

    async def generate_image(self, prompt: str, size: str | None = None, quality: str | None = None) -> dict:
        """调用图像生成 API，返回 {"b64": "..."} 或 {"url": "..."} 或 {"error": "..."}"""
        img_cfg = self.config_manager.image_config
        if not img_cfg.get("enabled"):
            return {"error": "图像生成未启用，请在设置中开启"}
        if not img_cfg.get("api_key") or not img_cfg.get("base_url"):
            return {"error": "图像生成 API 未配置，请设置 Base URL 和 API Key"}

        base = img_cfg["base_url"].rstrip("/")
        # 容错：用户可能已在 Base URL 里带完整接口路径
        url = base if base.endswith("/images/generations") else f"{base}/images/generations"
        size = size or img_cfg.get("size", "1024x1024")
        quality = quality or img_cfg.get("quality", "auto")

        body = {"prompt": prompt, "n": 1, "size": size}
        if img_cfg.get("model"):
            body["model"] = img_cfg["model"]
        if quality and quality != "auto":
            body["quality"] = quality

        headers = {
            "Authorization": f"Bearer {img_cfg['api_key']}",
            "Content-Type": "application/json",
        }

        # 优先请求 b64_json；部分服务不支持时回退
        for with_b64 in (True, False):
            payload = {**body, "response_format": "b64_json"} if with_b64 else body
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                    if resp.status_code in (400, 422) and with_b64:
                        continue  # 服务不支持 b64_json 参数，回退
                    if resp.status_code in (401, 402, 403):
                        return {"error": f"认证失败 ({resp.status_code})"}
                    resp.raise_for_status()
                    data = resp.json()
                    item = (data.get("data") or [{}])[0]
                    if item.get("b64_json"):
                        return {"b64": item["b64_json"]}
                    if item.get("url"):
                        return {"url": item["url"]}
                    return {"error": "绘图 API 未返回图片内容"}
            except httpx.HTTPStatusError as e:
                return {"error": f"绘图 API 返回 {e.response.status_code}: {str(e.response.text)[:200]}"}
            except Exception as e:
                if not with_b64:
                    return {"error": f"绘图 API 调用失败: {type(e).__name__}: {str(e)[:200]}"}

        return {"error": "绘图 API 调用失败"}

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
