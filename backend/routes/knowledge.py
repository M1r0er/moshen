"""
墨参 · 知识库管理路由
用户可以添加知识源（本地文件上传或网络搜索关键词），
系统调用 LLM 分析后整理为 MD 文件存放在工作区的 knowledge/ 目录。
每个知识条目是一个 .md 文件，带有 YAML frontmatter。
"""
import uuid
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

from core.file_parser import FileParser
from core.utils import sanitize_path_name
from core.llm_provider import get_llm_provider
from routes.workspace import get_workspace_path, read_text_safe, write_text_safe

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


# ===== 工具函数 =====

def get_knowledge_dir() -> Path:
    """获取知识库目录，不存在则创建"""
    kd = get_workspace_path() / "knowledge"
    kd.mkdir(parents=True, exist_ok=True)
    return kd


def sanitize_id(kid: str) -> str:
    """校验并清理 ID，防止路径遍历"""
    return sanitize_path_name(kid, "无效的知识条目 ID")


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """解析 MD 文件的 YAML frontmatter，返回 (metadata, body)

    支持简单键值对和 [tag1, tag2] 列表格式，不依赖 PyYAML。
    """
    if not content.startswith("---"):
        return {}, content

    lines = content.split("\n")
    # 第一行必须是 ---
    if lines[0].strip() != "---":
        return {}, content

    # 查找结束的 ---
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break

    if end_idx is None:
        return {}, content

    yaml_lines = lines[1:end_idx]
    body = "\n".join(lines[end_idx + 1:]).lstrip("\n")

    metadata = {}
    for line in yaml_lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            # 处理 tags: [tag1, tag2] 格式
            if value.startswith("[") and value.endswith("]"):
                inner = value[1:-1]
                value = [
                    v.strip().strip("'\"")
                    for v in inner.split(",")
                    if v.strip()
                ]
            metadata[key] = value

    return metadata, body


def generate_frontmatter(metadata: dict) -> str:
    """生成 YAML frontmatter 字符串"""
    lines = ["---"]
    for key, value in metadata.items():
        if key == "tags" and isinstance(value, list):
            lines.append(f"tags: [{', '.join(value)}]")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


def build_knowledge_md(metadata: dict, body: str) -> str:
    """组装带 frontmatter 的完整 MD 内容"""
    frontmatter = generate_frontmatter(metadata)
    return f"{frontmatter}\n\n{body}"


def extract_title_from_content(content: str, fallback: str) -> str:
    """从 MD 内容中提取第一个 H1 标题，否则用 fallback"""
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def read_knowledge_file(kid: str) -> tuple[dict, str, Path] | None:
    """读取知识条目文件，返回 (metadata, body, filepath)，不存在返回 None"""
    kd = get_knowledge_dir()
    filepath = kd / f"{kid}.md"
    if not filepath.exists():
        return None
    content = read_text_safe(filepath)
    metadata, body = parse_frontmatter(content)
    return metadata, body, filepath


async def parse_uploaded_file(file: UploadFile) -> str:
    """解析上传的文件内容，支持 .txt / .md / .docx

    转发至 FileParser.parse_upload_file，避免与 routes/files.py 重复实现。
    """
    raw = await file.read()
    return FileParser.parse_upload_file(raw, file.filename)


def create_metadata(
    title: str,
    source: str,
    source_detail: str,
    ktype: str,
    tags: list[str] | None = None,
) -> dict:
    """构建知识条目元数据（保持固定字段顺序）"""
    now = datetime.now().isoformat()
    return {
        "id": "",
        "title": title,
        "source": source,
        "source_detail": source_detail,
        "type": ktype,
        "created_at": now,
        "updated_at": now,
        "tags": tags or [],
    }


