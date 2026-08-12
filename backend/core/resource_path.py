"""
墨参 · 资源路径解析工具
兼容开发环境和 PyInstaller 打包环境
"""
import sys
import os
from pathlib import Path


def get_bundle_dir() -> Path:
    """获取资源根目录

    开发环境：backend/ 目录
    PyInstaller 打包环境：sys._MEIPASS（_internal 目录）
    """
    if getattr(sys, '_MEIPASS', None):
        # PyInstaller 打包环境
        return Path(sys._MEIPASS)
    else:
        # 开发环境：backend/ 目录
        return Path(__file__).parent.parent


def get_resource_path(*parts: str) -> Path:
    """获取资源文件路径

    Args:
        *parts: 相对于资源根目录的路径片段

    Returns:
        完整的资源路径

    开发环境：
        get_resource_path("frontend") -> backend/../frontend
        get_resource_path("prompts") -> backend/prompts
    打包环境：
        get_resource_path("frontend") -> _MEIPASS/frontend
        get_resource_path("prompts") -> _MEIPASS/prompts
    """
    return get_bundle_dir().joinpath(*parts)


def get_frontend_dir() -> Path:
    """获取前端文件目录

    开发环境：项目根目录/frontend（与 backend 同级）
    打包环境：_MEIPASS/frontend
    """
    if getattr(sys, '_MEIPASS', None):
        # 打包环境
        return Path(sys._MEIPASS) / "frontend"
    else:
        # 开发环境：frontend 与 backend 同级，在项目根目录下
        # __file__ = backend/core/resource_path.py
        # parent.parent.parent = 项目根目录
        return Path(__file__).parent.parent.parent / "frontend"


def get_prompts_dir() -> Path:
    """获取提示词模板目录"""
    return get_resource_path("prompts")


def get_workspace_dir() -> Path:
    """获取工作区目录（用户数据，需要可写）

    打包环境使用用户目录 ~/.moshen/workspace/
    开发环境使用项目目录 workspace/
    """
    if getattr(sys, '_MEIPASS', None):
        # 打包环境：使用用户目录，确保可写
        home = Path.home()
        ws = home / ".moshen" / "workspace"
        ws.mkdir(parents=True, exist_ok=True)
        return ws
    else:
        # 开发环境
        ws = Path(__file__).parent.parent.parent / "workspace"
        ws.mkdir(parents=True, exist_ok=True)
        return ws


def get_config_dir() -> Path:
    """获取配置文件目录（用户数据，需要可写）"""
    if getattr(sys, '_MEIPASS', None):
        # 打包环境
        cfg = Path.home() / ".moshen"
        cfg.mkdir(parents=True, exist_ok=True)
        return cfg
    else:
        # 开发环境
        return Path(__file__).parent.parent
