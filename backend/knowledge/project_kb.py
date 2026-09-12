"""
墨参 · 项目知识库管理器
管理用户创作项目的文件结构和知识库
"""
import os
import json
import time
import shutil
from pathlib import Path

from core.resource_path import get_workspace_dir
from core.utils import now_str
from core.safe_io import atomic_write_json, atomic_write_text, locked_write

# 已完成旧版目录迁移的工作区根路径（避免每次获取管理器时重复扫描磁盘）
_MIGRATED_ROOTS: set[str] = set()


def _read_user_workspace() -> Path:
    """读取用户通过UI选择的工作区路径（~/.moshen/workspace.json）
    
    优先使用用户选择的工作区路径，不存在时回退到默认路径。
    """
    config_path = Path.home() / ".moshen" / "workspace.json"
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            path = data.get("path", "")
            if path:
                p = Path(path)
                if p.exists() and p.is_dir():
                    return p
        except (json.JSONDecodeError, IOError):
            pass
    # 回退到默认路径
    return get_workspace_dir()


# 工作区根目录（动态读取用户选择的工作区）
WORKSPACE_ROOT = _read_user_workspace()

# 知识库文件模板
KB_TEMPLATES = {
    "世界观规则.md": """# 世界观规则

## 力量体系
（描述力量分级、来源、上限、修炼/获取方式）

## 时代背景
（描述故事发生的时代、文化、社会结构）

## 世界硬性禁令
（列出世界观中绝对不可违背的规则）

## 卷纲信息
（各卷的核心设定概要）
""",
    "角色档案.md": """# 角色档案

## 角色格式说明
每个角色包含：
- 角色ID: [C-XXX]
- 灵魂烙印：核心驱动力 + 根本性缺陷 + 绝对行为红线
- 成长弧光：起点 → 关键转折 → 终末锚点
- 关系向量：[关系向量： A -> B | 信任(70)/宿怨(10)]

## 主要角色

### [C-001] 角色名
- 身份：
- 灵魂烙印：
  - 核心驱动力：
  - 根本性缺陷：
  - 绝对行为红线：
- 成长弧光：
  - 起点：
  - 关键转折：
  - 终末锚点：
- 关系向量：
  - [关系向量： → 角色B | 信任(70)/宿怨(10)]
""",
    "档案事件.md": """# 档案事件

## 时代锚点表
（各时代的关键事件）

## 既定事实事件库
（历史中已发生不可更改的事件）

## 实体生命周期锚点
（角色/物品/组织/地点的首现-退场-状态转变）
""",
    "文风样本.md": """# 文风样本

## 风格定位
- 文风类型：（如口语化/古典/简洁/华丽）
- 叙述视角：（如第三人称限制视角）
- 情感基调：（如热血/沉重/轻松）

## 样本文本
（粘贴2-3段你认为风格典型的已写正文，覆盖战斗/对话/景物等场景）

## 风格基因（系统自动提取）
- 词汇偏好：
- 句式偏好：
- 节奏特征：
""",
    "伏笔台账.md": """# 伏笔台账

## 伏笔格式
每个伏笔包含：
- 伏笔ID: [F-XXX]
- 级别: Tier-1(战略级) / Tier-2(战役级) / Tier-3(战术级)
- 埋设位置: 第X卷第Y章
- 内容: 伏笔描述
- 状态: 未回收 / 已回收(第X卷第Y章) / 沉睡(超过50章未引用)
- 计划回收时机:

## Tier-1 战略级伏笔

## Tier-2 战役级伏笔

## Tier-3 战术级伏笔
""",
    "战略宏图.md": """# 战略宏图

## 核心冲突
（故事的核心矛盾是什么）

## 哲学母题
（贯穿全书的核心辩题，如"秩序与自由的冲突"）

## 各卷宏图
### 第一卷
- 主题：
- 核心事件：
- 终局锚点：

## 宏观节奏宪章
| 阶段 | 章节范围 | 核心使命 | 缓冲比 |
|------|---------|---------|--------|
| 起 | - | - | - |
| 承 | - | - | - |
| 转 | - | - | - |
| 合 | - | - | - |
""",
}


