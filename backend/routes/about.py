"""
墨参 · 关于与反馈路由

提供四类能力：
1. 版本与环境信息（关于面板展示）
2. 更新说明：解析随包分发的 CHANGELOG.md，按版本返回要点（用于「本次更新」悬浮窗）
3. 新版本检查：查询 GitHub 公共 API 的最新发布版本（失败静默，不阻塞启动）
4. 诊断信息 / 诊断包：仅在用户主动点击时读取本地文件

隐私约定：本模块不自动上报任何数据。查询更新时只发送一个匿名 GET 请求，不带任何本地信息。
"""
import json
import platform
import re
import sys
import time
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException

from core.version import APP_VERSION
from routes.workspace import MOSHEN_HOME, get_workspace_path, ui_prefs

router = APIRouter(prefix="/api/about", tags=["about"])

FEEDBACK_EMAIL = "3191639408@qq.com"
GITHUB_REPO = "M1r0er/moshen"
LOG_DIR = MOSHEN_HOME / "logs"
DIAG_DIR = MOSHEN_HOME / "diagnostics"

_UPDATE_TTL = 6 * 3600          # 更新检查结果缓存 6 小时，避免频繁请求
_MAX_DIAG_BYTES = 12 * 1024 * 1024
_MAX_LOG_INLINE = 3 * 1024 * 1024
_update_cache: dict = {"at": 0.0, "data": None}


def _changelog_path() -> Path:
    """CHANGELOG.md 位置：开发环境在仓库根目录，打包后在解压目录根"""
    if getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS) / "CHANGELOG.md"
    return Path(__file__).parent.parent.parent / "CHANGELOG.md"


def _parse_changelog(text: str) -> dict:
    """按 '## vX.Y.Z' 切分更新日志，返回 {版本号: [{group, items}]}"""
    sections: dict = {}
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            title = line[3:].strip()
            matched = re.match(r"^v(\d+\.\d+\.\d+)$", title)
            current = matched.group(1) if matched else None
            if current:
                sections[current] = []
            continue
        if current is None:
            continue
        if line.startswith("### "):
            sections[current].append({"group": line[4:].strip(), "items": []})
            continue
        if line.startswith("- "):
            if not sections[current]:
                sections[current].append({"group": "", "items": []})
            sections[current][-1]["items"].append(line[2:].strip())
    return sections


def _version_tuple(value: str) -> tuple:
    try:
        parts = value.strip().lstrip("vV").split(".")
        return tuple(int(p) for p in parts[:3]) + (0,) * (3 - len(parts[:3]))
    except ValueError:
        return (0, 0, 0)


def _log_files() -> list:
    if not LOG_DIR.exists():
        return []
    files = [p for p in LOG_DIR.rglob("*") if p.is_file()]
    try:
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        pass
    return files


def _tail(path: Path, max_lines: int) -> list:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    return [ln.rstrip("\n") for ln in lines[-max_lines:]]


def _is_packaged() -> bool:
    return bool(getattr(sys, "_MEIPASS", None))


def _env_summary() -> list:
    return [
        f"墨参版本: v{APP_VERSION}",
        f"运行方式: {'打包版' if _is_packaged() else '源码运行'}",
        f"操作系统: {platform.platform()}",
        f"Python: {platform.python_version()}",
        f"配置目录: {MOSHEN_HOME}",
        f"工作区: {get_workspace_path()}",
        f"日志目录: {LOG_DIR}",
        f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]


def _diagnostic_text() -> str:
    """环境信息 + 最近日志尾部摘录（供用户自行复制粘贴）"""
    parts = ["===== 环境信息 ====="]
    parts.extend(_env_summary())
    parts.append("")
    parts.append("===== 最近日志（尾部摘录）=====")
    logs = _log_files()[:3]
    if not logs:
        parts.append("（无日志文件）")
    for path in logs:
        parts.append(f"--- {path.name} ---")
        parts.extend(_tail(path, 40))
        parts.append("")
    return "\n".join(parts)


