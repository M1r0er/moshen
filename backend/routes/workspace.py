"""
墨参 · 工作区管理路由
用户可以选择本地文件夹作为工作区，所有项目文件和知识库都存放在工作区中。
工作区配置存储在 ~/.moshen/workspace.json
"""
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/workspace", tags=["workspace"])

# 用户配置目录 ~/.moshen/
MOSHEN_HOME = Path.home() / ".moshen"
WORKSPACE_CONFIG = MOSHEN_HOME / "workspace.json"
DEFAULT_WORKSPACE = MOSHEN_HOME / "workspace"


def get_workspace_path() -> Path:
    """获取当前工作区路径（从 ~/.moshen/workspace.json 读取，不存在则用默认路径）"""
    if WORKSPACE_CONFIG.exists():
        try:
            data = json.loads(WORKSPACE_CONFIG.read_text(encoding="utf-8"))
            path = data.get("path", "")
            if path:
                return Path(path)
        except (json.JSONDecodeError, IOError):
            pass
    return DEFAULT_WORKSPACE


def read_text_safe(filepath: Path) -> str:
    """安全读取文本文件（UTF-8 优先，回退 GB18030）"""
    raw = filepath.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("gb18030", errors="replace")


def write_text_safe(filepath: Path, content: str):
    """写入文本文件（UTF-8）"""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content, encoding="utf-8")


class SelectWorkspaceRequest(BaseModel):
    path: str


@router.get("")
async def get_workspace():
    """获取当前工作区信息（路径、是否存在）"""
    ws = get_workspace_path()
    return {
        "path": str(ws),
        "exists": ws.exists(),
        "is_default": str(ws) == str(DEFAULT_WORKSPACE),
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
    for subdir in ("projects", "knowledge"):
        sub = path / subdir
        if not sub.exists():
            sub.mkdir(parents=True, exist_ok=True)
            created_dirs.append(subdir)

    # 保存配置
    MOSHEN_HOME.mkdir(parents=True, exist_ok=True)
    WORKSPACE_CONFIG.write_text(
        json.dumps({"path": str(path)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

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