class ProjectKBManager:
    """项目知识库管理器"""

    def __init__(self, workspace_root: str | None = None):
        if workspace_root:
            self.root = Path(workspace_root)
        else:
            # 动态读取用户选择的工作区路径
            self.root = _read_user_workspace()
        self.root.mkdir(parents=True, exist_ok=True)
        self.migrate_legacy_project_layout()

    def get_project_root_dir(self, project_id: str) -> Path:
        """获取项目根目录（无论是否已存在），统一的项目数据边界

        历史遗留问题：设定与大纲曾存放在 {workspace}/projects/{proj_id}/ 下，
        而其余项目数据在 {workspace}/{proj_id}/ 下，导致项目数据被拆散在两处。
        现统一以 {workspace}/{proj_id}/ 为唯一项目目录。
        """
        return self.root / project_id

    def get_settings_dir(self, project_id: str) -> Path:
        """获取项目设定目录（统一路径：{workspace}/{proj_id}/settings）"""
        return self.get_project_root_dir(project_id) / "settings"

    def get_outline_path(self, project_id: str) -> Path:
        """获取项目大纲文件路径（统一路径：{workspace}/{proj_id}/outline.json）"""
        return self.get_project_root_dir(project_id) / "outline.json"

    def migrate_legacy_project_layout(self) -> list[str]:
        """把旧版 {workspace}/projects/{proj_id}/ 下的 settings、outline.json

        迁移到统一的项目目录 {workspace}/projects/{proj_id}/ -> {workspace}/{proj_id}/，
        使设定、大纲与其余项目数据落在同一目录下。迁移是幂等的：
        仅当目标位置不存在对应数据时才移动，孤儿目录（无对应项目）保持原样。

        Returns:
            被迁移的项目 ID 列表
        """
        legacy_root = self.root / "projects"
        if not legacy_root.exists() or not legacy_root.is_dir():
            return []

        migrated = []
        for legacy_proj in list(legacy_root.iterdir()):
            if not legacy_proj.is_dir() or legacy_proj.name.startswith("."):
                continue
            project_id = legacy_proj.name
            target_root = self.root / project_id
            # 仅迁移真实存在的项目，孤儿目录不动
            if not target_root.exists():
                continue

            moved = False

            legacy_settings = legacy_proj / "settings"
            target_settings = target_root / "settings"
            if legacy_settings.exists() and not target_settings.exists():
                try:
                    shutil.move(str(legacy_settings), str(target_settings))
                    moved = True
                except OSError:
                    pass

            legacy_outline = legacy_proj / "outline.json"
            target_outline = target_root / "outline.json"
            if legacy_outline.exists() and not target_outline.exists():
                try:
                    shutil.move(str(legacy_outline), str(target_outline))
                    moved = True
                except OSError:
                    pass

            if moved:
                migrated.append(project_id)

            # 清理迁移后残留的空目录
            try:
                if not any(legacy_proj.iterdir()):
                    legacy_proj.rmdir()
            except OSError:
                pass

        return migrated

    @locked_write
    def create_project(self, name: str, description: str = "") -> dict:
        """创建新项目"""
        import hashlib
        # 使用名称的 hash 前缀避免中文目录名问题
        name_hash = hashlib.md5(name.encode("utf-8")).hexdigest()[:8]
        project_id = f"proj_{int(time.time())}_{name_hash}"
        project_dir = self.root / project_id
        project_dir.mkdir(parents=True, exist_ok=True)

        # 创建子目录（使用拼音安全命名）
        (project_dir / "manuscripts").mkdir(exist_ok=True)
        (project_dir / "dialogue_history").mkdir(exist_ok=True)
        (project_dir / "reports").mkdir(exist_ok=True)

        # 创建知识库文件
        for filename, template in KB_TEMPLATES.items():
            filepath = project_dir / filename
            if not filepath.exists():
                atomic_write_text(filepath, template)

        # 创建项目元数据
        meta = {
            "project_id": project_id,
            "name": name,
            "description": description,
            "created_at": now_str(),
            "updated_at": now_str(),
            "chapters": [],
            "total_words": 0,
            "workspace_path": "",  # 项目独立工作区路径（可选）
        }
        atomic_write_json(project_dir / "project.json", meta)

        return meta

    def list_projects(self) -> list[dict]:
        """列出所有项目"""
        projects = []
        if not self.root.exists():
            return projects

        for item in self.root.iterdir():
            if item.is_dir() and item.name.startswith("proj_"):
                meta_path = item / "project.json"
                if meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text(encoding="utf-8"))
                        projects.append(meta)
                    except (json.JSONDecodeError, IOError):
                        pass

        projects.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return projects

    def get_project(self, project_id: str) -> dict | None:
        """获取项目信息"""
        meta_path = self.root / project_id / "project.json"
        if not meta_path.exists():
            return None
        return json.loads(meta_path.read_text(encoding="utf-8"))

    def get_project_dir(self, project_id: str) -> Path | None:
        """获取项目目录路径"""
        d = self.root / project_id
        return d if d.exists() else None

    def get_project_workspace(self, project_id: str) -> str:
        """获取项目的独立工作区路径（如果设置了）"""
        meta = self.get_project(project_id)
        if meta:
            return meta.get("workspace_path", "")
        return ""

    @locked_write
    def set_project_workspace(self, project_id: str, path: str) -> dict | None:
        """设置项目的独立工作区路径"""
        project_dir = self.get_project_dir(project_id)
        if not project_dir:
            return None

        # 验证路径
        if path:
            p = Path(path).expanduser().resolve()
            if not p.exists():
                return None
            path = str(p)

        meta = self.get_project(project_id)
        if not meta:
            return None

        meta["workspace_path"] = path
        meta["updated_at"] = now_str()
        atomic_write_json(project_dir / "project.json", meta)
        return meta

    def list_workspace_files(self, project_id: str) -> list[dict]:
        """列出项目独立工作区中的文件（递归，最多2层）"""
        ws_path = self.get_project_workspace(project_id)
        if not ws_path:
            return []

        ws = Path(ws_path)
        if not ws.exists() or not ws.is_dir():
            return []

        def _scan(directory: Path, depth: int, max_depth: int = 2) -> list[dict]:
            items = []
            try:
                for child in sorted(directory.iterdir(), key=lambda x: (not x.is_dir(), x.name)):
                    if child.name.startswith("."):
                        continue
                    node = {
                        "name": child.name,
                        "path": str(child),
                        "relative_path": str(child.relative_to(ws)),
                        "type": "dir" if child.is_dir() else "file",
                        "size": child.stat().st_size if child.is_file() else None,
                    }
                    if child.is_dir() and depth < max_depth:
                        node["children"] = _scan(child, depth + 1, max_depth)
                    items.append(node)
            except PermissionError:
                pass
            return items

        return _scan(ws, depth=1)

    def read_workspace_file(self, project_id: str, relative_path: str) -> str | None:
        """读取项目工作区中的文件内容"""
        ws_path = self.get_project_workspace(project_id)
        if not ws_path:
            return None

        ws = Path(ws_path)
        filepath = (ws / relative_path).resolve()

        # 安全检查：确保文件在工作区内
        try:
            filepath.relative_to(ws.resolve())
        except ValueError:
            return None

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

    @staticmethod
    def read_file_summary(filepath: Path) -> str | None:
        """读取文件内容用于摘要，支持 .docx/.txt/.md 等

        公开方法，供 routes/files.py、routes/settings_writer.py 等外部模块复用，
        避免各自重复实现 docx/编码检测逻辑。
        """
        ext = filepath.suffix.lower()
        if ext in (".docx", ".doc"):
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

    def get_project_summary(self, project_id: str) -> str:
        """获取项目知识库摘要（用于 LLM 上下文），包含知识库模板、设定树和上传文档"""
        project_dir = self.get_project_dir(project_id)
        if not project_dir:
            return ""

        parts = []

        # 1. 知识库模板文件
        for filename in KB_TEMPLATES:
            filepath = project_dir / filename
            if filepath.exists():
                content = filepath.read_text(encoding="utf-8").strip()
                if content and not content.endswith("（描述力量分级、来源、上限、修炼/获取方式）"):
                    if len(content) > 2000:
                        content = content[:2000] + "\n...(内容已截断)"
                    parts.append(f"### {filename}\n{content}")

        # 2. 设定树
        settings_tree_path = project_dir / "settings" / "settings_tree.json"
        if settings_tree_path.exists():
            try:
                tree = json.loads(settings_tree_path.read_text(encoding="utf-8"))
                settings_text = self._format_settings_tree_for_context(tree.get("nodes", []))
                if settings_text:
                    parts.append(f"### 设定树\n{settings_text}")
            except (json.JSONDecodeError, IOError):
                pass

        # 3. 上传文档摘要
        upload_dir = project_dir / "uploads"
        if upload_dir.exists():
            file_summaries = []
            for f in upload_dir.iterdir():
                if f.is_file() and not f.name.startswith("."):
                    try:
                        content = self.read_file_summary(f)
                        if content:
                            summary = content[:1500]
                            if len(content) > 1500:
                                summary += "\n...(内容已截断)"
                            file_summaries.append(f"#### {f.name}\n{summary}")
                    except Exception:
                        pass
            if file_summaries:
                parts.append(f"### 上传文档\n" + "\n\n".join(file_summaries))

        # 4. 项目独立工作区文档
        ws_path = self.get_project_workspace(project_id)
        if ws_path:
            ws = Path(ws_path)
            if ws.exists() and ws.is_dir():
                ws_summaries = []
                for f in ws.rglob("*"):
                    if f.is_file() and not f.name.startswith(".") and f.suffix.lower() in (".txt", ".md", ".doc", ".docx"):
                        try:
                            rel_path = f.relative_to(ws)
                            content = self.read_file_summary(f)
                            if content:
                                summary = content[:1500]
                                if len(content) > 1500:
                                    summary += "\n...(内容已截断)"
                                ws_summaries.append(f"#### {rel_path}\n{summary}")
                                if len(ws_summaries) >= 10:
                                    break
                        except Exception:
                            pass
                if ws_summaries:
                    parts.append(f"### 项目工作区文档 ({ws_path})\n" + "\n\n".join(ws_summaries))

        if not parts:
            return "（项目知识库为空，请通过对话逐步构建世界观、角色、大纲等内容）"

        return "\n\n---\n\n".join(parts)

    def _format_settings_tree_for_context(self, nodes: list, level: int = 1) -> str:
        """将设定树格式化为上下文文本"""
        lines = []
        for node in nodes:
            indent = "  " * (level - 1)
            title = node.get("title", "")
            content = node.get("content", "").strip()
            if content:
                # 截取前 800 字
                if len(content) > 800:
                    content = content[:800] + "..."
                lines.append(f"{indent}- **{title}**: {content[:200]}...")
            else:
                lines.append(f"{indent}- **{title}**")
            children = node.get("children", [])
            if children:
                child_text = self._format_settings_tree_for_context(children, level + 1)
                if child_text:
                    lines.append(child_text)
        return "\n".join(lines)

    def read_kb_file(self, project_id: str, filename: str) -> str | None:
        """读取知识库文件"""
        filepath = self.root / project_id / filename
        if not filepath.exists():
            return None
        return filepath.read_text(encoding="utf-8")

    def write_kb_file(self, project_id: str, filename: str, content: str) -> bool:
        """写入知识库文件"""
        filepath = self.root / project_id / filename
        if not filepath.parent.exists():
            return False
        atomic_write_text(filepath, content)
        self._update_project_timestamp(project_id)
        return True

    def list_kb_files(self, project_id: str) -> list[dict]:
        """列出项目知识库文件"""
        project_dir = self.get_project_dir(project_id)
        if not project_dir:
            return []

        files = []
        for filename in KB_TEMPLATES:
            filepath = project_dir / filename
            if filepath.exists():
                stat = filepath.stat()
                content = filepath.read_text(encoding="utf-8")
                has_content = len(content.strip()) > 100  # 超过模板初始内容
                files.append({
                    "filename": filename,
                    "size": stat.st_size,
                    "has_content": has_content,
                    "char_count": len(content),
                })
        return files

    def save_uploaded_file(self, project_id: str, filename: str, content: str) -> str:
        """保存上传的文件到项目目录"""
        project_dir = self.get_project_dir(project_id)
        if not project_dir:
            raise FileNotFoundError("项目不存在")

        upload_dir = project_dir / "uploads"
        upload_dir.mkdir(exist_ok=True)

        filepath = upload_dir / filename
        atomic_write_text(filepath, content)
        self._update_project_timestamp(project_id)
        return str(filepath)

    def list_uploaded_files(self, project_id: str) -> list[dict]:
        """列出已上传文件"""
        project_dir = self.get_project_dir(project_id)
        if not project_dir:
            return []

        upload_dir = project_dir / "uploads"
        if not upload_dir.exists():
            return []

        files = []
        for f in upload_dir.iterdir():
            if f.is_file():
                stat = f.stat()
                files.append({
                    "filename": f.name,
                    "size": stat.st_size,
                    "ext": f.suffix.lower(),
                    "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                })
        return files

    def save_chapter(self, project_id: str, volume: int, chapter: int, content: str) -> bool:
        """保存章节稿件"""
        project_dir = self.get_project_dir(project_id)
        if not project_dir:
            return False

        vol_dir = project_dir / "manuscripts" / f"vol_{volume:02d}"
        vol_dir.mkdir(parents=True, exist_ok=True)

        chapter_file = vol_dir / f"ch_{chapter:03d}.md"
        atomic_write_text(chapter_file, content)

        # 更新项目元数据
        meta = self.get_project(project_id)
        if meta:
            chapter_key = f"vol{volume}_ch{chapter}"
            if chapter_key not in [c.get("key") for c in meta.get("chapters", [])]:
                meta.setdefault("chapters", []).append({
                    "key": chapter_key,
                    "volume": volume,
                    "chapter": chapter,
                    "words": len(content),
                })
            meta["total_words"] = sum(c.get("words", 0) for c in meta["chapters"])
            meta["updated_at"] = now_str()
            atomic_write_json(project_dir / "project.json", meta)

        return True

    def list_chapters(self, project_id: str) -> list[dict]:
        """列出所有章节"""
        meta = self.get_project(project_id)
        if not meta:
            return []
        return meta.get("chapters", [])

    # ===== 写作系统 =====

    def _get_writing_dir(self, project_id: str) -> Path | None:
        """获取写作目录"""
        project_dir = self.get_project_dir(project_id)
        if not project_dir:
            return None
        wdir = project_dir / "writing"
        wdir.mkdir(exist_ok=True)
        return wdir

    def _get_writing_index_path(self, project_id: str) -> Path | None:
        wdir = self._get_writing_dir(project_id)
        if not wdir:
            return None
        return wdir / "writing.json"

    def _load_writing_index(self, project_id: str) -> dict:
        """加载写作索引（卷+章的元数据）"""
        idx_path = self._get_writing_index_path(project_id)
        if not idx_path or not idx_path.exists():
            return {"version": 1, "volumes": []}
        try:
            return json.loads(idx_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            return {"version": 1, "volumes": []}

    def _save_writing_index(self, project_id: str, data: dict) -> bool:
        idx_path = self._get_writing_index_path(project_id)
        if not idx_path:
            return False
        atomic_write_json(idx_path, data)
        return True

    def get_writing_index(self, project_id: str) -> dict:
        """获取写作索引（全部卷+章元数据）"""
        return self._load_writing_index(project_id)

    @locked_write
    def create_volume(self, project_id: str, number: int | None = None, title: str = "") -> dict | None:
        """创建新卷"""
        data = self._load_writing_index(project_id)
        vols = data.get("volumes", [])
        if number is None:
            number = len(vols) + 1
        vol_id = f"vol_{number:03d}"
        # 检查是否已存在
        for v in vols:
            if v.get("number") == number:
                return v
        vol = {
            "id": vol_id,
            "number": number,
            "title": title or f"第{number}卷",
            "chapters": [],
            "created_at": now_str(),
            "updated_at": now_str(),
        }
        vols.append(vol)
        vols.sort(key=lambda v: v.get("number", 0))
        data["volumes"] = vols
        self._save_writing_index(project_id, data)
        return vol

    @locked_write
    def update_volume(self, project_id: str, vol_id: str, number: int | None = None, title: str | None = None) -> dict | None:
        """更新卷信息"""
        data = self._load_writing_index(project_id)
        for v in data.get("volumes", []):
            if v["id"] == vol_id:
                if number is not None:
                    v["number"] = number
                    v["id"] = f"vol_{number:03d}"
                if title is not None:
                    v["title"] = title
                v["updated_at"] = now_str()
                self._save_writing_index(project_id, data)
                return v
        return None

    @locked_write
    def delete_volume(self, project_id: str, vol_id: str) -> bool:
        """删除卷（连同所有章节）"""
        data = self._load_writing_index(project_id)
        vols = data.get("volumes", [])
        target = None
        for v in vols:
            if v["id"] == vol_id:
                target = v
                break
        if not target:
            return False
        # 删除章节文件目录
        wdir = self._get_writing_dir(project_id)
        if wdir:
            vol_dir = wdir / vol_id
            if vol_dir.exists():
                import shutil
                shutil.rmtree(vol_dir, ignore_errors=True)
        # 从索引移除
        data["volumes"] = [v for v in vols if v["id"] != vol_id]
        self._save_writing_index(project_id, data)
        return True

    @locked_write
    def create_chapter(self, project_id: str, vol_id: str,
                       number: int | None = None, title: str = "",
                       numbering_mode: str = "continue") -> dict | None:
        """创建新章节

        Args:
            numbering_mode: "continue" 全局连续编号, "per_volume" 每卷从1开始
        """
        data = self._load_writing_index(project_id)
        vol = None
        for v in data.get("volumes", []):
            if v["id"] == vol_id:
                vol = v
                break
        if not vol:
            return None

        if number is None:
            if numbering_mode == "per_volume":
                # 本卷最大章号 +1
                existing = [c.get("number", 0) for c in vol.get("chapters", [])]
                number = max(existing) + 1 if existing else 1
            else:
                # 全局最大章号 +1
                all_nums = []
                for vv in data.get("volumes", []):
                    all_nums.extend([c.get("number", 0) for c in vv.get("chapters", [])])
                number = max(all_nums) + 1 if all_nums else 1

        ch_id = f"ch_{number:04d}"
        # 避免重号
        existing_ids = [c.get("id") for c in vol.get("chapters", [])]
        if ch_id in existing_ids:
            # 找一个不重复的
            base = number
            while ch_id in existing_ids:
                base += 1
                ch_id = f"ch_{base:04d}"
            number = base

        now = now_str()
        chapter = {
            "id": ch_id,
            "number": number,
            "title": title or f"第{number}章",
            "words": 0,
            "created_at": now,
            "updated_at": now,
        }
        vol.setdefault("chapters", []).append(chapter)
        vol["chapters"].sort(key=lambda c: c.get("number", 0))
        vol["updated_at"] = now
        self._save_writing_index(project_id, data)

        # 创建空文件
        wdir = self._get_writing_dir(project_id)
        if wdir:
            ch_dir = wdir / vol_id
            ch_dir.mkdir(exist_ok=True)
            ch_file = ch_dir / f"{ch_id}.html"
            atomic_write_text(ch_file, "")

        return chapter

    def get_chapter_content(self, project_id: str, vol_id: str, ch_id: str) -> str | None:
        """读取章节正文内容（HTML）"""
        wdir = self._get_writing_dir(project_id)
        if not wdir:
            return None
        ch_file = wdir / vol_id / f"{ch_id}.html"
        if not ch_file.exists():
            return ""
        try:
            return ch_file.read_text(encoding="utf-8")
        except IOError:
            return ""

    @locked_write
    def save_chapter_content(self, project_id: str, vol_id: str, ch_id: str,
                             content: str, title: str | None = None) -> dict | None:
        """保存章节内容"""
        wdir = self._get_writing_dir(project_id)
        if not wdir:
            return None
        ch_file = wdir / vol_id / f"{ch_id}.html"
        ch_file.parent.mkdir(exist_ok=True)
        atomic_write_text(ch_file, content)

        # 计算字数（去除 HTML 标签）
        import re
        plain = re.sub(r"<[^>]+>", "", content)
        words = len(plain)

        # 更新索引
        data = self._load_writing_index(project_id)
        now = now_str()
        for v in data.get("volumes", []):
            if v["id"] == vol_id:
                for c in v.get("chapters", []):
                    if c["id"] == ch_id:
                        c["words"] = words
                        c["updated_at"] = now
                        if title is not None:
                            c["title"] = title
                v["updated_at"] = now
        self._save_writing_index(project_id, data)

        # 返回更新后的章节信息
        for v in data.get("volumes", []):
            if v["id"] == vol_id:
                for c in v.get("chapters", []):
                    if c["id"] == ch_id:
                        return c
        return None

    @locked_write
    def update_chapter(self, project_id: str, vol_id: str, ch_id: str,
                       number: int | None = None, title: str | None = None) -> dict | None:
        """更新章节元数据（标题/编号）"""
        data = self._load_writing_index(project_id)
        now = now_str()
        for v in data.get("volumes", []):
            if v["id"] == vol_id:
                for c in v.get("chapters", []):
                    if c["id"] == ch_id:
                        if number is not None:
                            old_id = c["id"]
                            c["number"] = number
                            new_id = f"ch_{number:04d}"
                            c["id"] = new_id
                            # 重命名文件
                            wdir = self._get_writing_dir(project_id)
                            if wdir:
                                old_file = wdir / vol_id / f"{old_id}.html"
                                new_file = wdir / vol_id / f"{new_id}.html"
                                if old_file.exists():
                                    old_file.rename(new_file)
                        if title is not None:
                            c["title"] = title
                        c["updated_at"] = now
                        v["updated_at"] = now
                        v["chapters"].sort(key=lambda cc: cc.get("number", 0))
                        self._save_writing_index(project_id, data)
                        return c
        return None

    @locked_write
    def delete_chapter(self, project_id: str, vol_id: str, ch_id: str) -> bool:
        """删除章节"""
        data = self._load_writing_index(project_id)
        for v in data.get("volumes", []):
            if v["id"] == vol_id:
                before = len(v.get("chapters", []))
                v["chapters"] = [c for c in v.get("chapters", []) if c["id"] != ch_id]
                if len(v["chapters"]) == before:
                    return False
                v["updated_at"] = now_str()
                self._save_writing_index(project_id, data)
                # 删除文件
                wdir = self._get_writing_dir(project_id)
                if wdir:
                    ch_file = wdir / vol_id / f"{ch_id}.html"
                    if ch_file.exists():
                        ch_file.unlink()
                return True
        return False

    def get_all_chapters_text(self, project_id: str) -> list[dict]:
        """获取所有章节的纯文本内容（用于全文伏笔检测）"""
        data = self._load_writing_index(project_id)
        result = []
        import re
        for v in data.get("volumes", []):
            for c in v.get("chapters", []):
                content = self.get_chapter_content(project_id, v["id"], c["id"]) or ""
                plain = re.sub(r"<[^>]+>", "", content)
                result.append({
                    "vol_id": v["id"],
                    "vol_number": v.get("number"),
                    "vol_title": v.get("title"),
                    "ch_id": c["id"],
                    "ch_number": c.get("number"),
                    "ch_title": c.get("title"),
                    "content": plain,
                    "words": c.get("words", 0),
                })
        return result

    @locked_write
    def save_diagnosis_report(self, project_id: str, report: str) -> str:
        """保存诊断报告"""
        project_dir = self.get_project_dir(project_id)
        if not project_dir:
            return ""

        report_dir = project_dir / "reports"
        report_dir.mkdir(exist_ok=True)

        filename = f"report_{time.strftime('%Y%m%d_%H%M%S')}.md"
        filepath = report_dir / filename
        atomic_write_text(filepath, report)
        return filename

    def list_diagnosis_reports(self, project_id: str) -> list[dict]:
        """列出诊断报告"""
        project_dir = self.get_project_dir(project_id)
        if not project_dir:
            return []

        report_dir = project_dir / "reports"
        if not report_dir.exists():
            return []

        reports = []
        for f in report_dir.iterdir():
            if f.is_file() and f.suffix == ".md":
                stat = f.stat()
                reports.append({
                    "filename": f.name,
                    "created": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                    "size": stat.st_size,
                })
        reports.sort(key=lambda x: x["created"], reverse=True)
        return reports

    def save_dialogue_history(self, project_id: str, messages: list[dict]):
        """保存对话历史"""
        project_dir = self.get_project_dir(project_id)
        if not project_dir:
            return

        history_dir = project_dir / "dialogue_history"
        history_dir.mkdir(exist_ok=True)

        filename = f"session_{time.strftime('%Y%m%d')}.json"
        filepath = history_dir / filename
        atomic_write_json(filepath, messages)

    def _update_project_timestamp(self, project_id: str):
        """更新项目时间戳"""
        meta = self.get_project(project_id)
        if meta:
            meta["updated_at"] = now_str()
            project_dir = self.get_project_dir(project_id)
            if project_dir:
                atomic_write_json(project_dir / "project.json", meta)

    def rename_project(self, project_id: str, new_name: str) -> dict | None:
        """重命名项目"""
        if not new_name.strip():
            return None
        meta = self.get_project(project_id)
        if not meta:
            return None
        meta["name"] = new_name.strip()
        meta["updated_at"] = now_str()
        project_dir = self.get_project_dir(project_id)
        if project_dir:
            atomic_write_json(project_dir / "project.json", meta)
        return meta

    def delete_project(self, project_id: str) -> bool:
        """删除项目（删除整个目录）"""
        project_dir = self.get_project_dir(project_id)
        if not project_dir:
            return False
        import shutil
        shutil.rmtree(project_dir, ignore_errors=True)
        return True

    # ===== 多对话管理 =====

    def _get_conversations_dir(self, project_id: str) -> Path | None:
        """获取对话目录"""
        project_dir = self.get_project_dir(project_id)
        if not project_dir:
            return None
        conv_dir = project_dir / "conversations"
        conv_dir.mkdir(exist_ok=True)
        return conv_dir

    def create_conversation(self, project_id: str, title: str = "") -> dict:
        """创建新对话"""
        conv_dir = self._get_conversations_dir(project_id)
        if not conv_dir:
            return None

        import hashlib
        conv_id = f"conv_{int(time.time())}_{hashlib.md5(str(time.time()).encode()).hexdigest()[:6]}"
        now = now_str()
        conv = {
            "id": conv_id,
            "title": title or "新对话",
            "messages": [],
            "created_at": now,
            "updated_at": now,
        }
        filepath = conv_dir / f"{conv_id}.json"
        atomic_write_json(filepath, conv)
        return conv

    def list_conversations(self, project_id: str) -> list[dict]:
        """列出项目的所有对话（仅元数据，不含消息）"""
        conv_dir = self._get_conversations_dir(project_id)
        if not conv_dir:
            return []

        conversations = []
        for f in conv_dir.iterdir():
            if f.is_file() and f.suffix == ".json":
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    conversations.append({
                        "id": data.get("id", f.stem),
                        "title": data.get("title", "未命名"),
                        "message_count": len(data.get("messages", [])),
                        "created_at": data.get("created_at", ""),
                        "updated_at": data.get("updated_at", ""),
                    })
                except (json.JSONDecodeError, IOError):
                    pass

        # 按更新时间降序
        conversations.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return conversations

    def get_conversation(self, project_id: str, conv_id: str) -> dict | None:
        """获取对话完整内容（含消息）"""
        conv_dir = self._get_conversations_dir(project_id)
        if not conv_dir:
            return None

        filepath = conv_dir / f"{conv_id}.json"
        if not filepath.exists():
            return None

        try:
            return json.loads(filepath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            return None

    @locked_write
    def save_conversation(self, project_id: str, conv_id: str,
                          messages: list[dict], title: str | None = None) -> dict | None:
        """保存对话消息"""
        conv_dir = self._get_conversations_dir(project_id)
        if not conv_dir:
            return None

        filepath = conv_dir / f"{conv_id}.json"
        if not filepath.exists():
            return None

        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            data = {"id": conv_id, "title": "未命名", "messages": []}

        data["messages"] = messages
        if title is not None:
            data["title"] = title
        data["updated_at"] = now_str()

        atomic_write_json(filepath, data)
        return data

    @locked_write
    def delete_conversation(self, project_id: str, conv_id: str) -> bool:
        """删除对话"""
        conv_dir = self._get_conversations_dir(project_id)
        if not conv_dir:
            return False

        filepath = conv_dir / f"{conv_id}.json"
        if filepath.exists():
            filepath.unlink()
            return True
        return False

    @locked_write
    def rename_conversation(self, project_id: str, conv_id: str, title: str) -> dict | None:
        """重命名对话"""
        conv_dir = self._get_conversations_dir(project_id)
        if not conv_dir:
            return None

        filepath = conv_dir / f"{conv_id}.json"
        if not filepath.exists():
            return None

        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            return None

        data["title"] = title.strip() or "未命名"
        data["updated_at"] = now_str()
        atomic_write_json(filepath, data)
        return data

    @locked_write
    def import_conversation(self, project_id: str, conv_data: dict) -> dict | None:
        """导入会话（从导出的 JSON 创建新会话，生成新 ID 避免冲突）"""
        conv_dir = self._get_conversations_dir(project_id)
        if not conv_dir:
            return None

        import hashlib
        conv_id = f"conv_{int(time.time())}_{hashlib.md5(str(time.time()).encode()).hexdigest()[:6]}"
        now = now_str()
        conv = {
            "id": conv_id,
            "title": conv_data.get("title", "导入的对话"),
            "messages": conv_data.get("messages", []),
            "created_at": conv_data.get("created_at", now),
            "updated_at": now,
        }
        filepath = conv_dir / f"{conv_id}.json"
        atomic_write_json(filepath, conv)
        return conv

    # ===== 伏笔系统 =====

    def _get_foreshadowing_path(self, project_id: str) -> Path | None:
        project_dir = self.get_project_dir(project_id)
        if not project_dir:
            return None
        fdir = project_dir / "foreshadowing"
        fdir.mkdir(exist_ok=True)
        return fdir / "foreshadowing.json"

    def _load_foreshadowing(self, project_id: str) -> dict:
        fpath = self._get_foreshadowing_path(project_id)
        if not fpath or not fpath.exists():
            return {"version": 1, "foreshadowings": []}
        try:
            return json.loads(fpath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            return {"version": 1, "foreshadowings": []}

    def _save_foreshadowing(self, project_id: str, data: dict) -> bool:
        fpath = self._get_foreshadowing_path(project_id)
        if not fpath:
            return False
        atomic_write_json(fpath, data)
        return True

    def list_foreshadowings(self, project_id: str) -> list[dict]:
        """列出所有伏笔（摘要，不含 entries 详情）"""
        data = self._load_foreshadowing(project_id)
        result = []
        for f in data.get("foreshadowings", []):
            entries = f.get("entries", [])
            result.append({
                "id": f["id"],
                "name": f.get("name", ""),
                "status": f.get("status", "active"),  # active | resolved
                "entry_count": len(entries),
                "first_chapter": entries[0].get("chapter_title", "") if entries else "",
                "last_chapter": entries[-1].get("chapter_title", "") if entries else "",
                "resolution_chapter": f.get("resolution_chapter", ""),
                "created_at": f.get("created_at", ""),
                "updated_at": f.get("updated_at", ""),
            })
        # 先按更新时间倒序，再按状态做稳定排序（未回收在前）。
        # Python 的排序是稳定的，因此同一状态内仍保持"更新时间倒序"。
        result.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        result.sort(key=lambda x: 0 if x["status"] == "active" else 1)
        return result

    def get_foreshadowing(self, project_id: str, f_id: str) -> dict | None:
        """获取单个伏笔详情（含所有 entries）"""
        data = self._load_foreshadowing(project_id)
        for f in data.get("foreshadowings", []):
            if f["id"] == f_id:
                return f
        return None

    @locked_write
    def create_foreshadowing(self, project_id: str, name: str,
                             content: str = "", chapter_id: str = "",
                             chapter_title: str = "") -> dict | None:
        """创建新伏笔（首条 entry）"""
        data = self._load_foreshadowing(project_id)
        import hashlib
        f_id = f"fs_{int(time.time())}_{hashlib.md5(name.encode()).hexdigest()[:6]}"
        now = now_str()
        entry_id = f"e_{int(time.time()*1000)}"
        fs = {
            "id": f_id,
            "name": name,
            "status": "active",
            "resolution_chapter": "",
            "source": "author",  # 来源标注：伏笔由作者维护
            "entries": [{
                "id": entry_id,
                "content": content,
                "chapter_id": chapter_id,
                "chapter_title": chapter_title,
                "source": "author",
                "created_at": now,
            }],
            "created_at": now,
            "updated_at": now,
        }
        data.setdefault("foreshadowings", []).append(fs)
        self._save_foreshadowing(project_id, data)
        return fs

    @locked_write
    def update_foreshadowing(self, project_id: str, f_id: str,
                             name: str | None = None,
                             status: str | None = None,
                             resolution_chapter: str | None = None) -> dict | None:
        """更新伏笔基本信息"""
        data = self._load_foreshadowing(project_id)
        for f in data.get("foreshadowings", []):
            if f["id"] == f_id:
                if name is not None:
                    f["name"] = name
                if status is not None:
                    f["status"] = status
                if resolution_chapter is not None:
                    f["resolution_chapter"] = resolution_chapter
                f["updated_at"] = now_str()
                self._save_foreshadowing(project_id, data)
                return f
        return None

    @locked_write
    def delete_foreshadowing(self, project_id: str, f_id: str) -> bool:
        """删除伏笔"""
        data = self._load_foreshadowing(project_id)
        before = len(data.get("foreshadowings", []))
        data["foreshadowings"] = [f for f in data.get("foreshadowings", []) if f["id"] != f_id]
        if len(data["foreshadowings"]) == before:
            return False
        self._save_foreshadowing(project_id, data)
        return True

    @locked_write
    def add_entry(self, project_id: str, f_id: str,
                  content: str, chapter_id: str = "",
                  chapter_title: str = "") -> dict | None:
        """给伏笔添加一条出现记录"""
        data = self._load_foreshadowing(project_id)
        for f in data.get("foreshadowings", []):
            if f["id"] == f_id:
                entry_id = f"e_{int(time.time()*1000)}"
                now = now_str()
                entry = {
                    "id": entry_id,
                    "content": content,
                    "chapter_id": chapter_id,
                    "chapter_title": chapter_title,
                    "source": "author",
                    "created_at": now,
                }
                f.setdefault("entries", []).append(entry)
                f["updated_at"] = now
                self._save_foreshadowing(project_id, data)
                return entry
        return None

    @locked_write
    def remove_entry(self, project_id: str, f_id: str, entry_id: str) -> bool:
        """删除伏笔的一条出现记录"""
        data = self._load_foreshadowing(project_id)
        for f in data.get("foreshadowings", []):
            if f["id"] == f_id:
                before = len(f.get("entries", []))
                f["entries"] = [e for e in f.get("entries", []) if e["id"] != entry_id]
                if len(f["entries"]) == before:
                    return False
                f["updated_at"] = now_str()
                self._save_foreshadowing(project_id, data)
                return True
        return False


# 全局单例
_kb_manager: ProjectKBManager | None = None


def get_project_kb_manager() -> ProjectKBManager:
    global _kb_manager
    if _kb_manager is None:
        _kb_manager = ProjectKBManager()
    else:
        # 每次获取时刷新工作区路径（用户可能通过UI切换了工作区）
        _kb_manager.root = _read_user_workspace()
        _kb_manager.root.mkdir(parents=True, exist_ok=True)
        # 工作区切换后执行一次旧版目录迁移（避免每次调用都扫描磁盘）
        root_key = str(_kb_manager.root)
        if root_key not in _MIGRATED_ROOTS:
            _MIGRATED_ROOTS.add(root_key)
            _kb_manager.migrate_legacy_project_layout()
    return _kb_manager
