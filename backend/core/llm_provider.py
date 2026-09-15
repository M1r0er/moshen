"""
墨参 · LLM 调用封装
OpenAI 兼容接口，支持流式输出、重试、超时
"""
import asyncio
import json
import time
from pathlib import Path
from typing import AsyncGenerator
import httpx
from .config import get_config_manager, ModelConfig


def new_usage_stats() -> dict:
    """一次统计周期的初始用量结构"""
    return {
        "calls": 0,               # 成功调用次数
        "failed_calls": 0,        # 失败调用次数
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "elapsed": 0.0,           # 成功调用累计耗时（秒）
        "usage_missing": 0,       # 服务端未返回 usage 的次数
    }


def _usage_log_path() -> Path:
    d = Path.home() / ".moshen" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / "llm-usage.jsonl"


class LLMProvider:
    """LLM 调用提供器，封装 OpenAI 兼容接口"""

    def __init__(self):
        self.config_manager = get_config_manager()
        self.timeout = 300  # 默认超时 5 分钟
        self.max_retries = 2
        self._prompt_log: list[dict] = []
        # 最近一次流式调用的结束原因（stop/length/...），供上层诊断截断等问题
        self.last_finish_reason: str | None = None
        # 用量统计（供星图等长任务统计耗时与 token 消耗）
        self.stats: dict = new_usage_stats()

    def reset_stats(self) -> None:
        """开启新一轮统计（如一次星图分析任务开始前）"""
        self.stats = new_usage_stats()

    def _record_usage(self, role: str, model: str, usage: dict, elapsed: float) -> None:
        """累计一次调用的耗时与 token，并追加写入用量日志"""
        self.stats["calls"] += 1
        self.stats["elapsed"] += elapsed
        if not usage:
            self.stats["usage_missing"] += 1
        p = int(usage.get("prompt_tokens") or 0)
        c = int(usage.get("completion_tokens") or 0)
        t = int(usage.get("total_tokens") or (p + c))
        self.stats["prompt_tokens"] += p
        self.stats["completion_tokens"] += c
        self.stats["total_tokens"] += t
        record = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "role": role,
            "model": model,
            "elapsed": round(elapsed, 3),
            "prompt_tokens": p,
            "completion_tokens": c,
            "total_tokens": t,
        }
        try:
            with open(_usage_log_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass

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
        }
        # temperature 与 max_tokens 仅在确有取值时下发，避免把 None 传给服务端；
        # 使用 `is not None` 判断，使显式传入 0/较小值也能生效。
        temp = temperature if temperature is not None else cfg.temperature
        if temp is not None:
            payload["temperature"] = temp
        tokens = max_tokens if max_tokens is not None else cfg.max_tokens
        if tokens:
            payload["max_tokens"] = tokens
        if response_format:
            payload["response_format"] = response_format
        return payload

    @staticmethod
    def _extract_content(message: dict) -> str:
        """从 message 中提取文本内容，兼容字符串与分段（多模态）结构"""
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item, str):
                    parts.append(item)
            return "".join(parts)
        return ""

    @classmethod
    def _extract_delta(cls, delta: dict) -> str:
        """从流式 delta 中提取正文增量

        注意：`reasoning_content`（思维链）不计入正文，仅在存在时忽略；
        这保证推理模型不会把思考过程混入作品文本。
        """
        return cls._extract_content(delta)

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
            t0 = time.perf_counter()
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(url, headers=self._build_headers(cfg), json=payload)
                    if resp.status_code in (401, 402, 403):
                        self.stats["failed_calls"] += 1
                        raise RuntimeError(f"认证失败 ({resp.status_code})，请检查 API Key")
                    resp.raise_for_status()
                    data = resp.json()
                    choices = data.get("choices") or []
                    if not choices:
                        self.stats["failed_calls"] += 1
                        raise RuntimeError("模型返回为空（无 choices）")
                    self._record_usage(role, cfg.model, data.get("usage") or {},
                                       time.perf_counter() - t0)
                    return self._extract_content(choices[0].get("message") or {})
            except RuntimeError:
                # 认证失败/空响应等不可重试错误，直接抛出
                raise
            except httpx.HTTPStatusError as e:
                last_error = e
                if attempt < self.max_retries:
                    await asyncio.sleep(1.5 * (attempt + 1))
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    await asyncio.sleep(1.5 * (attempt + 1))

        self.stats["failed_calls"] += 1
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

        # 断流恢复：仅当"尚未产出任何内容"时才允许重试，避免重复输出半截正文。
        last_error = None
        self.last_finish_reason = None

        for attempt in range(self.max_retries + 1):
            yielded_any = False
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    async with client.stream(
                        "POST", url, headers=self._build_headers(cfg), json=payload
                    ) as resp:
                        if resp.status_code in (401, 402, 403):
                            await resp.aread()
                            raise RuntimeError(f"认证失败 ({resp.status_code})，请检查 API Key")
                        resp.raise_for_status()

                        async for line in resp.aiter_lines():
                            if not line or line.startswith(":"):
                                continue
                            if not line.startswith("data:"):
                                continue
                            data_str = line[5:].strip()
                            if not data_str or data_str == "[DONE]":
                                if data_str == "[DONE]":
                                    break
                                continue
                            try:
                                data = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue
                            choices = data.get("choices") or []
                            if not choices:
                                continue
                            choice = choices[0] or {}
                            finish = choice.get("finish_reason")
                            if finish:
                                self.last_finish_reason = finish
                            content = self._extract_delta(choice.get("delta") or {})
                            if content:
                                yielded_any = True
                                yield content
                return
            except RuntimeError:
                # 认证失败等不可重试错误，直接抛出
                raise
            except httpx.HTTPStatusError as e:
                last_error = e
                code = e.response.status_code
                retryable = code == 429 or code >= 500
                if yielded_any or not retryable or attempt >= self.max_retries:
                    raise RuntimeError(f"LLM 流式调用失败 (HTTP {code})：{e}")
                await asyncio.sleep(1.5 * (attempt + 1))
            except Exception as e:
                last_error = e
                if yielded_any or attempt >= self.max_retries:
                    raise RuntimeError(
                        f"LLM 流式调用失败（重试 {self.max_retries} 次后仍失败）: {last_error}"
                    )
                await asyncio.sleep(1.5 * (attempt + 1))

    @staticmethod
    def _image_error_text(resp: "httpx.Response") -> str:
        """从生图服务响应中提取可读错误信息（兼容 OpenAI 与火山方舟两种错误结构）"""
        try:
            data = resp.json()
        except Exception:
            return str(resp.text)[:200]
        err = data.get("error")
        if isinstance(err, dict):
            code = err.get("code") or err.get("type") or ""
            msg = err.get("message") or ""
            return f"{code} {msg}".strip() or str(data)[:200]
        if isinstance(err, str):
            return err[:200]
        if isinstance(data.get("message"), str):
            return data["message"][:200]
        return str(data)[:200]

    def _image_endpoint(self) -> tuple[str, dict]:
        """返回 (生图 URL, image_config)，配置不可用时 URL 为空串"""
        img_cfg = self.config_manager.image_config or {}
        base = (img_cfg.get("base_url") or "").rstrip("/")
        url = base if base.endswith("/images/generations") else (f"{base}/images/generations" if base else "")
        return url, img_cfg

    async def generate_image(self, prompt: str, size: str | None = None, quality: str | None = None) -> dict:
        """调用图像生成 API，返回 {"b64": "..."} 或 {"url": "..."} 或 {"error": "..."}

        不同厂商对可选参数支持不一致（例如火山方舟的 images 接口不接受 quality、
        部分服务不支持 response_format），因此按"完整 → 去 quality → 去 response_format"
        依次降级重试，避免因为一个可选参数被判 400 而整条生图链路失败。
        """
        img_cfg = self.config_manager.image_config
        if not img_cfg.get("enabled"):
            return {"error": "图像生成未启用，请在设置中开启"}
        url, _ = self._image_endpoint()
        if not img_cfg.get("api_key") or not url:
            return {"error": "图像生成 API 未配置，请设置 Base URL 和 API Key"}

        size = size or img_cfg.get("size", "1024x1024")
        quality = quality or img_cfg.get("quality", "auto")

        base_body: dict = {"prompt": prompt, "n": 1, "size": size}
        if img_cfg.get("model"):
            base_body["model"] = img_cfg["model"]
        with_quality = dict(base_body)
        if quality and quality != "auto":
            with_quality["quality"] = quality

        headers = {
            "Authorization": f"Bearer {img_cfg['api_key']}",
            "Content-Type": "application/json",
        }

        variants = [
            {**with_quality, "response_format": "b64_json"},
            {**base_body, "response_format": "b64_json"},
            base_body,
        ]

        last_error = "绘图 API 调用失败"
        for payload in variants:
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                    if resp.status_code in (400, 422):
                        # 可选参数不被支持：记录原因后换更精简的参数再试
                        last_error = f"绘图 API 返回 {resp.status_code}: {self._image_error_text(resp)}"
                        continue
                    if resp.status_code in (401, 402, 403):
                        return {"error": f"认证失败 ({resp.status_code})：{self._image_error_text(resp)}"}
                    resp.raise_for_status()
                    data = resp.json()
                    item = (data.get("data") or [{}])[0]
                    if item.get("b64_json"):
                        return {"b64": item["b64_json"]}
                    if item.get("url"):
                        return {"url": item["url"]}
                    return {"error": f"绘图 API 未返回图片内容：{str(data)[:200]}"}
            except httpx.HTTPStatusError as e:
                return {"error": f"绘图 API 返回 {e.response.status_code}: {self._image_error_text(e.response)}"}
            except Exception as e:
                last_error = f"绘图 API 调用失败: {type(e).__name__}: {str(e)[:200]}"

        return {"error": last_error}

    async def probe_image(self) -> dict:
        """低成本探测生图 API 是否可用（不真实出图，不产生费用）

        做法：故意传一个任何模型都不接受的尺寸，让服务端在"参数校验"阶段就拒绝：
        - 401/403/402  → 鉴权问题
        - 404/ModelNotOpen → 模型未开通
        - 400（参数相关）→ 说明鉴权与模型都正常，可视为可用
        - 200 → 服务居然接受了，同样视为可用
        """
        img_cfg = self.config_manager.image_config
        if not img_cfg.get("enabled"):
            return {"ok": False, "detail": "图像生成未启用"}
        url, _ = self._image_endpoint()
        if not img_cfg.get("api_key") or not url:
            return {"ok": False, "detail": "未配置 Base URL 或 API Key"}

        body = {"prompt": "probe", "n": 1, "size": "1x1"}
        if img_cfg.get("model"):
            body["model"] = img_cfg["model"]
        headers = {
            "Authorization": f"Bearer {img_cfg['api_key']}",
            "Content-Type": "application/json",
        }
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, headers=headers, json=body)
        except Exception as e:
            return {"ok": False, "detail": f"请求失败：{type(e).__name__}: {str(e)[:150]}"}
        elapsed = round(time.perf_counter() - t0, 2)

        if resp.status_code in (401, 402, 403):
            return {"ok": False, "elapsed": elapsed,
                    "detail": f"鉴权失败 ({resp.status_code})：{self._image_error_text(resp)}"}
        if resp.status_code in (400, 404, 422):
            text = self._image_error_text(resp)
            low = text.lower()
            if "notopen" in low or "not activated" in low or "未开通" in text:
                return {"ok": False, "elapsed": elapsed, "detail": f"模型未开通：{text}"}
            if resp.status_code == 404:
                return {"ok": False, "elapsed": elapsed, "detail": f"接口或模型不存在：{text}"}
            return {"ok": True, "elapsed": elapsed,
                    "detail": f"鉴权与模型可用（探测请求被参数校验拒绝，属预期）：{text[:120]}"}
        if resp.status_code >= 500:
            return {"ok": False, "elapsed": elapsed,
                    "detail": f"服务端错误 ({resp.status_code})：{self._image_error_text(resp)}"}
        return {"ok": True, "elapsed": elapsed, "detail": f"调用成功 (HTTP {resp.status_code})"}

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
