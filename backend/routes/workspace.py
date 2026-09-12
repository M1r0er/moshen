"""
墨参 · 工作区管理路由
用户可以选择本地文件夹作为工作区，所有项目文件和知识库都存放在工作区中。
工作区配置存储在 ~/.moshen/workspace.json
"""
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.safe_io import atomic_write_json, atomic_write_text, locked_write

router = APIRouter(prefix="/api/workspace", tags=["workspace"])

# 用户配置目录 ~/.moshen/
MOSHEN_HOME = Path.home() / ".moshen"
WORKSPACE_CONFIG = MOSHEN_HOME / "workspace.json"
DEFAULT_WORKSPACE = MOSHEN_HOME / "workspace"


def _read_config() -> dict:
    """读取 ~/.moshen/workspace.json 配置"""
    if WORKSPACE_CONFIG.exists():
        try:
            return json.loads(WORKSPACE_CONFIG.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_config(data: dict):
    """保存配置到 ~/.moshen/workspace.json（原子写）"""
    atomic_write_json(WORKSPACE_CONFIG, data)


def get_workspace_path() -> Path:
    """获取当前工作区路径（从 ~/.moshen/workspace.json 读取，不存在则用默认路径）"""
    data = _read_config()
    path = data.get("path", "")
    if path:
        return Path(path)
    return DEFAULT_WORKSPACE


def get_inspiration_path() -> str:
    """获取灵感文件夹路径，未设置则返回空字符串"""
    data = _read_config()
    return data.get("inspiration_path", "")


def read_file_safe(filepath: Path) -> str | None:
    """读取文件内容，支持 .docx/.txt/.md 等"""
    if not filepath.exists() or not filepath.is_file():
        return None
    ext = filepath.suffix.lower()
    if ext == ".docx":
        try:
            from core.file_parser import FileParser
            return FileParser.read_docx(str(filepath))
        except Exception:
            return None
    raw = filepath.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("gb18030", errors="replace")


def read_text_safe(filepath: Path) -> str:
    """安全读取文本文件（UTF-8 优先，回退 GB18030）"""
    raw = filepath.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("gb18030", errors="replace")


def write_text_safe(filepath: Path, content: str):
    """写入文本文件（UTF-8，原子写）"""
    atomic_write_text(filepath, content)


class SelectWorkspaceRequest(BaseModel):
    path: str


@router.get("")
async def get_workspace():
    """获取当前工作区信息（路径、是否存在、灵感文件夹路径）"""
    ws = get_workspace_path()
    insp = get_inspiration_path()
    return {
        "path": str(ws),
        "exists": ws.exists(),
        "is_default": str(ws) == str(DEFAULT_WORKSPACE),
        "inspiration_path": insp,
    }


@router.post("/select")
async def select_workspace(req: SelectWorkspaceRequest):
    """设置工作区路径，验证路径存在并创建必要的子目录"""
    path = Path(req.path).expanduser().resolve()
    if not path.exists():
        raise HTTPException(400, f"路径不存在: {path}")
    if not path.is_dir():
        raise HTTPException(400, f"路径不是目录: {path}")

    # 创建必要的子目录
    created_dirs = []
    for subdir in ("knowledge",):
        sub = path / subdir
        if not sub.exists():
            sub.mkdir(parents=True, exist_ok=True)
            created_dirs.append(subdir)

    # 保存配置（保留灵感文件夹路径）
    config = _read_config()
    config["path"] = str(path)
    _save_config(config)

    return {
        "success": True,
        "path": str(path),
        "created_dirs": created_dirs,
    }


@router.get("/tree")
async def get_workspace_tree():
    """获取工作区文件树（列出目录结构，最多2层深度）"""

    def build_tree(path: Path, depth: int, max_depth: int) -> list[dict]:
        """递归构建文件树"""
        items = []
        if depth > max_depth:
            return items
        try:
            for child in sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name)):
                # 跳过隐藏文件
                if child.name.startswith("."):
                    continue
                node = {
                    "name": child.name,
                    "path": str(child),
                    "type": "dir" if child.is_dir() else "file",
                    "size": child.stat().st_size if child.is_file() else None,
                }
                if child.is_dir() and depth < max_depth:
                    node["children"] = build_tree(child, depth + 1, max_depth)
                items.append(node)
        except PermissionError:
            pass
        return items

    ws = get_workspace_path()
    if not ws.exists():
        return {"path": str(ws), "tree": []}

    tree = build_tree(ws, depth=1, max_depth=2)
    return {"path": str(ws), "tree": tree}


# ===== 灵感文件夹 =====

class InspirationPathRequest(BaseModel):
    path: str


class InspirationReadRequest(BaseModel):
    relative_path: str


SUPPORTED_EXTS = {".txt", ".md", ".docx", ".doc", ".markdown", ".csv", ".json"}


