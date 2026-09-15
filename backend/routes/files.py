"""
墨参 · 文件管理路由
文件上传、分析、拆书（可选）
"""
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from core.file_parser import FileParser
from core.utils import resolve_within
from knowledge.project_kb import get_project_kb_manager

router = APIRouter(prefix="/api/files", tags=["files"])
kb = get_project_kb_manager()


@router.post("/upload/{project_id}")
async def upload_file(project_id: str, file: UploadFile = File(...)):
    """上传文件到项目"""
    if not kb.get_project(project_id):
        raise HTTPException(404, "项目不存在")

    raw = await file.read()
    # 解析文件内容（docx 临时文件 + chardet 编码检测已统一封装至 FileParser）
    content = FileParser.parse_upload_file(raw, file.filename)

    # 保存到项目
    kb.save_uploaded_file(project_id, file.filename, content)

    # 尝试识别章节
    chapters = FileParser.split_chapters(content)

    return {
        "filename": file.filename,
        "size": len(raw),
        "char_count": len(content),
        "chapters_detected": len(chapters),
        "saved": True,
    }


@router.get("/{project_id}")
async def list_files(project_id: str):
    """列出项目已上传文件"""
    return {"files": kb.list_uploaded_files(project_id)}


@router.get("/{project_id}/content")
async def read_file_content(project_id: str, filename: str, max_chars: int = 200000):
    """读取已上传文件的正文（文件页预览用，超长时截断）"""
    if not kb.get_project(project_id):
        raise HTTPException(404, "项目不存在")

    project_dir = kb.get_project_dir(project_id)
    if not project_dir:
        raise HTTPException(404, "项目不存在")

    filepath = resolve_within(project_dir / "uploads", filename)
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(404, "文件不存在")

    content = kb.read_file_summary(filepath) or ""
    limit = max(1, max_chars)
    return {
        "filename": filename,
        "content": content[:limit],
        "char_count": len(content),
        "truncated": len(content) > limit,
    }


class AnalyzeRequest(BaseModel):
    project_id: str
    filename: str
    analysis_type: str = "style"  # style / logic / conflict / full


@router.post("/analyze")
async def analyze_file(req: AnalyzeRequest):
    """分析文件（文风/逻辑/冲突值/全面）"""
    from core.llm_provider import get_llm_provider
    from knowledge.rules_kb import RulesKB

    # 读取文件（优先知识库模板，其次 uploads 目录）
    content = kb.read_kb_file(req.project_id, req.filename)
    if content is None:
        project_dir = kb.get_project_dir(req.project_id)
        if project_dir:
            filepath = resolve_within(project_dir / "uploads", req.filename)
            if filepath.exists():
                content = kb.read_file_summary(filepath)
    if not content:
        raise HTTPException(404, "文件不存在")

    llm = get_llm_provider()

    if req.analysis_type == "style":
        rules = RulesKB.get_ai_fingerprint_checklist()
        prompt = f"""你是墨参，请对以下文本进行文风诊断。

{rules}

## 待分析文本
{content[:8000]}

请检查文本中是否存在 AI 指纹痕迹，分析感官描写比例、对话质量、翻译腔等问题。
以编辑批注的格式输出诊断报告，包括：
1. 总体评价
2. 发现的问题（按位置标注）
3. 修改建议"""

    elif req.analysis_type == "logic":
        project_summary = kb.get_project_summary(req.project_id)
        prompt = f"""你是墨参，请对以下文本进行逻辑诊断。

## 项目知识库摘要
{project_summary[:3000]}

## 待分析文本
{content[:8000]}

请检查：
1. 因果链完整性
2. 角色行为一致性
3. 时空连续性
4. 设定一致性
5. 关系逻辑
6. 节奏健康度

以诊断报告格式输出，标注问题位置和严重度。"""

    elif req.analysis_type == "conflict":
        rules = RulesKB.get_conflict_score_formula()
        prompt = f"""你是墨参，请对以下文本计算冲突值分布。

{rules}

## 待分析文本
{content[:8000]}

请逐章计算冲突值，输出每章的冲突值和星级评定，并绘制张力分布概况。
最后给出节奏建议。"""

    else:  # full
        rules = RulesKB.get_all_rules()
        project_summary = kb.get_project_summary(req.project_id)
        prompt = f"""你是墨参，请对以下文本进行全面诊断分析。

## 创作规范
{rules}

## 项目知识库摘要
{project_summary[:3000]}

## 待分析文本
{content[:8000]}

请输出综合诊断报告，包括：
1. 总览评分
2. 冲突值分布
3. 文风诊断
4. 逻辑检查
5. 伏笔追踪
6. 节奏分析
7. 总体建议"""

    messages = [{"role": "user", "content": prompt}]
    result = await llm.generate(messages, role="TEXT_MASTER", max_tokens=4096)

    # 保存诊断报告
    report_name = kb.save_diagnosis_report(req.project_id, result)

    return {"report": result, "saved_as": report_name}


class DissectRequest(BaseModel):
    project_id: str
    filename: str
    max_chapters: int = 0  # 0 = 全部


@router.post("/dissect")
async def dissect_novel(req: DissectRequest):
    """拆书蒸馏（可选功能）"""
    from knowledge.novel_analyzer import get_novel_analyzer

    # 读取文件
    project_dir = kb.get_project_dir(req.project_id)
    if not project_dir:
        raise HTTPException(404, "项目不存在")

    filepath = resolve_within(project_dir / "uploads", req.filename)
    if not filepath.exists():
        raise HTTPException(404, "文件不存在")

    content = kb.read_file_summary(filepath)

    analyzer = get_novel_analyzer()

    # 章节拆分
    chapters = FileParser.split_chapters(content)
    if req.max_chapters > 0:
        chapters = chapters[:req.max_chapters]

    if not chapters:
        return {"error": "未识别到章节结构", "chapters": 0}

    # 执行拆书（简化版：提取每章事实卡）
    results = await analyzer.analyze_chapters(chapters, project_id=req.project_id)

    return {
        "total_chapters": len(chapters),
        "analyzed": len(results),
        "results": results[:20],  # 只返回前20章结果预览
    }
