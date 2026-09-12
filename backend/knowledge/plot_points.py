"""
墨参 · 爽爆点库

作者为某一段剧情或副本预先安排的爽点、爆点、钩子等"情绪弹药"，
并跟踪每个点已经用过几次，避免同一套路反复使用造成审美疲劳。

数据存放在项目目录下 plot_points/plot_points.json：
{
  "version": 1,
  "fatigue_threshold": 3,
  "points": [ {id, title, type, trope, content, segment, status,
               used_count, fatigue_threshold, planned_count,
               usage_notes: [{at, note, chapter_key}], source,
               created_at, updated_at} ]
}
"""

import time

from core.safe_io import atomic_write_json, locked_write, read_json
from core.utils import gen_id, now_str

# 爽爆点类型
PP_TYPES = ["爽点", "爆点", "钩子", "情绪点", "悬念", "转折", "其他"]

# 状态
PP_STATUSES = ["pending", "writing", "used", "archived"]
PP_STATUS_LABELS = {
    "pending": "待写",
    "writing": "写作中",
    "used": "已用",
    "archived": "归档",
}

DEFAULT_FATIGUE_THRESHOLD = 3


def _norm_int(value, default: int = 0) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, n)


class PlotPointManager:
    """按项目维护爽爆点库"""

    def __init__(self, kb):
        self.kb = kb

    def _path(self, project_id: str):
        project_dir = self.kb.get_project_dir(project_id)
        if not project_dir:
            return None
        pp_dir = project_dir / "plot_points"
        pp_dir.mkdir(exist_ok=True)
        return pp_dir / "plot_points.json"

    def _load(self, project_id: str) -> dict:
        path = self._path(project_id)
        if not path:
            return {"version": 1, "fatigue_threshold": DEFAULT_FATIGUE_THRESHOLD, "points": []}
        data = read_json(path, None)
        if not isinstance(data, dict):
            data = {"version": 1, "fatigue_threshold": DEFAULT_FATIGUE_THRESHOLD, "points": []}
        data.setdefault("version", 1)
        data.setdefault("fatigue_threshold", DEFAULT_FATIGUE_THRESHOLD)
        data.setdefault("points", [])
        return data

    def _threshold(self, data: dict) -> int:
        return _norm_int(data.get("fatigue_threshold"), DEFAULT_FATIGUE_THRESHOLD) or DEFAULT_FATIGUE_THRESHOLD

    def _item_threshold(self, point: dict, default: int) -> int:
        v = point.get("fatigue_threshold")
        n = _norm_int(v, 0)
        return n if n > 0 else default

    # ===== 读取 =====

    def list_points(self, project_id: str) -> dict:
        """返回全部爽爆点与阈值"""
        data = self._load(project_id)
        points = sorted(
            data.get("points", []),
            key=lambda p: p.get("updated_at", ""),
            reverse=True,
        )
        return {
            "points": points,
            "fatigue_threshold": self._threshold(data),
            "types": PP_TYPES,
            "statuses": PP_STATUSES,
            "status_labels": PP_STATUS_LABELS,
        }

    def stats(self, project_id: str) -> dict:
        """按类型与套路统计已用次数，并给出审美疲劳提示"""
        data = self._load(project_id)
        default = self._threshold(data)
        by_type: dict[str, dict] = {}
        by_trope: dict[str, dict] = {}
        warnings: list[str] = []

        for p in data.get("points", []):
            used = _norm_int(p.get("used_count"))
            if used <= 0:
                continue
            thr = self._item_threshold(p, default)
            for bucket, key in ((by_type, (p.get("type") or "其他").strip()), (by_trope, (p.get("trope") or "").strip())):
                if not key:
                    continue
                entry = bucket.setdefault(key, {"used": 0, "count": 0})
                entry["used"] += used
                entry["count"] += 1

        def warn(bucket: dict[str, dict], label: str) -> None:
            for key, entry in bucket.items():
                if entry["used"] >= default:
                    warnings.append(f"{label}「{key}」累计已用 {entry['used']} 次（阈值 {default}），建议换用其他手法")

        warn(by_type, "类型")
        warn(by_trope, "套路")

        return {
            "fatigue_threshold": default,
            "by_type": by_type,
            "by_trope": by_trope,
            "warnings": warnings,
        }

    def get_point(self, project_id: str, pp_id: str) -> dict | None:
        for p in self._load(project_id).get("points", []):
            if p.get("id") == pp_id:
                return p
        return None

    # ===== 写入 =====

    @locked_write
    def create_point(self, project_id: str, payload: dict) -> dict | None:
        path = self._path(project_id)
        if not path:
            return None
        title = str(payload.get("title", "")).strip()
        if not title:
            return None

        data = self._load(project_id)
        now = now_str()
        point = {
            "id": gen_id("pp_"),
            "title": title,
            "type": (str(payload.get("type", "")).strip() or "爽点"),
            "trope": str(payload.get("trope", "")).strip(),
            "content": str(payload.get("content", "")).strip(),
            "segment": str(payload.get("segment", "")).strip(),
            "status": payload.get("status") if payload.get("status") in PP_STATUSES else "pending",
            "used_count": _norm_int(payload.get("used_count")),
            "fatigue_threshold": _norm_int(payload.get("fatigue_threshold")) or None,
            "planned_count": _norm_int(payload.get("planned_count")) or None,
            "usage_notes": [],
            "source": payload.get("source") if payload.get("source") in ("author", "ai") else "author",
            "created_at": now,
            "updated_at": now,
        }
        data.setdefault("points", []).append(point)
        atomic_write_json(path, data)
        return point

    @locked_write
    def update_point(self, project_id: str, pp_id: str, fields: dict) -> dict | None:
        path = self._path(project_id)
        if not path:
            return None
        data = self._load(project_id)
        for p in data.get("points", []):
            if p.get("id") != pp_id:
                continue
            if "title" in fields and str(fields["title"]).strip():
                p["title"] = str(fields["title"]).strip()
            if "type" in fields and str(fields["type"]).strip():
                p["type"] = str(fields["type"]).strip()
            if "trope" in fields:
                p["trope"] = str(fields["trope"]).strip()
            if "content" in fields:
                p["content"] = str(fields["content"]).strip()
            if "segment" in fields:
                p["segment"] = str(fields["segment"]).strip()
            if "status" in fields and fields["status"] in PP_STATUSES:
                p["status"] = fields["status"]
            if "used_count" in fields:
                p["used_count"] = _norm_int(fields["used_count"])
            if "fatigue_threshold" in fields:
                p["fatigue_threshold"] = _norm_int(fields["fatigue_threshold"]) or None
            if "planned_count" in fields:
                p["planned_count"] = _norm_int(fields["planned_count"]) or None
            p["updated_at"] = now_str()
            atomic_write_json(path, data)
            return p
        return None

    @locked_write
    def add_usage(self, project_id: str, pp_id: str, delta: int = 1,
                  set_to: int | None = None, note: str = "",
                  chapter_key: str = "") -> dict | None:
        """记录一次使用：delta 增减，或 set_to 直接设定已用次数"""
        path = self._path(project_id)
        if not path:
            return None
        data = self._load(project_id)
        for p in data.get("points", []):
            if p.get("id") != pp_id:
                continue
            if set_to is not None:
                new_value = _norm_int(set_to)
            else:
                new_value = _norm_int(p.get("used_count")) + int(delta)
                new_value = max(0, new_value)
            p["used_count"] = new_value
            # 首次记入使用后，状态自动离开"待写"
            if new_value > 0 and p.get("status") == "pending":
                p["status"] = "used"
            p["updated_at"] = now_str()
            p.setdefault("usage_notes", []).append({
                "at": p["updated_at"],
                "note": note,
                "chapter_key": chapter_key,
                "value": new_value,
            })
            p["usage_notes"] = p["usage_notes"][-50:]
            atomic_write_json(path, data)
            return p
        return None

    @locked_write
    def delete_point(self, project_id: str, pp_id: str) -> bool:
        path = self._path(project_id)
        if not path:
            return False
        data = self._load(project_id)
        before = len(data.get("points", []))
        data["points"] = [p for p in data.get("points", []) if p.get("id") != pp_id]
        if len(data["points"]) == before:
            return False
        atomic_write_json(path, data)
        return True

    @locked_write
    def set_fatigue_threshold(self, project_id: str, threshold: int) -> int:
        path = self._path(project_id)
        if not path:
            return DEFAULT_FATIGUE_THRESHOLD
        data = self._load(project_id)
        data["fatigue_threshold"] = max(1, _norm_int(threshold, DEFAULT_FATIGUE_THRESHOLD))
        atomic_write_json(path, data)
        return data["fatigue_threshold"]


def get_plot_point_manager():
    from knowledge.project_kb import get_project_kb_manager
    return PlotPointManager(get_project_kb_manager())