@router.get("/inspiration")
async def get_inspiration():
    """获取灵感文件夹路径和文件列表"""
    insp = get_inspiration_path()
    if not insp:
        return {"path": "", "files": [], "exists": False}
    p = Path(insp)
    if not p.exists() or not p.is_dir():
        return {"path": insp, "files": [], "exists": False}

    files = []
    try:
        for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name)):
            if child.name.startswith("."):
                continue
            if child.is_file():
                ext = child.suffix.lower()
                if ext not in SUPPORTED_EXTS:
                    continue
                files.append({
                    "name": child.name,
                    "relative_path": child.name,
                    "size": child.stat().st_size,
                    "ext": ext,
                    "modified": child.stat().st_mtime,
                })
            elif child.is_dir():
                files.append({
                    "name": child.name,
                    "relative_path": child.name,
                    "type": "dir",
                    "ext": "",
                    "modified": child.stat().st_mtime,
                })
    except PermissionError:
        pass

    return {"path": insp, "files": files, "exists": True}


@router.put("/inspiration")
async def set_inspiration(req: InspirationPathRequest):
    """设置灵感文件夹路径"""
    if not req.path.strip():
        config = _read_config()
        config.pop("inspiration_path", None)
        _save_config(config)
        return {"success": True, "path": ""}

    path = Path(req.path).expanduser().resolve()
    if not path.exists():
        raise HTTPException(400, f"路径不存在: {path}")
    if not path.is_dir():
        raise HTTPException(400, f"路径不是目录: {path}")

    config = _read_config()
    config["inspiration_path"] = str(path)
    _save_config(config)

    return {"success": True, "path": str(path)}


@router.post("/inspiration/read")
async def read_inspiration_file(req: InspirationReadRequest):
    """读取灵感文件夹中的文件内容"""
    insp = get_inspiration_path()
    if not insp:
        raise HTTPException(400, "未设置灵感文件夹")

    base = Path(insp)
    filepath = (base / req.relative_path).resolve()

    try:
        filepath.relative_to(base.resolve())
    except ValueError:
        raise HTTPException(400, "无效的文件路径")

    content = read_file_safe(filepath)
    if content is None:
        raise HTTPException(404, "文件不存在或无法读取")

    return {"relative_path": req.relative_path, "content": content, "name": filepath.name}


# ===== 写作偏好设置 =====

class WritingPrefsRequest(BaseModel):
    chapter_numbering_mode: str | None = None  # continue | per_volume
    auto_foreshadowing_detect: bool | None = None


@router.get("/prefs/writing")
async def get_writing_prefs():
    """获取写作偏好设置"""
    data = _read_config()
    prefs = data.get("writing_prefs", {})
    return {
        "chapter_numbering_mode": prefs.get("chapter_numbering_mode", "continue"),
        "auto_foreshadowing_detect": prefs.get("auto_foreshadowing_detect", False),
    }


@router.put("/prefs/writing")
async def set_writing_prefs(req: WritingPrefsRequest):
    """更新写作偏好设置"""
    data = _read_config()
    prefs = data.get("writing_prefs", {})
    if req.chapter_numbering_mode is not None:
        prefs["chapter_numbering_mode"] = req.chapter_numbering_mode
    if req.auto_foreshadowing_detect is not None:
        prefs["auto_foreshadowing_detect"] = req.auto_foreshadowing_detect
    data["writing_prefs"] = prefs
    _save_config(data)
    return {"success": True, "prefs": prefs}


# ===== 界面语言 / 项目写作语言 =====
# 两者相互独立：界面语言只影响 UI 文案；项目写作语言决定模型用什么语言创作。
# 作者原文与引用资料在任何语言下都保持原样。

UI_LOCALES = ("zh-CN", "en-US")
WRITING_LANGUAGES = ("zh-CN", "en-US")


class I18nPrefsRequest(BaseModel):
    ui_locale: str | None = None
    writing_language: str | None = None


def get_ui_locale() -> str:
    """获取界面语言"""
    return _read_config().get("i18n", {}).get("ui_locale", "zh-CN")


def get_writing_language() -> str:
    """获取项目写作语言"""
    return _read_config().get("i18n", {}).get("writing_language", "zh-CN")


@router.get("/prefs/i18n")
async def get_i18n_prefs():
    """获取界面语言与写作语言设置"""
    return {
        "ui_locale": get_ui_locale(),
        "writing_language": get_writing_language(),
        "ui_locales": list(UI_LOCALES),
        "writing_languages": list(WRITING_LANGUAGES),
    }


@router.put("/prefs/i18n")
async def set_i18n_prefs(req: I18nPrefsRequest):
    """更新界面语言 / 写作语言"""
    data = _read_config()
    prefs = data.get("i18n", {})
    if req.ui_locale is not None and req.ui_locale in UI_LOCALES:
        prefs["ui_locale"] = req.ui_locale
    if req.writing_language is not None and req.writing_language in WRITING_LANGUAGES:
        prefs["writing_language"] = req.writing_language
    data["i18n"] = prefs
    _save_config(data)
    return {"success": True, "prefs": prefs}