def _persist_knowledge(
    title: str,
    content: str,
    source: str,
    source_detail: str,
    ktype: str,
    raw_content: str | None = None,
) -> dict:
    """统一的知识条目持久化流程

    被 save_knowledge_entry / upload_local_knowledge / search_knowledge /
    save_from_chat / refresh_knowledge 共用，消除"uuid → create_metadata →
    build_knowledge_md → write_text_safe → 返回 dict"模板的 5 处重复。

    Args:
        title: 条目标题（建议先 extract_title_from_content 提取）
        content: 条目正文（Markdown）
        source: 来源标识（chat / local_file / web_search）
        source_detail: 来源详情
        ktype: 条目类型
        raw_content: 原始内容（可选，仅 local_file 用于后续刷新）

    Returns:
        包含 id/title/filename/source/... 的 dict
    """
    kid = uuid.uuid4().hex[:12]
    metadata = create_metadata(
        title=title,
        source=source,
        source_detail=source_detail,
        ktype=ktype,
    )
    metadata["id"] = kid

    kd = get_knowledge_dir()
    md_content = build_knowledge_md(metadata, content)
    write_text_safe(kd / f"{kid}.md", md_content)

    # 保存原始内容用于后续刷新
    if raw_content is not None:
        write_text_safe(kd / f"{kid}.raw", raw_content)

    return {
        "id": kid,
        "title": title,
        "filename": f"{kid}.md",
        "source": source,
        "source_detail": source_detail,
        "type": ktype,
        "content": content,
    }


def save_knowledge_entry(title: str, content: str, ktype: str = "other") -> dict:
    """直接保存知识条目（供对话管理器调用，无需 HTTP 请求）

    Args:
        title: 条目标题
        content: 条目内容（Markdown）
        ktype: 条目类型

    Returns:
        保存结果 dict，包含 id, title 等
    """
    title = title.strip()
    content = content.strip()
    if not title or not content:
        return None

    extracted_title = extract_title_from_content(content, title)
    return _persist_knowledge(
        title=extracted_title,
        content=content,
        source="chat",
        source_detail="AI自动整理",
        ktype=ktype,
    )


def get_knowledge_summary(max_entries: int = 40, max_content_len: int = 400) -> str:
    """生成全局知识库摘要文本，供对话管理器注入上下文

    全局知识库条目存放在 {workspace}/knowledge/ 下。此前该目录只写不读，
    导致 AI 通过 [[KB_SAVE]] 自动存入的知识条目在后续对话中不可见。
    本函数将这些条目纳入上下文，使其可被引用。

    Args:
        max_entries: 最多注入的条目数（按更新时间倒序，超出部分省略）
        max_content_len: 每条正文的最大字符数

    Returns:
        Markdown 摘要文本；无条目时返回空字符串
    """
    kd = get_knowledge_dir()
    if not kd.exists():
        return ""

    entries = []
    for f in kd.iterdir():
        if not f.is_file() or f.suffix != ".md":
            continue
        try:
            content = read_text_safe(f)
            metadata, body = parse_frontmatter(content)
        except Exception:
            continue
        title = metadata.get("title", f.stem)
        ktype = metadata.get("type", "")
        updated = metadata.get("updated_at", metadata.get("created_at", ""))
        summary = body.strip()
        if len(summary) > max_content_len:
            summary = summary[:max_content_len] + "..."
        entries.append((updated, title, ktype, summary))

    if not entries:
        return ""

    entries.sort(key=lambda x: x[0], reverse=True)
    total = len(entries)
    entries = entries[:max_entries]

    lines = []
    for _updated, title, ktype, summary in entries:
        tag = f"（{ktype}）" if ktype else ""
        lines.append(f"#### {title}{tag}\n{summary}")
    if total > max_entries:
        lines.append(f"（另有 {total - max_entries} 条知识条目未列出）")
    return "\n\n".join(lines)


# ===== 请求模型 =====

class SearchRequest(BaseModel):
    query: str
    type: str = "other"


class UpdateKnowledgeRequest(BaseModel):
    content: str = ""
    title: str | None = None


