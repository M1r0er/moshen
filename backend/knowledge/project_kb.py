"""
墨参 · 项目知识库管理器
管理用户创作项目的文件结构和知识库
"""
import os
import json
import time
from pathlib import Path

from core.resource_path import get_workspace_dir


# 工作区根目录
WORKSPACE_ROOT = get_workspace_dir()

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
            self.root = WORKSPACE_ROOT
        self.root.mkdir(parents=True, exist_ok=True)

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
                filepath.write_text(template, encoding="utf-8")

        # 创建项目元数据
        meta = {
            "project_id": project_id,
            "name": name,
            "description": description,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "chapters": [],
            "total_words": 0,
        }
        (project_dir / "project.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

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

    def get_project_summary(self, project_id: str) -> str:
        """获取项目知识库摘要（用于 LLM 上下文）"""
        project_dir = self.get_project_dir(project_id)
        if not project_dir:
            return ""

        parts = []
        for filename in KB_TEMPLATES:
            filepath = project_dir / filename
            if filepath.exists():
                content = filepath.read_text(encoding="utf-8").strip()
                # 只包含有实际内容的文件（非模板）
                if content and not content.endswith("（描述力量分级、来源、上限、修炼/获取方式）"):
                    # 截取前 2000 字
                    if len(content) > 2000:
                        content = content[:2000] + "\n...(内容已截断)"
                    parts.append(f"### {filename}\n{content}")

        if not parts:
            return "（项目知识库为空，请通过对话逐步构建世界观、角色、大纲等内容）"

        return "\n\n---\n\n".join(parts)

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
        filepath.write_text(content, encoding="utf-8")
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
        filepath.write_text(content, encoding="utf-8")
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
        chapter_file.write_text(content, encoding="utf-8")

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
            meta["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            (project_dir / "project.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        return True

    def list_chapters(self, project_id: str) -> list[dict]:
        """列出所有章节"""
        meta = self.get_project(project_id)
        if not meta:
            return []
        return meta.get("chapters", [])

    def save_diagnosis_report(self, project_id: str, report: str) -> str:
        """保存诊断报告"""
        project_dir = self.get_project_dir(project_id)
        if not project_dir:
            return ""

        report_dir = project_dir / "reports"
        report_dir.mkdir(exist_ok=True)

        filename = f"report_{time.strftime('%Y%m%d_%H%M%S')}.md"
        filepath = report_dir / filename
        filepath.write_text(report, encoding="utf-8")
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
        filepath.write_text(
            json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _update_project_timestamp(self, project_id: str):
        """更新项目时间戳"""
        meta = self.get_project(project_id)
        if meta:
            meta["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            project_dir = self.get_project_dir(project_id)
            if project_dir:
                (project_dir / "project.json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
                )


# 全局单例
_kb_manager: ProjectKBManager | None = None


def get_project_kb_manager() -> ProjectKBManager:
    global _kb_manager
    if _kb_manager is None:
        _kb_manager = ProjectKBManager()
    return _kb_manager
