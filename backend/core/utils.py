"""
墨参 · 通用工具函数
集中管理时间戳、JSON 解析、路径校验、ID 生成等被多模块复用的逻辑。
"""
import json
import re
import time
import uuid
from datetime import datetime
from fastapi import HTTPException

# 统一的时间戳格式（保持与原 time.strftime("%Y-%m-%d %H:%M:%S") 完全一致）
_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def now_str() -> str:
    """返回当前本地时间的字符串表示，格式 YYYY-MM-DD HH:MM:SS"""
    return time.strftime(_TS_FORMAT)


def now_iso() -> str:
    """返回 ISO 8601 格式的时间戳（用于元数据字段，兼容旧 datetime.now().isoformat()）"""
    return datetime.now().isoformat()


def parse_json_response(text: str) -> dict | list | None:
    """安全解析 LLM 返回的 JSON 内容

    统一处理：
    - 去除首尾空白
    - 剥离 ``` / ```json 代码块围栏
    - 失败时返回 None（不抛异常）

    原 foreshadowing.py / novel_analyzer.py / intervention.py 各自重复实现，统一至此。
    """
    if not text:
        return None
    cleaned = text.strip()
    # 去除 markdown 代码块围栏
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


def sanitize_path_name(name: str, error_message: str = "无效的名称") -> str:
    """校验名称，防止路径遍历

    拒绝空字符串、包含 / \\ .. 的输入。
    原 outline.py 与 settings_writer.py 各自定义一份，统一至此。
    """
    if not name or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, error_message)
    return name


def gen_id(prefix: str = "", length: int = 10) -> str:
    """生成短 ID

    Args:
        prefix: 可选前缀（如 "node_"）
        length: hex 部分长度（默认 10）

    原 outline.py 使用 uuid.uuid4().hex[:10]，settings_writer.py 使用 time+md5，
    统一为 uuid.hex 截断，避免基于时间碰撞。
    """
    return f"{prefix}{uuid.uuid4().hex[:length]}"