class SaveFromChatRequest(BaseModel):
    title: str
    content: str
    type: str = "other"


# ===== 路由 =====

@router.get("")
async def list_knowledge():
    """列出所有知识条目（读取 knowledge/ 目录下所有 .md 文件的 frontmatter）"""
    kd = get_knowledge_dir()
    entries = []
    for f in kd.iterdir():
        if not f.is_file() or f.suffix != ".md":
            continue
        try:
            content = read_text_safe(f)
            metadata, _ = parse_frontmatter(content)
            entries.append({
                "id": metadata.get("id", f.stem),
                "title": metadata.get("title", f.stem),
                "source": metadata.get("source", ""),
                "source_detail": metadata.get("source_detail", ""),
                "type": metadata.get("type", ""),
                "created_at": metadata.get("created_at", ""),
                "updated_at": metadata.get("updated_at", metadata.get("created_at", "")),
                "tags": metadata.get("tags", []),
                "filename": f.name,
            })
        except Exception:
            continue
    # 按创建时间倒序
    entries.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return {"entries": entries}


@router.post("/local")
async def upload_local_knowledge(
    file: UploadFile = File(...),
    type: str = Form("other"),
):
    """上传本地文件分析（解析内容，调用 LLM 分析整理为 MD）"""
    if not file.filename:
        raise HTTPException(400, "文件名不能为空")

    # 解析文件内容
    content = await parse_uploaded_file(file)
    if not content.strip():
        raise HTTPException(400, "文件内容为空")

    # 调用 LLM 分析
    llm = get_llm_provider()
    prompt = (
        f"你是墨参，请分析以下文件内容，提取关键知识点，整理为结构化的 Markdown 文档。"
        f"包括：概述、关键设定、人物关系、重要事件、时间线等。"
        f"文件内容：{content[:8000]}"
    )
    try:
        result = await llm.generate(
            [{"role": "user", "content": prompt}],
            role="TEXT_MASTER",
            max_tokens=4096,
        )
    except Exception as e:
        raise HTTPException(500, f"LLM 分析失败: {e}")

    title = extract_title_from_content(result, Path(file.filename).stem)
    return _persist_knowledge(
        title=title,
        content=result,
        source="local_file",
        source_detail=file.filename,
        ktype=type,
        raw_content=content,
    )


@router.post("/search")
async def search_knowledge(req: SearchRequest):
    """网络搜索（调用 LLM 根据搜索词生成知识摘要 MD）"""
    if not req.query.strip():
        raise HTTPException(400, "搜索关键词不能为空")

    llm = get_llm_provider()
    prompt = (
        f"你是墨参，请根据以下搜索关键词，利用你的知识生成一份结构化的参考文档。"
        f"搜索关键词：{req.query}。类型：{req.type}。"
        f"请整理为 Markdown 格式，包括：概述、核心设定、详细信息、参考资料建议等。"
    )
    try:
        result = await llm.generate(
            [{"role": "user", "content": prompt}],
            role="TEXT_MASTER",
            max_tokens=4096,
        )
    except Exception as e:
        raise HTTPException(500, f"LLM 分析失败: {e}")

    title = extract_title_from_content(result, req.query)
    return _persist_knowledge(
        title=title,
        content=result,
        source="web_search",
        source_detail=req.query,
        ktype=req.type,
    )


@router.post("/from-chat")
async def save_from_chat(req: SaveFromChatRequest):
    """从对话内容直接保存为知识条目（无需 LLM 分析）"""
    if not req.title.strip():
        raise HTTPException(400, "标题不能为空")
    if not req.content.strip():
        raise HTTPException(400, "内容不能为空")

    title = req.title.strip()
    content = req.content.strip()
    extracted_title = extract_title_from_content(content, title)
    return _persist_knowledge(
        title=extracted_title,
        content=content,
        source="chat",
        source_detail="对话保存",
        ktype=req.type,
    )


