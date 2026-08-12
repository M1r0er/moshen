"""
墨参 · 多模型配置管理器
支持默认API统一调用 + 可选的四角色独立API配置
"""
import os
from pathlib import Path
from dataclasses import dataclass, field
from dotenv import load_dotenv


@dataclass
class ModelConfig:
    """单个模型角色配置"""
    role: str
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    temperature: float = 0.7
    max_tokens: int = 8192

    def is_configured(self) -> bool:
        return bool(self.model and self.base_url and self.api_key)


# 四角色定义
MODEL_ROLES = {
    "TEXT_MASTER": "文学性分析、文风诊断、文笔评估",
    "STRUCTURE_ANALYST": "大纲结构分析、节奏诊断、逻辑检查",
    "KNOWLEDGE_BUILDER": "拆书、知识提取、叙事模式抽象",
    "DIALOGUE_PARTNER": "日常对话、创意讨论、问答",
}

# 角色降级链：首选不可用时按序降级
FALLBACK_CHAIN = {
    "TEXT_MASTER": ["STRUCTURE_ANALYST", "DIALOGUE_PARTNER"],
    "STRUCTURE_ANALYST": ["TEXT_MASTER", "DIALOGUE_PARTNER"],
    "KNOWLEDGE_BUILDER": ["DIALOGUE_PARTNER"],
    "DIALOGUE_PARTNER": ["TEXT_MASTER", "STRUCTURE_ANALYST"],
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

    def _load(self):
        """从 .env 文件加载配置"""
        if os.path.exists(self.env_path):
            load_dotenv(self.env_path, override=True)

        # 加载默认 API
        self._default_api = ModelConfig(
            role="DEFAULT",
            model=os.getenv("DEFAULT_API_MODEL", ""),
            base_url=os.getenv("DEFAULT_API_BASE_URL", ""),
            api_key=os.getenv("DEFAULT_API_KEY", ""),
        )

        # 加载独立开关
        self._independent_keys = os.getenv("INDEPENDENT_API_KEYS", "False").lower() in ("true", "1", "yes")

        # 加载四角色配置
        for role in MODEL_ROLES:
            cfg = ModelConfig(
                role=role,
                model=os.getenv(f"{role}_MODEL", ""),
                base_url=os.getenv(f"{role}_BASE_URL", ""),
                api_key=os.getenv(f"{role}_API_KEY", ""),
                temperature=float(os.getenv(f"{role}_TEMPERATURE", "0.7")),
                max_tokens=int(os.getenv(f"{role}_MAX_TOKENS", "8192")),
            )
            self._models[role] = cfg

    def reload(self):
        """重新加载配置"""
        # 清除 dotenv 缓存
        os.environ.pop("DEFAULT_API_MODEL", None)
        os.environ.pop("DEFAULT_API_BASE_URL", None)
        os.environ.pop("DEFAULT_API_KEY", None)
        os.environ.pop("INDEPENDENT_API_KEYS", None)
        for role in MODEL_ROLES:
            os.environ.pop(f"{role}_MODEL", None)
            os.environ.pop(f"{role}_BASE_URL", None)
            os.environ.pop(f"{role}_API_KEY", None)
            os.environ.pop(f"{role}_TEMPERATURE", None)
            os.environ.pop(f"{role}_MAX_TOKENS", None)
        self._models.clear()
        self._load()

    @property
    def independent_keys(self) -> bool:
        return self._independent_keys

    def get_model(self, role: str) -> ModelConfig | None:
        """获取指定角色的模型配置"""
        # 独立模式关闭时：统一使用默认 API
        if not self._independent_keys:
            if self._default_api.is_configured():
                return self._default_api
            # 默认 API 未配置，尝试降级到任一已配置的角色
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

        # 最终降级：默认 API
        if self._default_api.is_configured():
            return self._default_api

        # 最终降级：任一已配置
        for r, c in self._models.items():
            if c.is_configured():
                return c

        return None

    def get_default_api(self) -> dict:
        """获取默认 API 配置状态"""
        return {
            "model": self._default_api.model,
            "base_url": self._default_api.base_url,
            "is_configured": self._default_api.is_configured(),
        }

    def get_all_configs(self) -> dict[str, dict]:
        """获取所有配置状态（隐藏 API Key 详情）"""
        result = {}
        for role, cfg in self._models.items():
            result[role] = {
                "model": cfg.model,
                "base_url": cfg.base_url,
                "is_configured": cfg.is_configured(),
                "description": MODEL_ROLES[role],
            }
        return result

    def get_full_config(self) -> dict:
        """获取完整配置（包含默认 API、开关、四角色），用于前端展示"""
        return {
            "default_api": {
                "model": self._default_api.model,
                "base_url": self._default_api.base_url,
                "api_key": self._default_api.api_key,
                "is_configured": self._default_api.is_configured(),
            },
            "independent_keys": self._independent_keys,
            "roles": {
                role: {
                    "model": cfg.model,
                    "base_url": cfg.base_url,
                    "api_key": cfg.api_key,
                    "is_configured": cfg.is_configured(),
                    "description": MODEL_ROLES[role],
                }
                for role, cfg in self._models.items()
            },
        }

    def is_any_configured(self) -> bool:
        """是否至少有一个模型已配置"""
        if self._default_api.is_configured():
            return True
        return any(c.is_configured() for c in self._models.values())

    def save_config(self, data: dict):
        """保存配置到 .env 文件

        data 格式:
        {
            "default_api": {"model": "...", "base_url": "...", "api_key": "..."},
            "independent_keys": True/False,
            "roles": {"TEXT_MASTER": {"model": "...", "base_url": "...", "api_key": "..."}, ...}
        }
        """
        os.makedirs(os.path.dirname(self.env_path), exist_ok=True)
        lines = ["# 墨参 MoShen 配置文件", ""]

        # 默认 API
        default_api = data.get("default_api", {})
        lines.append("# 默认 API 配置")
        lines.append(f"DEFAULT_API_MODEL={default_api.get('model', '')}")
        lines.append(f"DEFAULT_API_BASE_URL={default_api.get('base_url', '')}")
        lines.append(f"DEFAULT_API_KEY={default_api.get('api_key', '')}")
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
            lines.append(f"# {role}: {MODEL_ROLES[role]}")
            lines.append(f"{role}_MODEL={role_data.get('model', '')}")
            lines.append(f"{role}_BASE_URL={role_data.get('base_url', '')}")
            lines.append(f"{role}_API_KEY={role_data.get('api_key', '')}")
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
