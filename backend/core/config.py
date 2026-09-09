"""
墨参 · 多模型配置管理器
支持默认API统一调用 + 可选的四角色独立API配置
每个API可配置多个模型，支持auto模式自动选择
"""
import os
import json
from pathlib import Path
from dataclasses import dataclass, field
from dotenv import load_dotenv


@dataclass
class ModelConfig:
    """单个模型角色配置（支持多模型）"""
    role: str
    models: list[str] = field(default_factory=list)
    base_url: str = ""
    api_key: str = ""
    temperature: float = 0.7
    max_tokens: int = 8192

    @property
    def model(self) -> str:
        """默认模型（第一个）"""
        return self.models[0] if self.models else ""

    def is_configured(self) -> bool:
        return bool(self.models and self.base_url and self.api_key)

    def get_model_for_task(self, user_input: str = "", intent: str = "") -> str:
        """根据任务自动选择模型（auto模式）

        策略：
        - 只有一个模型：直接用
        - 复杂任务（分析/诊断/拆解/长文本）：用第一个（通常是最强的）
        - 简单任务（短文本/闲聊）：用最后一个（通常是最轻量的）
        """
        if not self.models:
            return ""
        if len(self.models) == 1:
            return self.models[0]

        # 复杂任务关键词
        complex_keywords = ["分析", "诊断", "拆解", "拆书", "蒸馏", "评估", "检查",
                           "逻辑", "结构", "大纲", "全面", "详细", "深入", "优化"]
        # 简单任务关键词
        simple_keywords = ["你好", "谢谢", "哈哈", "好的", "嗯", "继续", "ok"]

        text = user_input.lower()
        is_complex = (
            len(user_input) > 500
            or any(kw in text for kw in complex_keywords)
            or intent in ("reference_dissect", "logic_check", "style_analyze")
        )
        is_simple = (
            len(user_input) < 50
            or any(kw in text for kw in simple_keywords)
            or intent == "free_chat"
        )

        if is_complex:
            return self.models[0]  # 最强模型
        if is_simple and len(self.models) > 1:
            return self.models[-1]  # 最轻量模型
        return self.models[0]  # 默认用最强


# 角色定义
MODEL_ROLES = {
    "TEXT_MASTER": "文学性分析、文风诊断、文笔评估",
    "STRUCTURE_ANALYST": "大纲结构分析、节奏诊断、逻辑检查",
    "KNOWLEDGE_BUILDER": "拆书、知识提取、叙事模式抽象",
    "DIALOGUE_PARTNER": "日常对话、创意讨论、问答",
    "NOVEL_ANALYZER": "小说关系图谱分析、实体与关系提取",
    "IMAGE_GENERATOR": "人物肖像绘制 prompt 生成（复用聊天 API）",
}

# 角色降级链：首选不可用时按序降级
FALLBACK_CHAIN = {
    "TEXT_MASTER": ["STRUCTURE_ANALYST", "DIALOGUE_PARTNER"],
    "STRUCTURE_ANALYST": ["TEXT_MASTER", "DIALOGUE_PARTNER"],
    "KNOWLEDGE_BUILDER": ["DIALOGUE_PARTNER"],
    "DIALOGUE_PARTNER": ["TEXT_MASTER", "STRUCTURE_ANALYST"],
    "NOVEL_ANALYZER": ["STRUCTURE_ANALYST", "KNOWLEDGE_BUILDER", "DIALOGUE_PARTNER"],
    "IMAGE_GENERATOR": ["NOVEL_ANALYZER", "DIALOGUE_PARTNER"],
}


