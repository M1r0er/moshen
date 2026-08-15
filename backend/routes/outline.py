"""
墨参 · 大纲管理路由
流程图式大纲，支持分支、合并、连线说明。
数据存储在项目的 outline.json 文件中。
"""
import json
import time
import uuid
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from routes.workspace import get_workspace_path

router = APIRouter(prefix="/api/outline", tags=["outline"])

NODE_W = 180
NODE_H = 70


def sanitize_name(name: str) -> str:
    """校验名称，防止路径遍历"""
    if not name or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "无效的项目ID")
    return name


def _outline_path(project_id: str) -> Path:
    pid = sanitize_name(project_id)
    p = get_workspace_path() / "projects" / pid / "outline.json"
    return p


def load_outline(project_id: str) -> dict:
    """加载大纲数据，不存在则返回空结构"""
    p = _outline_path(project_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"nodes": [], "edges": [], "meta": {"version": 1}}


def save_outline(project_id: str, data: dict) -> None:
    """保存大纲数据"""
    p = _outline_path(project_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _gen_id() -> str:
    return uuid.uuid4().hex[:10]


# ===== 请求模型 =====

class CreateNodeRequest(BaseModel):
    title: str
    content: str = ""
    node_type: str = "main"  # main | branch
    x: float = 0
    y: float = 0


class UpdateNodeRequest(BaseModel):
    title: str | None = None
    content: str | None = None
    node_type: str | None = None
    x: float | None = None
    y: float | None = None


class CreateEdgeRequest(BaseModel):
    from_node: str
    to_node: str
    label: str = ""


class UpdateEdgeRequest(BaseModel):
    label: str | None = None
    from_node: str | None = None
    to_node: str | None = None


# ===== 路由 =====

@router.get("/{project_id}")
async def get_outline(project_id: str):
    """获取大纲数据"""
    return load_outline(project_id)


@router.post("/{project_id}/node")
async def create_node(project_id: str, req: CreateNodeRequest):
    """创建节点"""
    data = load_outline(project_id)
    node = {
        "id": _gen_id(),
        "title": req.title.strip(),
        "content": req.content.strip(),
        "type": req.node_type,
        "x": req.x,
        "y": req.y,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    data["nodes"].append(node)
    save_outline(project_id, data)
    return {"success": True, "node": node}


@router.put("/{project_id}/node/{node_id}")
async def update_node(project_id: str, node_id: str, req: UpdateNodeRequest):
    """更新节点"""
    data = load_outline(project_id)
    for n in data["nodes"]:
        if n["id"] == node_id:
            if req.title is not None:
                n["title"] = req.title.strip()
            if req.content is not None:
                n["content"] = req.content.strip()
            if req.node_type is not None:
                n["type"] = req.node_type
            if req.x is not None:
                n["x"] = req.x
            if req.y is not None:
                n["y"] = req.y
            n["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            save_outline(project_id, data)
            return {"success": True, "node": n}
    raise HTTPException(404, "节点不存在")


@router.delete("/{project_id}/node/{node_id}")
async def delete_node(project_id: str, node_id: str):
    """删除节点及其关联连线"""
    data = load_outline(project_id)
    data["nodes"] = [n for n in data["nodes"] if n["id"] != node_id]
    data["edges"] = [e for e in data["edges"]
                     if e["from"] != node_id and e["to"] != node_id]
    save_outline(project_id, data)
    return {"success": True}


@router.post("/{project_id}/edge")
async def create_edge(project_id: str, req: CreateEdgeRequest):
    """创建连线"""
    data = load_outline(project_id)
    node_ids = {n["id"] for n in data["nodes"]}
    if req.from_node not in node_ids or req.to_node not in node_ids:
        raise HTTPException(400, "源节点或目标节点不存在")
    edge = {
        "id": _gen_id(),
        "from": req.from_node,
        "to": req.to_node,
        "label": req.label.strip(),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    data["edges"].append(edge)
    save_outline(project_id, data)
    return {"success": True, "edge": edge}


@router.put("/{project_id}/edge/{edge_id}")
async def update_edge(project_id: str, edge_id: str, req: UpdateEdgeRequest):
    """更新连线"""
    data = load_outline(project_id)
    for e in data["edges"]:
        if e["id"] == edge_id:
            if req.label is not None:
                e["label"] = req.label.strip()
            if req.from_node is not None:
                e["from"] = req.from_node
            if req.to_node is not None:
                e["to"] = req.to_node
            e["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            save_outline(project_id, data)
            return {"success": True, "edge": e}
    raise HTTPException(404, "连线不存在")


@router.delete("/{project_id}/edge/{edge_id}")
async def delete_edge(project_id: str, edge_id: str):
    """删除连线"""
    data = load_outline(project_id)
    data["edges"] = [e for e in data["edges"] if e["id"] != edge_id]
    save_outline(project_id, data)
    return {"success": True}


# ===== 供对话管理器直接调用的函数 =====

def save_outline_node(
    project_id: str,
    title: str,
    content: str = "",
    node_type: str = "main",
    after_title: str | None = None,
    edge_label: str = "",
) -> dict | None:
    """直接创建大纲节点（供对话管理器调用）

    Args:
        project_id: 项目ID
        title: 节点标题
        content: 节点内容
        node_type: 节点类型 main|branch
        after_title: 如果指定，会在该标题的节点之后创建连线
        edge_label: 连线说明文字

    Returns:
        创建结果 dict
    """
    title = title.strip()
    if not title:
        return None

    data = load_outline(project_id)

    # 重名检测：如果同名节点已存在，转为更新
    for n in data["nodes"]:
        if n["title"] == title:
            n["content"] = content.strip()
            n["type"] = node_type
            n["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            save_outline(project_id, data)
            return {"id": n["id"], "title": title, "updated": True}

    # 计算新节点位置
    max_x = max((n["x"] for n in data["nodes"]), default=0)
    node = {
        "id": _gen_id(),
        "title": title,
        "content": content.strip(),
        "type": node_type,
        "x": max_x + 250 if data["nodes"] else 100,
        "y": 200 if node_type == "main" else 350,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    data["nodes"].append(node)

    # 如果指定了前置节点，创建连线
    if after_title:
        after_title = after_title.strip()
        for n in data["nodes"]:
            if n["title"] == after_title and n["id"] != node["id"]:
                edge = {
                    "id": _gen_id(),
                    "from": n["id"],
                    "to": node["id"],
                    "label": edge_label.strip(),
                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                data["edges"].append(edge)
                break

    save_outline(project_id, data)
    return {"id": node["id"], "title": title, "updated": False}


def update_outline_node(
    project_id: str,
    title: str,
    content: str | None = None,
    new_title: str | None = None,
) -> dict | None:
    """按标题查找并更新已有大纲节点"""
    title = title.strip()
    if not title:
        return None

    data = load_outline(project_id)
    for n in data["nodes"]:
        if n["title"] == title:
            if new_title:
                n["title"] = new_title.strip()
            if content is not None:
                n["content"] = content.strip()
            n["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            save_outline(project_id, data)
            return {"id": n["id"], "title": n["title"], "updated": True}
    return None


def save_outline_edge(
    project_id: str,
    from_title: str,
    to_title: str,
    label: str = "",
) -> dict | None:
    """按标题查找两个节点并创建/更新连线"""
    from_title = from_title.strip()
    to_title = to_title.strip()
    if not from_title or not to_title:
        return None

    data = load_outline(project_id)
    from_id = to_id = None
    for n in data["nodes"]:
        if n["title"] == from_title:
            from_id = n["id"]
        if n["title"] == to_title:
            to_id = n["id"]

    if not from_id or not to_id:
        return None

    # 检查是否已有连线，有则更新
    for e in data["edges"]:
        if e["from"] == from_id and e["to"] == to_id:
            e["label"] = label.strip()
            e["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            save_outline(project_id, data)
            return {"id": e["id"], "from": from_title, "to": to_title, "updated": True}

    edge = {
        "id": _gen_id(),
        "from": from_id,
        "to": to_id,
        "label": label.strip(),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    data["edges"].append(edge)
    save_outline(project_id, data)
    return {"id": edge["id"], "from": from_title, "to": to_title, "updated": False}


def get_outline_summary(project_id: str, max_content_len: int = 300) -> str:
    """生成大纲摘要文本，供对话管理器注入上下文"""
    data = load_outline(project_id)
    if not data["nodes"]:
        return ""

    lines = []
    node_map = {n["id"]: n for n in data["nodes"]}

    # 找出没有入边的节点作为起点
    has_incoming = {e["to"] for e in data["edges"]}
    roots = [n for n in data["nodes"] if n["id"] not in has_incoming]

    def render_node(nid, indent, visited):
        if nid in visited:
            return
        visited.add(nid)
        n = node_map.get(nid)
        if not n:
            return
        prefix = "  " * indent + ("▸ " if indent == 0 else "  " * indent + "└ ")
        type_tag = "[支线]" if n["type"] == "branch" else ""
        lines.append(f"{prefix}{n['title']}{type_tag}")
        content = n.get("content", "").strip()
        if content:
            truncated = content[:max_content_len]
            if len(content) > max_content_len:
                truncated += "..."
            for cl in truncated.split("\n"):
                lines.append("  " * (indent + 1) + cl)
        # 找出出边
        for e in data["edges"]:
            if e["from"] == nid:
                label = f" ({e['label']})" if e.get("label") else ""
                lines.append("  " * (indent + 1) + f"→{label}")
                render_node(e["to"], indent + 1, visited)

    visited = set()
    for root in roots:
        render_node(root["id"], 0, visited)

    # 渲染未被访问到的节点（孤立节点）
    for n in data["nodes"]:
        if n["id"] not in visited:
            lines.append(f"▸ {n['title']} [孤立]")

    return "\n".join(lines).strip()
