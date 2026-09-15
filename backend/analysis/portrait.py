"""人物肖像绘制模块

流程：
1. 从关系脉络 JSON 读取角色条目
2. 调用 LLM 生成英文绘画 prompt（基于角色档案信息）
3. 调用图像生成 API 生成肖像图片
4. 保存为 PNG 到项目星图目录
"""
import base64
import json
import logging
from pathlib import Path

import httpx

from core.llm_provider import get_llm_provider

logger = logging.getLogger(__name__)

IMAGE_EXTS = ["png", "jpg", "jpeg", "webp", "gif"]


def _to_filename(name: str) -> str:
    """安全文件名转换"""
    return name.replace("/", "_").replace("\\", "_").replace(":", "_").replace("?", "_").replace("*", "_").replace('"', "_").replace("|", "_").replace("<", "_").replace(">", "_").strip()


def portrait_prompt(entity: dict, relations: list, timeline: list) -> list[dict]:
    """构建肖像绘制 prompt 的 messages

    LLM 根据角色档案信息生成英文绘画 prompt。
    """
    eid = entity.get("id", "")
    rel_lines = []
    for r in (relations or []):
        if r.get("from") == eid or r.get("to") == eid:
            rel_lines.append(f"- {r.get('from', '')} —({r.get('type', '')})→ {r.get('to', '')}")
    rel_text = "\n".join(rel_lines) if rel_lines else "（无）"

    tl_lines = []
    for t in (timeline or []):
        refs = t.get("refs", [])
        if eid in refs:
            tl_lines.append(f"- {t.get('time', '')}：{t.get('event', '')}")
    tl_text = "\n".join(tl_lines) if tl_lines else "（无）"

    aliases = entity.get("aliases", [])
    alias_str = "、".join(aliases) if aliases else "（无）"

    system = (
        "你是小说人物肖像画师。根据人物档案信息，总结人物在小说中的外貌、形象与性格特征，"
        "输出一段可用于 AI 绘画的英文肖像提示词（portrait prompt）。\n"
        "要求：\n"
        "1. 只输出提示词正文，不要任何解释、前后缀、引号或 Markdown。\n"
        "2. 提示词必须包含：外貌细节（发色、发型、眼睛、脸型、肤色、体型）、"
        "服饰（依据小说时代与风格，如古装/现代/科幻军装）、气质与神态（依据性格特征推断，"
        "如沉稳内敛/锋芒毕露/温柔敦厚）、年龄感。\n"
        "3. 画风依据小说类型推断（古风/写实/日系动漫/厚涂），在提示词末尾用一句标注画风。\n"
        "4. 构图：上半身肖像，正面或微侧，背景简洁纯净。\n"
        "5. 只能基于给定档案信息，档案未提及的部分做合理推断，不得编造相互矛盾的外貌细节。"
    )
    user = (
        f"人物：{eid}\n"
        f"档案摘要：{entity.get('summary', '（无）')}\n"
        f"别名：{alias_str}\n"
        f"性别：{entity.get('gender', '')}\n"
        f"年龄：{entity.get('age', '')}\n"
        f"身份：{entity.get('identity', '')}\n"
        f"外貌：{entity.get('appearance', '')}\n"
        f"性格：{entity.get('personality', '')}\n"
        f"相关关系：\n{rel_text}\n"
        f"相关时间线：\n{tl_text}\n"
        "请输出绘图提示词。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def find_portrait(project_dir: Path, entity_id: str) -> str | None:
    """查找已存在的肖像文件，返回扩展名或 None"""
    type_dir = project_dir / "星图" / "关系" / "角色"
    base = _to_filename(entity_id)
    for ext in IMAGE_EXTS:
        if (type_dir / f"{base}.{ext}").exists():
            return ext
    return None


def get_portrait_path(project_dir: Path, entity_id: str) -> Path | None:
    """获取肖像文件路径"""
    ext = find_portrait(project_dir, entity_id)
    if ext is None:
        return None
    return project_dir / "星图" / "关系" / "角色" / f"{_to_filename(entity_id)}.{ext}"


async def draw_one(
    project_dir: Path,
    entity: dict,
    relations: list,
    timeline: list,
    llm=None,
) -> str:
    """为单个角色生成肖像，返回文件扩展名

    流程：
    1. LLM 生成英文绘画 prompt
    2. 调用图像生成 API
    3. 保存图片到项目目录
    """
    if llm is None:
        llm = get_llm_provider()

    # 1. 生成绘画 prompt
    messages = portrait_prompt(entity, relations, timeline)
    draw_prompt = await llm.generate(messages, role="IMAGE_GENERATOR", temperature=0.5)
    draw_prompt = draw_prompt.strip() or entity.get("id", "character")

    # 2. 调用图像 API
    result = await llm.generate_image(draw_prompt)
    if "error" in result:
        raise RuntimeError(result["error"])

    # 3. 保存图片
    type_dir = project_dir / "星图" / "关系" / "角色"
    type_dir.mkdir(parents=True, exist_ok=True)
    base = type_dir / _to_filename(entity["id"])

    # 删除旧图（任意扩展名）
    for ext in IMAGE_EXTS:
        old = base.with_suffix(f".{ext}")
        if old.exists():
            old.unlink()

    if result.get("b64"):
        base.with_suffix(".png").write_bytes(
            base64.b64decode(result["b64"])
        )
        return "png"

    if result.get("url"):
        # 下载 URL 图片
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(result["url"])
            resp.raise_for_status()
            buf = resp.content
        # 从 URL 推断扩展名
        import re
        m = re.search(r"\.(png|jpe?g|webp|gif)(?:\?|$)", result["url"], re.I)
        ext = m.group(1).lower() if m else "png"
        ext = "jpg" if ext == "jpeg" else ext
        base.with_suffix(f".{ext}").write_bytes(buf)
        return ext

    raise RuntimeError("绘图 API 未返回图片内容")


async def generate_portraits(
    project_dir: Path,
    llm=None,
    progress_cb=None,
) -> dict:
    """为所有角色条目生成肖像（幂等：已有肖像则跳过）

    Returns: {"drawn": int, "skipped": int, "failed": int, "errors": [str]}
    """
    if llm is None:
        llm = get_llm_provider()

    type_dir = project_dir / "星图" / "关系" / "角色"
    data_file = type_dir / "关系脉络.json"
    if not data_file.exists():
        return {"drawn": 0, "skipped": 0, "failed": 0, "errors": ["尚无角色分析结果"]}

    data = json.loads(data_file.read_text(encoding="utf-8"))
    entities = data.get("entities", [])
    relations = data.get("relations", [])
    timeline = data.get("timeline", [])

    drawn = 0
    skipped = 0
    failed = 0
    errors: list[str] = []
    error_groups: dict[str, list[str]] = {}

    for entity in entities:
        eid = entity.get("id", "")
        if not eid:
            continue

        # 幂等：已有肖像则跳过
        if find_portrait(project_dir, eid):
            skipped += 1
            continue

        try:
            if progress_cb:
                progress_cb(f"绘制肖像「{eid}」…")
            ext = await draw_one(project_dir, entity, relations, timeline, llm)
            drawn += 1
            if progress_cb:
                progress_cb(f"肖像「{eid}」已保存（.{ext}）")
        except Exception as e:
            failed += 1
            # 配置级故障（如模型未开通、鉴权失败）会让所有条目报同样的错，按原因归并
            msg = f"{type(e).__name__}: {str(e)[:300]}"
            error_groups.setdefault(msg, []).append(eid)
            if progress_cb:
                progress_cb(f"肖像「{eid}」失败：{str(e)[:120]}")

    for msg, ids in error_groups.items():
        errors.append(f"{msg}（影响 {len(ids)} 个条目）" if len(ids) > 1 else f"{ids[0]}: {msg}")

    return {"drawn": drawn, "skipped": skipped, "failed": failed, "errors": errors}


async def redraw_portrait(
    project_dir: Path,
    entity_id: str,
    llm=None,
    progress_cb=None,
) -> str:
    """重绘指定角色的肖像（强制覆盖旧图）

    Returns: 扩展名
    """
    if llm is None:
        llm = get_llm_provider()

    type_dir = project_dir / "星图" / "关系" / "角色"
    data_file = type_dir / "关系脉络.json"
    if not data_file.exists():
        raise RuntimeError("尚无角色分析结果，请先执行分析")

    data = json.loads(data_file.read_text(encoding="utf-8"))
    entities = data.get("entities", [])
    relations = data.get("relations", [])
    timeline = data.get("timeline", [])

    entity = next((e for e in entities if e.get("id") == entity_id), None)
    if entity is None:
        raise RuntimeError(f"条目「{entity_id}」不存在")

    if progress_cb:
        progress_cb(f"重绘肖像「{entity_id}」…")
    ext = await draw_one(project_dir, entity, relations, timeline, llm)
    if progress_cb:
        progress_cb(f"肖像「{entity_id}」重绘完成（.{ext}）")
    return ext
