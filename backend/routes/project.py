"""
墨参 · 项目管理路由
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from knowledge.project_kb import get_project_kb_manager

router = APIRouter(prefix="/api/projects", tags=["project"])
kb = get_project_kb_manager()


class CreateProjectRequest(BaseModel):
    name: str
    description: str = ""


class RenameProjectRequest(BaseModel):
    name: str


class SetWorkspaceRequest(BaseModel):
    path: str


class ReadFileRequest(BaseModel):
    relative_path: str


@router.get("")
async def list_projects():
    """列出所有项目"""
    return {"projects": kb.list_projects()}


@router.post("")
async def create_project(req: CreateProjectRequest):
    """创建新项目"""
    if not req.name.strip():
        raise HTTPException(400, "项目名称不能为空")
    return kb.create_project(req.name.strip(), req.description)


@router.get("/{project_id}")
async def get_project(project_id: str):
    """获取项目详情"""
    meta = kb.get_project(project_id)
    if not meta:
        raise HTTPException(404, "项目不存在")
    return meta


@router.put("/{project_id}")
async def rename_project(project_id: str, req: RenameProjectRequest):
    """重命名项目"""
    if not req.name.strip():
        raise HTTPException(400, "项目名称不能为空")
    meta = kb.rename_project(project_id, req.name.strip())
    if not meta:
        raise HTTPException(404, "项目不存在")
    return {"success": True, "project": meta}


@router.delete("/{project_id}")
async def delete_project(project_id: str):
    """删除项目"""
    success = kb.delete_project(project_id)
    if not success:
        raise HTTPException(404, "项目不存在")
    return {"success": True}


@router.get("/{project_id}/kb-files")
async def list_kb_files(project_id: str):
    """列出项目知识库文件"""
    return {"files": kb.list_kb_files(project_id)}


@router.get("/{project_id}/kb-files/{filename}")
async def read_kb_file(project_id: str, filename: str):
    """读取知识库文件内容"""
    content = kb.read_kb_file(project_id, filename)
    if content is None:
        raise HTTPException(404, "文件不存在")
    return {"filename": filename, "content": content}


@router.put("/{project_id}/kb-files/{filename}")
async def write_kb_file(project_id: str, filename: str, body: dict):
    """写入知识库文件内容"""
    success = kb.write_kb_file(project_id, filename, body.get("content", ""))
    if not success:
        raise HTTPException(404, "项目或文件不存在")
    return {"success": True}


@router.get("/{project_id}/chapters")
async def list_chapters(project_id: str):
    """列出章节"""
    return {"chapters": kb.list_chapters(project_id)}


@router.get("/{project_id}/reports")
async def list_reports(project_id: str):
    """列出诊断报告"""
    return {"reports": kb.list_diagnosis_reports(project_id)}


# ===== 项目独立工作区 =====

@router.get("/{project_id}/workspace")
async def get_project_workspace(project_id: str):
    """获取项目工作区信息"""
    if not kb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    ws_path = kb.get_project_workspace(project_id)
    return {
        "path": ws_path,
        "exists": bool(ws_path),
    }


@router.put("/{project_id}/workspace")
async def set_project_workspace(project_id: str, req: SetWorkspaceRequest):
    """设置项目独立工作区路径"""
    path = req.path.strip()
    if path:
        from pathlib import Path
        p = Path(path).expanduser().resolve()
        if not p.exists():
            raise HTTPException(400, f"路径不存在: {p}")
        if not p.is_dir():
            raise HTTPException(400, f"路径不是目录: {p}")
        path = str(p)

    meta = kb.set_project_workspace(project_id, path)
    if not meta:
        raise HTTPException(404, "项目不存在")
    return {"success": True, "workspace_path": meta.get("workspace_path", "")}


@router.get("/{project_id}/workspace/tree")
async def get_workspace_tree(project_id: str):
    """获取项目工作区文件树"""
    if not kb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    files = kb.list_workspace_files(project_id)
    return {"files": files}


@router.post("/{project_id}/workspace/read")
async def read_workspace_file(project_id: str, req: ReadFileRequest):
    """读取项目工作区中的文件"""
    if not kb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    content = kb.read_workspace_file(project_id, req.relative_path)
    if content is None:
        raise HTTPException(404, "文件不存在或无法读取")
    return {"relative_path": req.relative_path, "content": content}