class ConfigManager:
    """全局配置管理器"""

    def __init__(self, env_path: str | None = None):
        if env_path is None:
            home = Path.home()
            env_path = str(home / ".moshen" / ".env")
        self.env_path = env_path
        self._models: dict[str, ModelConfig] = {}
        self._default_api: ModelConfig = ModelConfig(role="DEFAULT")
        self._independent_keys: bool = False
        self._load()

    def _parse_models(self, val: str) -> list[str]:
        """解析模型列表（逗号分隔）"""
        if not val:
            return []
        return [m.strip() for m in val.split(",") if m.strip()]

    def _load(self):
        """从 .env 文件加载配置"""
        if os.path.exists(self.env_path):
            load_dotenv(self.env_path, override=True)

        # 加载默认 API（支持新格式 DEFAULT_API_MODELS 和旧格式 DEFAULT_API_MODEL）
        models_str = os.getenv("DEFAULT_API_MODELS", "")
        if not models_str:
            # 向后兼容：旧格式单模型
            old_model = os.getenv("DEFAULT_API_MODEL", "")
            models_str = old_model

        self._default_api = ModelConfig(
            role="DEFAULT",
            models=self._parse_models(models_str),
            base_url=os.getenv("DEFAULT_API_BASE_URL", ""),
            api_key=os.getenv("DEFAULT_API_KEY", ""),
            temperature=float(os.getenv("DEFAULT_API_TEMPERATURE", "0.7")),
            max_tokens=int(os.getenv("DEFAULT_API_MAX_TOKENS", "8192")),
        )

        # 加载独立开关
        self._independent_keys = os.getenv("INDEPENDENT_API_KEYS", "False").lower() in ("true", "1", "yes")

        # 加载四角色配置
        for role in MODEL_ROLES:
            # 优先使用多模型格式
            role_models_str = os.getenv(f"{role}_MODELS", "")
            if not role_models_str:
                # 向后兼容：旧格式单模型
                role_models_str = os.getenv(f"{role}_MODEL", "")

            cfg = ModelConfig(
                role=role,
                models=self._parse_models(role_models_str),
                base_url=os.getenv(f"{role}_BASE_URL", ""),
                api_key=os.getenv(f"{role}_API_KEY", ""),
                temperature=float(os.getenv(f"{role}_TEMPERATURE", "0.7")),
                max_tokens=int(os.getenv(f"{role}_MAX_TOKENS", "8192")),
            )
            self._models[role] = cfg

        # 加载 API 渠道列表
        channels_raw = os.getenv("API_CHANNELS", "")
        self._api_channels: list[dict] = []
        if channels_raw:
            try:
                self._api_channels = json.loads(channels_raw)
                if not isinstance(self._api_channels, list):
                    self._api_channels = []
            except (json.JSONDecodeError, TypeError):
                self._api_channels = []

        # 加载图像生成 API 配置（独立于聊天 API）
        self._image_config = {
            "enabled": os.getenv("IMAGE_ENABLED", "False").lower() in ("true", "1", "yes"),
            "base_url": os.getenv("IMAGE_BASE_URL", ""),
            "api_key": os.getenv("IMAGE_API_KEY", ""),
            "model": os.getenv("IMAGE_MODEL", ""),
            "size": os.getenv("IMAGE_SIZE", "1024x1024"),
            "quality": os.getenv("IMAGE_QUALITY", "auto"),
        }

    def reload(self):
        """重新加载配置"""
        # 清除环境变量缓存
        keys_to_clear = [
            "DEFAULT_API_MODELS", "DEFAULT_API_MODEL", "DEFAULT_API_BASE_URL", "DEFAULT_API_KEY",
            "DEFAULT_API_TEMPERATURE", "DEFAULT_API_MAX_TOKENS",
            "INDEPENDENT_API_KEYS",
            "API_CHANNELS",
        ]
        for role in MODEL_ROLES:
            keys_to_clear.extend([
                f"{role}_MODELS", f"{role}_MODEL", f"{role}_BASE_URL",
                f"{role}_API_KEY", f"{role}_TEMPERATURE", f"{role}_MAX_TOKENS",
            ])
        for key in keys_to_clear:
            os.environ.pop(key, None)
        self._models.clear()
        self._load()

    @property
    def independent_keys(self) -> bool:
        return self._independent_keys

    def get_available_models(self, role: str = "DEFAULT") -> list[str]:
        """获取某个角色可用的所有模型列表

        优先返回所有 API 渠道中的模型合集（跨渠道），
        若没有渠道则回退到默认 API / 角色配置。
        """
        # 1. 汇总所有渠道的模型（跨渠道）
        all_models: list[str] = []
        for ch in self._api_channels:
            for m in ch.get("models", []):
                if m and m.strip() and m.strip() not in all_models:
                    all_models.append(m.strip())
        if all_models:
            return all_models

        # 2. 回退：独立模式关闭或 DEFAULT 角色
        if not self._independent_keys or role == "DEFAULT":
            return list(self._default_api.models)
        cfg = self._models.get(role)
        if cfg and cfg.is_configured():
            return list(cfg.models)
        # 降级
        for fallback_role in FALLBACK_CHAIN.get(role, []):
            fallback_cfg = self._models.get(fallback_role)
            if fallback_cfg and fallback_cfg.is_configured():
                return list(fallback_cfg.models)
        if self._default_api.is_configured():
            return list(self._default_api.models)
        return []

    def get_channel_by_model(self, model_name: str) -> dict | None:
        """根据模型名查找所属的 API 渠道"""
        if not model_name:
            return None
        for ch in self._api_channels:
            models = ch.get("models", [])
            if model_name in models:
                return ch
        return None

    def get_default_channel(self) -> dict | None:
        """获取标记为默认的渠道（或第一个已配置的渠道）"""
        for ch in self._api_channels:
            if ch.get("is_default"):
                return ch
        # 回退：第一个有模型+base_url+api_key 的渠道
        for ch in self._api_channels:
            if (ch.get("models") and ch.get("base_url") and ch.get("api_key")):
                return ch
        return self._api_channels[0] if self._api_channels else None

    def get_model(self, role: str, model_name: str | None = None,
                  user_input: str = "", intent: str = "") -> ModelConfig | None:
        """获取指定角色的模型配置

        Args:
            role: 模型角色
            model_name: 指定模型名称。None 或 "auto" 表示自动选择
            user_input: 用户输入（用于auto模式判断）
            intent: 意图（用于auto模式判断）

        路由策略：
        - 若指定了具体模型名：在所有 API 渠道中查找该模型，命中即用对应渠道的配置
        - 若为 auto 或未指定：使用默认渠道的模型（按任务复杂度自动选）
        """
        # 1. 具体模型名 → 跨渠道查找
        if model_name and model_name != "auto":
            ch = self.get_channel_by_model(model_name)
            if ch is not None:
                return ModelConfig(
                    role=role,
                    models=[model_name],
                    base_url=ch.get("base_url", ""),
                    api_key=ch.get("api_key", ""),
                    temperature=float(ch.get("temperature", 0.7)),
                    max_tokens=int(ch.get("max_tokens", 8192)),
                )
            # 渠道中没找到，回退到默认 API（兼容旧逻辑）
            base_cfg = self._get_base_config(role)
            if base_cfg is not None and model_name in base_cfg.models:
                return ModelConfig(
                    role=base_cfg.role,
                    models=[model_name],
                    base_url=base_cfg.base_url,
                    api_key=base_cfg.api_key,
                    temperature=base_cfg.temperature,
                    max_tokens=base_cfg.max_tokens,
                )

        # 2. auto 模式：使用默认渠道
        default_ch = self.get_default_channel()
        if default_ch is not None and default_ch.get("models"):
            models = [m for m in default_ch["models"] if m and m.strip()]
            if models:
                base_cfg = ModelConfig(
                    role=role,
                    models=models,
                    base_url=default_ch.get("base_url", ""),
                    api_key=default_ch.get("api_key", ""),
                    temperature=float(default_ch.get("temperature", 0.7)),
                    max_tokens=int(default_ch.get("max_tokens", 8192)),
                )
                if len(models) > 1:
                    selected = base_cfg.get_model_for_task(user_input, intent)
                    return ModelConfig(
                        role=base_cfg.role,
                        models=[selected],
                        base_url=base_cfg.base_url,
                        api_key=base_cfg.api_key,
                        temperature=base_cfg.temperature,
                        max_tokens=base_cfg.max_tokens,
                    )
                return base_cfg

        # 3. 回退到原有的 default_api / 角色配置逻辑
        base_cfg = self._get_base_config(role)
        if base_cfg is None:
            return None
        if (not model_name or model_name == "auto") and len(base_cfg.models) > 1:
            selected = base_cfg.get_model_for_task(user_input, intent)
            return ModelConfig(
                role=base_cfg.role,
                models=[selected],
                base_url=base_cfg.base_url,
                api_key=base_cfg.api_key,
                temperature=base_cfg.temperature,
                max_tokens=base_cfg.max_tokens,
            )
        return base_cfg

    def _get_base_config(self, role: str) -> ModelConfig | None:
        """获取角色的基础配置（不含模型选择逻辑）"""
        # 独立模式关闭时：统一使用默认 API
        if not self._independent_keys:
            if self._default_api.is_configured():
                return self._default_api
            for r, c in self._models.items():
                if c.is_configured():
                    return c
            return None

        # 独立模式开启时：使用角色配置 + 降级链
        cfg = self._models.get(role)
        if cfg and cfg.is_configured():
            return cfg

        for fallback_role in FALLBACK_CHAIN.get(role, []):
            fallback_cfg = self._models.get(fallback_role)
            if fallback_cfg and fallback_cfg.is_configured():
                return fallback_cfg

        if self._default_api.is_configured():
            return self._default_api

        for r, c in self._models.items():
            if c.is_configured():
                return c

        return None

    def get_default_api(self) -> dict:
        """获取默认 API 配置状态"""
        return {
            "models": list(self._default_api.models),
            "model": self._default_api.model,
            "base_url": self._default_api.base_url,
            "is_configured": self._default_api.is_configured(),
        }

    def get_all_configs(self) -> dict[str, dict]:
        """获取所有配置状态"""
        result = {}
        for role, cfg in self._models.items():
            result[role] = {
                "models": list(cfg.models),
                "model": cfg.model,
                "base_url": cfg.base_url,
                "is_configured": cfg.is_configured(),
                "description": MODEL_ROLES[role],
            }
        return result

    def get_full_config(self) -> dict:
        """获取完整配置，用于前端展示"""
        return {
            "default_api": {
                "models": list(self._default_api.models),
                "model": self._default_api.model,
                "base_url": self._default_api.base_url,
                "api_key": self._default_api.api_key,
                "temperature": self._default_api.temperature,
                "max_tokens": self._default_api.max_tokens,
                "is_configured": self._default_api.is_configured(),
            },
            "independent_keys": self._independent_keys,
            "available_roles": list(MODEL_ROLES.keys()),
            "roles": {
                role: {
                    "models": list(cfg.models),
                    "model": cfg.model,
                    "base_url": cfg.base_url,
                    "api_key": cfg.api_key,
                    "temperature": cfg.temperature,
                    "max_tokens": cfg.max_tokens,
                    "is_configured": cfg.is_configured(),
                    "description": MODEL_ROLES[role],
                }
                for role, cfg in self._models.items()
            },
            "image_config": self._image_config,
            "api_channels": list(self._api_channels),
        }

    def is_any_configured(self) -> bool:
        if self._default_api.is_configured():
            return True
        # 任何 API 渠道已配置也算
        for ch in self._api_channels:
            if (ch.get("models") and ch.get("base_url") and ch.get("api_key")):
                return True
        return any(c.is_configured() for c in self._models.values())

    @property
    def image_config(self) -> dict:
        """图像生成 API 配置"""
        return self._image_config

    @property
    def api_channels(self) -> list[dict]:
        """API 渠道列表"""
        return list(self._api_channels)

    def save_config(self, data: dict):
        """保存配置到 .env 文件

        data 格式:
        {
            "default_api": {"models": ["model1", "model2"], "base_url": "...", "api_key": "..."},
            "independent_keys": True/False,
            "roles": {"TEXT_MASTER": {"models": [...], "base_url": "...", "api_key": "..."}, ...}
        }
        """
        os.makedirs(os.path.dirname(self.env_path), exist_ok=True)
        lines = ["# 墨参 MoShen 配置文件", ""]

        # 默认 API
        default_api = data.get("default_api", {})
        api_channels = data.get("api_channels", [])
        if not isinstance(api_channels, list):
            api_channels = []
        # 过滤无效渠道
        api_channels = [c for c in api_channels if isinstance(c, dict) and c.get("name")]
        lines.append("# API 渠道列表（JSON）")
        lines.append(f"API_CHANNELS={json.dumps(api_channels, ensure_ascii=False)}")
        lines.append("")

        # 默认 API 配置：从标记为默认的渠道同步（若无则取第一个已配置的渠道）
        default_ch = None
        for ch in api_channels:
            if ch.get("is_default"):
                default_ch = ch
                break
        if default_ch is None:
            for ch in api_channels:
                if (ch.get("models") and ch.get("base_url") and ch.get("api_key")):
                    default_ch = ch
                    break

        if default_ch is not None:
            default_models = [m for m in default_ch.get("models", []) if m and m.strip()]
            lines.append("# 默认 API 配置（由默认渠道自动同步）")
            lines.append(f"DEFAULT_API_MODELS={','.join(default_models)}")
            lines.append(f"DEFAULT_API_BASE_URL={default_ch.get('base_url', '')}")
            lines.append(f"DEFAULT_API_KEY={default_ch.get('api_key', '')}")
            lines.append(f"DEFAULT_API_TEMPERATURE={default_ch.get('temperature', 0.7)}")
            lines.append(f"DEFAULT_API_MAX_TOKENS={default_ch.get('max_tokens', 8192)}")
        else:
            # 无渠道时使用前端传入的 default_api（兼容旧数据）
            default_models = default_api.get("models", [])
            default_models = [m for m in default_models if m and m.strip()]
            lines.append("# 默认 API 配置")
            lines.append(f"DEFAULT_API_MODELS={','.join(default_models)}")
            lines.append(f"DEFAULT_API_BASE_URL={default_api.get('base_url', '')}")
            lines.append(f"DEFAULT_API_KEY={default_api.get('api_key', '')}")
            default_temp = default_api.get("temperature")
            if default_temp is not None:
                lines.append(f"DEFAULT_API_TEMPERATURE={default_temp}")
            default_max = default_api.get("max_tokens")
            if default_max is not None:
                lines.append(f"DEFAULT_API_MAX_TOKENS={default_max}")
        lines.append("")

        # 独立开关
        independent = data.get("independent_keys", False)
        lines.append("# 职能独立 API Key 开关")
        lines.append(f"INDEPENDENT_API_KEYS={'True' if independent else 'False'}")
        lines.append("")

        # 四角色配置
        roles = data.get("roles", {})
        for role in MODEL_ROLES:
            role_data = roles.get(role, {})
            role_models = role_data.get("models", [])
            role_models = [m for m in role_models if m and m.strip()]
            lines.append(f"# {role}: {MODEL_ROLES[role]}")
            lines.append(f"{role}_MODELS={','.join(role_models)}")
            lines.append(f"{role}_BASE_URL={role_data.get('base_url', '')}")
            lines.append(f"{role}_API_KEY={role_data.get('api_key', '')}")
            role_temp = role_data.get("temperature")
            if role_temp is not None:
                lines.append(f"{role}_TEMPERATURE={role_temp}")
            role_max = role_data.get("max_tokens")
            if role_max is not None:
                lines.append(f"{role}_MAX_TOKENS={role_max}")
            lines.append("")

        # 图像生成 API 配置
        image_data = data.get("image_config", {})
        lines.append("# 图像生成 API 配置")
        lines.append(f"IMAGE_ENABLED={'True' if image_data.get('enabled') else 'False'}")
        lines.append(f"IMAGE_BASE_URL={image_data.get('base_url', '')}")
        lines.append(f"IMAGE_API_KEY={image_data.get('api_key', '')}")
        lines.append(f"IMAGE_MODEL={image_data.get('model', '')}")
        lines.append(f"IMAGE_SIZE={image_data.get('size', '1024x1024')}")
        lines.append(f"IMAGE_QUALITY={image_data.get('quality', 'auto')}")
        lines.append("")

        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        self.reload()


# 全局单例
_config_manager: ConfigManager | None = None


def get_config_manager() -> ConfigManager:
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager
