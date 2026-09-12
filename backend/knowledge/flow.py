"""
墨参 · 创作流程状态机

把"蓝图 → 草稿 → 审稿 → 修稿 → 定稿"从隐性约定变成显式的、可追溯的章节状态：
每个章节在项目中有一个明确的阶段（stage），并记录状态变迁历史。

设计要点（借鉴"创作流程显式化 + 门禁"的思路）：
- 阶段之间只允许**向前推进**（planned → drafted → reviewed → revised → finalized）；
  回退必须显式重置（force），避免误操作悄悄丢失已完成的工作。
- 定稿前的门禁：从 reviewed 进入 revised、从 revised 进入 finalized 才被允许，
  不能越过审稿直接定稿。
"""
import time

from core.safe_io import atomic_write_json, locked_write, read_json

# 阶段定义（顺序即推进方向）
STAGES = ["planned", "drafted", "reviewed", "revised", "finalized"]
STAGE_LABELS = {
    "planned": "已规划",
    "drafted": "草稿",
    "reviewed": "已审稿",
    "revised": "已修稿",
    "finalized": "已定稿",
}

# 允许的"向前一步"推进
_FORWARD = {STAGES[i]: STAGES[i + 1] for i in range(len(STAGES) - 1)}


def stage_index(stage: str) -> int:
    return STAGES.index(stage) if stage in STAGES else -1


def can_transition(current: str | None, target: str) -> tuple[bool, str]:
    """判断阶段变迁是否被允许

    Returns:
        (是否允许, 原因说明)
    """
    if target not in STAGES:
        return False, f"未知阶段: {target}"
    if not current:
        # 无既有状态：只允许从 planned 或 drafted 起步
        if target in ("planned", "drafted"):
            return True, ""
        return False, f"新章节不能直接进入「{STAGE_LABELS[target]}」，请先规划或写作"

    cur_i, tgt_i = stage_index(current), stage_index(target)
    if cur_i < 0:
        return True, ""
    if tgt_i == cur_i:
        return True, ""
    if tgt_i == cur_i + 1:
        return True, ""
    if tgt_i > cur_i:
        # 允许跳级前进，但必须越过审稿：草案不得直接定稿
        if current in ("planned", "drafted") and target == "finalized":
            return False, "草稿需先经过审稿与修稿才能定稿"
        return True, ""
    return False, f"不能从「{STAGE_LABELS.get(current, current)}」回退到「{STAGE_LABELS[target]}」；如需重来请先重置"


class ProjectFlowManager:
    """按项目维护章节创作阶段"""

    def __init__(self, kb):
        self.kb = kb

    def _path(self, project_id: str):
        project_dir = self.kb.get_project_dir(project_id)
        if not project_dir:
            return None
        flow_dir = project_dir / "flow"
        flow_dir.mkdir(exist_ok=True)
        return flow_dir / "flow.json"

    def _load(self, project_id: str) -> dict:
        path = self._path(project_id)
        if not path:
            return {"version": 1, "chapters": {}}
        return read_json(path, {"version": 1, "chapters": {}}) or {"version": 1, "chapters": {}}

    def get_chapter(self, project_id: str, chapter_key: str) -> dict | None:
        return self._load(project_id).get("chapters", {}).get(chapter_key)

    def list_chapters(self, project_id: str) -> dict:
        return self._load(project_id).get("chapters", {})

    @locked_write
    def set_stage(self, project_id: str, chapter_key: str, target: str,
                  note: str = "", force: bool = False) -> dict:
        """推进/设置章节阶段（带门禁）

        Args:
            force: 显式重置（允许回退），用于作者主动重来
        """
        path = self._path(project_id)
        if not path:
            return {"success": False, "error": "项目不存在"}

        data = self._load(project_id)
        chapters = data.setdefault("chapters", {})
        current = (chapters.get(chapter_key) or {}).get("stage")

        if not force:
            allowed, reason = can_transition(current, target)
            if not allowed:
                return {"success": False, "error": reason, "current": current}

        entry = chapters.get(chapter_key) or {"history": []}
        entry["stage"] = target
        entry["stage_label"] = STAGE_LABELS.get(target, target)
        entry["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        if note:
            entry["note"] = note
        entry.setdefault("history", []).append({
            "from": current,
            "to": target,
            "note": note,
            "at": entry["updated_at"],
            "forced": bool(force),
        })
        entry["history"] = entry["history"][-50:]
        chapters[chapter_key] = entry

        atomic_write_json(path, data)
        return {"success": True, "chapter": entry, "current": current}


def get_flow_manager():
    from knowledge.project_kb import get_project_kb_manager
    return ProjectFlowManager(get_project_kb_manager())
