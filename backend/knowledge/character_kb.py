"""墨参 · 角色页知识库

一个「角色」= 档案 + 阶段节点 + 事件连线 + AI 观察收件箱。

为什么把 AI 观察单独放一层收件箱：
- 正文里对人物的描述往往是模糊的、临时的、甚至是作者随手写的，直接改写设定或阶段会污染
  作者的"既有事实"；
- 观察只是"待办建议"，作者点采纳才升级为正式阶段/别名，观察错了不采纳即可，零副作用。

存储：{project}/角色/角色.json
"""
import time
import uuid

from core.safe_io import atomic_write_json, locked_write, read_json

# 阶段切分档位：只影响"切分粒度"，不规定阶段数量（不同小说体量与变化节奏差异极大）
LEVELS = ("coarse", "standard", "fine")
LEVEL_LABELS = {"coarse": "粗略", "standard": "标准", "fine": "细致"}

# 观察状态
OBS_PENDING = "pending"
OBS_ACCEPTED = "accepted"
OBS_IGNORED = "ignored"

_MAX_OBSERVATIONS = 400


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _empty_doc() -> dict:
    return {"version": 1, "generatedAt": "", "characters": [], "scan_state": {}}


class CharacterKB:
    """角色页数据读写（线程内加锁，写入原子化）"""

    def __init__(self, kb):
        self.kb = kb

    # ---------- 存储 ----------

    def _path(self, project_id: str):
        project_dir = self.kb.get_project_dir(project_id)
        if not project_dir:
            return None
        d = project_dir / "角色"
        d.mkdir(parents=True, exist_ok=True)
        return d / "角色.json"

    def load(self, project_id: str) -> dict:
        path = self._path(project_id)
        if not path:
            return _empty_doc()
        data = read_json(path, None)
        if not isinstance(data, dict):
            return _empty_doc()
        data.setdefault("version", 1)
        data.setdefault("characters", [])
        data.setdefault("scan_state", {})
        if not isinstance(data.get("characters"), list):
            data["characters"] = []
        return data

    def save(self, project_id: str, data: dict) -> None:
        path = self._path(project_id)
        if not path:
            return
        data["generatedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
        atomic_write_json(path, data)

    # ---------- 角色 ----------

    def list_characters(self, project_id: str) -> list[dict]:
        return self.load(project_id).get("characters", [])

    def get(self, project_id: str, cid: str) -> dict | None:
        return next((c for c in self.list_characters(project_id) if c.get("id") == cid), None)

    def find_by_name(self, project_id: str, name: str) -> dict | None:
        """按名称或别名定位角色（用于「添加到角色」的去重）"""
        target = (name or "").strip()
        if not target:
            return None
        for c in self.list_characters(project_id):
            if c.get("name") == target or target in (c.get("aliases") or []):
                return c
        return None

    @locked_write
    def create(self, project_id: str, name: str, aliases=None, profile: str = "",
               source: str = "manual", color: str = "") -> dict | None:
        name = (name or "").strip()
        if not name:
            return None
        data = self.load(project_id)
        chars = data["characters"]
        if any(c.get("name") == name for c in chars):
            return None
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        char = {
            "id": _new_id("char"),
            "name": name,
            "aliases": [str(a).strip() for a in (aliases or []) if str(a).strip()],
            "profile": profile or "",
            "color": color or "",
            "source": source,
            "locked": False,
            "stages": [],
            "events": [],
            "observations": [],
            "created_at": now,
            "updated_at": now,
        }
        chars.append(char)
        self.save(project_id, data)
        return char

    @locked_write
    def update(self, project_id: str, cid: str, fields: dict) -> dict | None:
        data = self.load(project_id)
        char = next((c for c in data["characters"] if c.get("id") == cid), None)
        if char is None:
            return None
        if "name" in fields and fields["name"] is not None:
            new_name = str(fields["name"]).strip()
            if new_name:
                char["name"] = new_name
        if "aliases" in fields and fields["aliases"] is not None:
            seen: list[str] = []
            for a in fields["aliases"]:
                a = str(a).strip()
                if a and a != char.get("name") and a not in seen:
                    seen.append(a)
            char["aliases"] = seen
        if "profile" in fields and fields["profile"] is not None:
            char["profile"] = str(fields["profile"])
        if "color" in fields and fields["color"] is not None:
            char["color"] = str(fields["color"])
        if "locked" in fields and fields["locked"] is not None:
            char["locked"] = bool(fields["locked"])
        char["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.save(project_id, data)
        return char

    @locked_write
    def delete(self, project_id: str, cid: str) -> bool:
        data = self.load(project_id)
        before = len(data["characters"])
        data["characters"] = [c for c in data["characters"] if c.get("id") != cid]
        if len(data["characters"]) == before:
            return False
        self.save(project_id, data)
        return True

    @locked_write
    def set_stages(self, project_id: str, cid: str, stages: list) -> dict | None:
        """整体替换阶段节点（拖拽摆位 / 增删 / AI 解析都走这里）"""
        data = self.load(project_id)
        char = next((c for c in data["characters"] if c.get("id") == cid), None)
        if char is None:
            return None
        cleaned = []
        for s in stages or []:
            title = str(s.get("title", "")).strip()
            if not title:
                continue
            cleaned.append({
                "id": s.get("id") or _new_id("stage"),
                "title": title,
                "note": str(s.get("note", "") or ""),
                "chapter": str(s.get("chapter", "") or ""),
                "x": float(s.get("x", 0) or 0),
                "y": float(s.get("y", 0) or 0),
            })
        char["stages"] = cleaned
        # 阶段被替换后，删除指向不存在阶段的事件，避免出现悬空连线
        valid = {s["id"] for s in cleaned}
        char["events"] = [
            e for e in char.get("events", [])
            if e.get("from") in valid and e.get("to") in valid
        ]
        char["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.save(project_id, data)
        return char

    @locked_write
    def set_events(self, project_id: str, cid: str, events: list) -> dict | None:
        data = self.load(project_id)
        char = next((c for c in data["characters"] if c.get("id") == cid), None)
        if char is None:
            return None
        valid = {s["id"] for s in char.get("stages", [])}
        cleaned = []
        for e in events or []:
            frm, to = e.get("from"), e.get("to")
            if not frm or not to or frm == to:
                continue
            if frm not in valid or to not in valid:
                continue
            cleaned.append({
                "id": e.get("id") or _new_id("evt"),
                "from": frm,
                "to": to,
                "label": str(e.get("label", "") or ""),
            })
        char["events"] = cleaned
        char["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.save(project_id, data)
        return char

    # ---------- AI 观察（收件箱）----------

    @locked_write
    def add_observations(self, project_id: str, cid: str, items: list) -> int:
        """批量追加观察；同一章节 + 同一摘要视为重复，直接跳过"""
        data = self.load(project_id)
        char = next((c for c in data["characters"] if c.get("id") == cid), None)
        if char is None:
            return 0
        obs = char.setdefault("observations", [])
        existing = {(o.get("chapter"), o.get("snippet")) for o in obs}
        added = 0
        for it in items or []:
            snippet = str(it.get("snippet", "") or "").strip()
            chapter = str(it.get("chapter", "") or "").strip()
            if not snippet:
                continue
            if (chapter, snippet) in existing:
                continue
            obs.append({
                "id": _new_id("obs"),
                "chapter": chapter,
                "vol_id": str(it.get("vol_id", "") or ""),
                "ch_id": str(it.get("ch_id", "") or ""),
                "snippet": snippet[:400],
                "hit_alias": str(it.get("hit_alias", "") or ""),
                "matched_stage": str(it.get("matched_stage", "") or ""),
                "matched_stage_id": str(it.get("matched_stage_id", "") or ""),
                "suggest_stage": str(it.get("suggest_stage", "") or ""),
                "is_new_alias": bool(it.get("is_new_alias")),
                "status": OBS_PENDING,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            existing.add((chapter, snippet))
            added += 1
        if added:
            char["observations"] = obs[-_MAX_OBSERVATIONS:]
            char["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self.save(project_id, data)
        return added

    def list_observations(self, project_id: str, status: str = "") -> list[dict]:
        """展平所有角色的观察（带角色信息），可按状态过滤"""
        out = []
        for c in self.list_characters(project_id):
            for o in c.get("observations", []):
                if status and o.get("status") != status:
                    continue
                out.append({**o, "cid": c.get("id"), "character": c.get("name")})
        out.sort(key=lambda o: (o.get("created_at") or ""), reverse=True)
        return out

    def _find_obs(self, data: dict, cid: str, oid: str):
        char = next((c for c in data["characters"] if c.get("id") == cid), None)
        if char is None:
            return None, None
        obs = next((o for o in char.get("observations", []) if o.get("id") == oid), None)
        return char, obs

    @locked_write
    def set_observation_status(self, project_id: str, cid: str, oid: str, status: str) -> dict | None:
        data = self.load(project_id)
        char, obs = self._find_obs(data, cid, oid)
        if char is None or obs is None:
            return None
        obs["status"] = status
        obs["handled_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        char["updated_at"] = obs["handled_at"]
        self.save(project_id, data)
        return obs

    @locked_write
    def accept_observation(self, project_id: str, cid: str, oid: str,
                           mode: str = "note", stage_id: str = "",
                           title: str = "", note: str = "") -> dict | None:
        """采纳一条观察

        mode:
          note  —— 追加到已有阶段（只补出处/描述，不改阶段本体）
          stage —— 作为新阶段插入（放在末尾，作者可再拖拽）
          alias —— 把观察里的别名并入角色别名
        """
        data = self.load(project_id)
        char, obs = self._find_obs(data, cid, oid)
        if char is None or obs is None:
            return None

        now = time.strftime("%Y-%m-%d %H:%M:%S")
        snippet = obs.get("snippet", "")
        chapter = obs.get("chapter", "")

        if mode == "alias":
            alias = (title or obs.get("hit_alias") or "").strip()
            if alias and alias != char.get("name") and alias not in char.get("aliases", []):
                char.setdefault("aliases", []).append(alias)
        elif mode == "stage":
            stages = char.setdefault("stages", [])
            idx = len(stages)
            stages.append({
                "id": _new_id("stage"),
                "title": (title or obs.get("suggest_stage") or "新阶段").strip(),
                "note": (note or snippet).strip(),
                "chapter": chapter,
                "x": 40 + idx * 240,
                "y": 120 + (idx % 2) * 120,
            })
        else:  # note：追加到已有阶段
            stage = next((s for s in char.get("stages", []) if s.get("id") == stage_id), None)
            if stage is None:
                return None
            extra = f"[{chapter}] {note or snippet}".strip()
            stage["note"] = (stage.get("note", "") + "\n" + extra).strip()

        obs["status"] = OBS_ACCEPTED
        obs["handled_at"] = now
        char["updated_at"] = now
        self.save(project_id, data)
        return char

    # ---------- 扫描缓存（避免重复消耗 token）----------

    def get_scan(self, project_id: str, key: str) -> dict:
        return (self.load(project_id).get("scan_state") or {}).get(key) or {}

    @locked_write
    def set_scan(self, project_id: str, key: str, digest: str) -> None:
        path = self._path(project_id)
        if not path:
            return
        data = self.load(project_id)
        state = data.setdefault("scan_state", {})
        state[key] = {"hash": digest, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        # 只保留最近 300 章的扫描记录，避免文件无限增长
        if len(state) > 300:
            for k in sorted(state.keys(), key=lambda k: state[k].get("at", ""))[:len(state) - 300]:
                state.pop(k, None)
        data["generatedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
        atomic_write_json(path, data)

    # ---------- 标蓝词表 ----------

    def mention_index(self, project_id: str) -> list[dict]:
        """给写作页标蓝用的词表：每条 = 角色名 + 别名 + 当前阶段名"""
        index = []
        for c in self.list_characters(project_id):
            terms = [c.get("name", "")] + list(c.get("aliases") or [])
            terms = [t for t in terms if t and t.strip()]
            if not terms:
                continue
            index.append({
                "cid": c.get("id"),
                "name": c.get("name", ""),
                "aliases": list(c.get("aliases") or []),
                "terms": terms,
                "stages": [
                    {"id": s.get("id", ""), "title": s.get("title", "")}
                    for s in c.get("stages", []) if s.get("title")
                ],
            })
        return index


def get_character_kb():
    from knowledge.project_kb import get_project_kb_manager
    return CharacterKB(get_project_kb_manager())
