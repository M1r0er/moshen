"""
墨参 · 通用工具函数
集中管理时间戳、JSON 解析、路径校验、ID 生成等被多模块复用的逻辑。
"""
import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
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
        pass
    # 容错：尝试从文本中提取 JSON 对象或数组
    # 策略：找第一个 { 或 [，匹配到最后一个 } 或 ]
    for opener, closer in [("{", "}"), ("[", "]")]:
        start = cleaned.find(opener)
        if start < 0:
            continue
        end = cleaned.rfind(closer)
        if end > start:
            fragment = cleaned[start:end + 1]
            try:
                return json.loads(fragment)
            except json.JSONDecodeError:
                continue
    return None


def sanitize_path_name(name: str, error_message: str = "无效的名称") -> str:
    """校验名称，防止路径遍历

    拒绝空字符串、包含 / \\ .. 的输入。
    原 outline.py 与 settings_writer.py 各自定义一份，统一至此。
    """
    if not name or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, error_message)
    return name


def resolve_within(base, *parts, error_message: str = "无效的文件路径") -> Path:
    """将 parts 拼接到 base 之下并解析为绝对路径，确保结果不越出 base。

    用于所有"用户可控名称 → 读/写磁盘路径"的场景，拦截 ../../、绝对路径、
    盘符路径、NUL 字节等导致的路径穿越。

    与 routes/workspace.py 的 read_inspiration_file、project_kb.read_workspace_file
    中的历史写法一致，统一至此供各模块复用。
    允许 base 内部的多级子路径（如 "子目录/文件.txt"），仅拒绝越界。
    """
    base_path = Path(base).resolve()
    for part in parts:
        if "\x00" in str(part):
            raise HTTPException(400, error_message)
    try:
        target = base_path.joinpath(*parts).resolve()
    except (ValueError, OSError) as exc:
        raise HTTPException(400, error_message) from exc
    try:
        target.relative_to(base_path)
    except ValueError:
        raise HTTPException(400, error_message) from None
    return target


def gen_id(prefix: str = "", length: int = 10) -> str:
    """生成短 ID

    Args:
        prefix: 可选前缀（如 "node_"）
        length: hex 部分长度（默认 10）

    原 outline.py 使用 uuid.uuid4().hex[:10]，settings_writer.py 使用 time+md5，
    统一为 uuid.hex 截断，避免基于时间碰撞。
    """
    return f"{prefix}{uuid.uuid4().hex[:length]}"