def _workspace_tree(root: Path, max_entries: int = 400, max_depth: int = 3) -> list:
    """列出工作区目录结构：仅目录名、文件名与大小，不含任何文件内容"""
    out: list = []

    def walk(path: Path, depth: int):
        if depth > max_depth or len(out) >= max_entries:
            return
        try:
            items = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
        except OSError:
            return
        for item in items:
            if len(out) >= max_entries:
                return
            if item.name.startswith("."):
                continue
            indent = "  " * depth
            if item.is_dir():
                out.append(f"{indent}[目录] {item.name}")
                walk(item, depth + 1)
            else:
                try:
                    out.append(f"{indent}{item.name}  ({item.stat().st_size} B)")
                except OSError:
                    out.append(f"{indent}{item.name}")

    if root.exists():
        walk(root, 0)
    return out


# ===== 版本与环境信息 =====

@router.get("")
async def about_info():
    """关于面板所需的版本与环境信息"""
    return {
        "version": APP_VERSION,
        "email": FEEDBACK_EMAIL,
        "repo": GITHUB_REPO,
        "releases_url": f"https://github.com/{GITHUB_REPO}/releases",
        "issues_url": f"https://github.com/{GITHUB_REPO}/issues/new/choose",
        "os": platform.platform(),
        "python": platform.python_version(),
        "packaged": _is_packaged(),
        "log_dir": str(LOG_DIR),
        "workspace": str(get_workspace_path()),
        "prefs": ui_prefs(),
    }


@router.get("/changelog")
async def changelog(version: str = ""):
    """返回指定版本的更新说明；version 为空时取当前版本"""
    target = (version or APP_VERSION).strip().lstrip("vV")
    path = _changelog_path()
    if not path.exists():
        return {"version": target, "found": False, "blocks": []}
    try:
        blocks = _parse_changelog(path.read_text(encoding="utf-8")).get(target, [])
    except OSError:
        return {"version": target, "found": False, "blocks": []}
    blocks = [b for b in blocks if b.get("items")]
    return {"version": target, "found": bool(blocks), "blocks": blocks}


# ===== 新版本检查 =====

@router.get("/update-check")
def update_check():
    """查询 GitHub 最新发布版本（同步函数，FastAPI 会放到线程池，不阻塞事件循环）"""
    if not ui_prefs().get("check_update", True):
        return {"enabled": False, "has_update": False}

    now = time.time()
    cached = _update_cache["data"]
    if cached and now - _update_cache["at"] < _UPDATE_TTL:
        return cached

    result = {
        "enabled": True,
        "has_update": False,
        "current": APP_VERSION,
        "latest": "",
        "title": "",
        "url": "",
        "error": "",
    }
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            headers={
                "User-Agent": f"MoShen/{APP_VERSION}",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        latest = str(data.get("tag_name", "")).strip().lstrip("vV")
        result["latest"] = latest
        result["title"] = data.get("name") or ""
        result["url"] = data.get("html_url") or ""
        result["has_update"] = _version_tuple(latest) > _version_tuple(APP_VERSION)
    except Exception:
        # 网络不可用（含境内访问 GitHub 受限）：静默失败，前端不做任何提示
        result["error"] = "unavailable"

    _update_cache["at"] = now
    _update_cache["data"] = result
    return result


# ===== 诊断信息与诊断包 =====

@router.get("/diagnostics/text")
async def diagnostics_text():
    """诊断信息文本，由用户自行复制发送"""
    return {"text": _diagnostic_text()}


@router.post("/diagnostics/export")
async def diagnostics_export():
    """导出诊断包（zip）：环境信息、日志、工作区文件清单；不含正文内容与 API Key"""
    try:
        DIAG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = DIAG_DIR / f"moshen-diagnostics-{stamp}.zip"
        total = 0
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("环境信息与日志摘录.txt", _diagnostic_text())
            tree = _workspace_tree(get_workspace_path())
            zf.writestr(
                "工作区文件清单.txt",
                "仅列出目录与文件名，不含任何文件内容。\n\n" + "\n".join(tree),
            )
            for path in _log_files():
                if total >= _MAX_DIAG_BYTES:
                    break
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                total += size
                try:
                    if size > _MAX_LOG_INLINE:
                        zf.writestr(f"logs/{path.name}", "\n".join(_tail(path, 4000)))
                    else:
                        zf.write(path, f"logs/{path.name}")
                except OSError:
                    continue
        return {
            "success": True,
            "path": str(out),
            "dir": str(DIAG_DIR),
            "size": out.stat().st_size,
        }
    except Exception as exc:  # noqa: BLE001 - 导出失败需把原因返回给用户
        raise HTTPException(500, f"导出诊断包失败: {exc}")
