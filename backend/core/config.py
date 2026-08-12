"""
墨参 · 多模型配置管理器
支持四角色模型配置，从 .env 文件加载
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
            # 默认从 ~/.moshen/.env 加载
            home = Path.home()
            env_path = str(home / ".moshen" / ".env")
        self.env_path = env_path
        self._models: dict[str, ModelConfig] = {}
        self._load()

    def _load(self):
        """从 .env 文件加载配置"""
        if os.path.exists(self.env_path):
            load_dotenv(self.env_path)

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
        self._models.clear()
        self._load()

    def get_model(self, role: str) -> ModelConfig | None:
        """获取指定角色的模型配置，支持降级"""
        cfg = self._models.get(role)
        if cfg and cfg.is_configured():
            return cfg

        # 降级链查找
        for fallback_role in FALLBACK_CHAIN.get(role, []):
            fallback_cfg = self._models.get(fallback_role)
            if fallback_cfg and fallback_cfg.is_configured():
                return fallback_cfg

        # 最终降级：找任意已配置的模型
        for r, c in self._models.items():
            if c.is_configured():
                return c

        return None

    def get_all_configs(self) -> dict[str, dict]:
        """获取所有配置状态（隐藏 API Key）"""
        result = {}
        for role, cfg in self._models.items():
            result[role] = {
                "model": cfg.model,
                "base_url": cfg.base_url,
                "configured": cfg.is_configured(),
                "description": MODEL_ROLES[role],
            }
        return result

    def is_any_configured(self) -> bool:
        """是否至少有一个模型已配置"""
        return any(c.is_configured() for c in self._models.values())

    def save_config(self, configs: dict[str, dict]):
        """保存配置到 .env 文件"""
        os.makedirs(os.path.dirname(self.env_path), exist_ok=True)
        lines = ["# 墨参 MoShen 配置文件", ""]
        for role, data in configs.items():
            lines.append(f"# {role}: {MODEL_ROLES.get(role, '')}")
            lines.append(f"{role}_MODEL={data.get('model', '')}")
            lines.append(f"{role}_BASE_URL={data.get('base_url', '')}")
            lines.append(f"{role}_API_KEY={data.get('api_key', '')}")
            if "temperature" in data:
                lines.append(f"{role}_TEMPERATURE={data['temperature']}")
            if "max_tokens" in data:
                lines.append(f"{role}_MAX_TOKENS={data['max_tokens']}")
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