@router.get("/{kid}")
async def get_knowledge(kid: str):
    """获取知识详情（返回完整 MD 内容）"""
    kid = sanitize_id(kid)
    result = read_knowledge_file(kid)
    if result is None:
        raise HTTPException(404, "知识条目不存在")
    metadata, body, filepath = result
    return {
        "id": metadata.get("id", kid),
        "title": metadata.get("title", ""),
        "source": metadata.get("source", ""),
        "source_detail": metadata.get("source_detail", ""),
        "type": metadata.get("type", ""),
        "created_at": metadata.get("created_at", ""),
        "updated_at": metadata.get("updated_at", metadata.get("created_at", "")),
        "tags": metadata.get("tags", []),
        "filename": filepath.name,
        "content": body,
    }


@router.put("/{kid}")
async def update_knowledge(kid: str, req: UpdateKnowledgeRequest):
    """更新知识内容（body: content 和 title）"""
    kid = sanitize_id(kid)
    result = read_knowledge_file(kid)
    if result is None:
        raise HTTPException(404, "知识条目不存在")
    metadata, _, filepath = result

    # 更新 title
    if req.title is not None:
        metadata["title"] = req.title

    # 更新修改时间
    metadata["updated_at"] = datetime.now().isoformat()

    # 重新组装并写入
    md_content = build_knowledge_md(metadata, req.content)
    write_text_safe(filepath, md_content)

    return {"success": True, "id": kid, "title": metadata.get("title", "")}


@router.delete("/{kid}")
async def delete_knowledge(kid: str):
    """删除知识条目"""
    kid = sanitize_id(kid)
    kd = get_knowledge_dir()
    md_path = kd / f"{kid}.md"
    raw_path = kd / f"{kid}.raw"

    if not md_path.exists():
        raise HTTPException(404, "知识条目不存在")

    md_path.unlink()
    if raw_path.exists():
        raw_path.unlink()

    return {"success": True, "id": kid}


@router.post("/{kid}/refresh")
async def refresh_knowledge(kid: str):
    """重新分析知识条目"""
    kid = sanitize_id(kid)
    result = read_knowledge_file(kid)
    if result is None:
        raise HTTPException(404, "知识条目不存在")
    metadata, body, filepath = result

    source = metadata.get("source", "")
    source_detail = metadata.get("source_detail", "")
    ktype = metadata.get("type", "other")

    llm = get_llm_provider()

    if source == "local_file":
        # 读取原始文件内容
        kd = get_knowledge_dir()
        raw_path = kd / f"{kid}.raw"
        if not raw_path.exists():
            raise HTTPException(400, "原始文件内容不存在，无法重新分析")
        content = read_text_safe(raw_path)
        prompt = (
            f"你是墨参，请分析以下文件内容，提取关键知识点，整理为结构化的 Markdown 文档。"
            f"包括：概述、关键设定、人物关系、重要事件、时间线等。"
            f"文件内容：{content[:8000]}"
        )
    elif source == "web_search":
        prompt = (
            f"你是墨参，请根据以下搜索关键词，利用你的知识生成一份结构化的参考文档。"
            f"搜索关键词：{source_detail}。类型：{ktype}。"
            f"请整理为 Markdown 格式，包括：概述、核心设定、详细信息、参考资料建议等。"
        )
    else:
        raise HTTPException(400, f"不支持的知识来源类型: {source}")

    try:
        new_content = await llm.generate(
            [{"role": "user", "content": prompt}],
            role="TEXT_MASTER",
            max_tokens=4096,
        )
    except Exception as e:
        raise HTTPException(500, f"LLM 分析失败: {e}")

    # 更新标题
    fallback_title = source_detail or metadata.get("title", kid)
    new_title = extract_title_from_content(new_content, fallback_title)
    metadata["title"] = new_title

    # 保存
    md_content = build_knowledge_md(metadata, new_content)
    write_text_safe(filepath, md_content)

    return {
        "id": kid,
        "title": new_title,
        "content": new_content,
    }
