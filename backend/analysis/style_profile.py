"""本书「出品风格档案」

用途：让同一本小说生成的所有人物肖像保持统一画风。

做法：
1. 用一次 LLM 调用识别本书的题材（玄幻 / 仙侠 / 机甲科幻 / 末日 …）；
2. 题材映射到规范化的英文画风基线（GENRE_LIBRARY），再拼上固定的一致性尾巴；
3. 之后每次生图都把这段**同一份**风格串追加到绘图提示词末尾。

为什么不让每张图各自“推断画风”：模型每轮推断出的媒介、上色、笔触都会有漂移，
只有把风格串固定下来（并在提示词里显式约束），同书肖像才会真正统一。
"""
import json
import re
import time
from pathlib import Path

from core.llm_provider import get_llm_provider
from core.safe_io import atomic_write_json

# 风格档案在项目内的存放位置（与星图其它状态同处 state.json）
STATE_REL = ("星图", "state.json")
STYLE_KEY = "novel_style"


# 题材规范库：key 为规范题材名，style 为英文画风基线
GENRE_LIBRARY: dict[str, dict] = {
    "东方玄幻": {"label": "东方玄幻", "style": "eastern fantasy (xuanhuan) illustration, semi-realistic Chinese CG, ornate armor and layered robes, glowing qi energy, dramatic scale"},
    "传统仙侠": {"label": "传统仙侠", "style": "traditional Chinese xianxia, ink-wash brushwork blended with semi-realistic CG, flowing silk hanfu, ethereal mist, jade and gold ornaments"},
    "现代修真": {"label": "现代修真", "style": "modern-day cultivation fantasy, urban China with mystical elements, semi-realistic anime-adjacent CG, subtle aura glow"},
    "玄幻": {"label": "玄幻", "style": "generic xuanhuan fantasy illustration, semi-realistic Chinese CG, fantasy armor, glowing magical effects"},
    "武侠": {"label": "武侠", "style": "wuxia martial arts illustration, semi-realistic Chinese painting, plain hanfu and daoist robes, misty mountains, restrained muted palette"},
    "西方幻想": {"label": "西方幻想", "style": "western high fantasy illustration, oil-painting realism, medieval European garments and armor, castles and cathedrals, dramatic chiaroscuro"},
    "剑与魔法": {"label": "剑与魔法", "style": "sword-and-sorcery fantasy illustration, painterly semi-realism, leather-and-steel adventurer gear, arcane runes"},
    "科幻": {"label": "科幻", "style": "science fiction concept art, hard-surface design, cinematic lighting, sleek futuristic materials"},
    "机甲科幻": {"label": "机甲科幻", "style": "mecha sci-fi concept art, detailed mechanical hard-surface design, metallic plating and cockpit details, cool industrial palette"},
    "星际科幻": {"label": "星际科幻", "style": "space opera concept art, sleek starship interiors, holographic interfaces, deep-space lighting with rim light"},
    "赛博朋克": {"label": "赛博朋克", "style": "cyberpunk illustration, neon-soaked night city, rain and chroma, high-contrast magenta-cyan palette, techwear"},
    "末日废土": {"label": "末日废土", "style": "post-apocalyptic wasteland art, desaturated dusty palette, improvised gear and scavenged armor, harsh sun and rust"},
    "丧尸": {"label": "丧尸", "style": "zombie apocalypse art, gritty desaturated realism, torn modern clothing, grime and desperation"},
    "都市": {"label": "都市", "style": "modern urban realistic illustration, contemporary casual fashion, clean studio-like lighting"},
    "都市异能": {"label": "都市异能", "style": "urban supernatural illustration, modern fashion with subtle power effects, semi-realistic CG with cool rim light"},
    "极道流": {"label": "极道流", "style": "gritty underworld crime drama art, dark moody low-key lighting, sharp suits and scars, noir palette"},
    "历史": {"label": "历史", "style": "historical Chinese realism, period-accurate hanfu and armor, aged parchment palette, natural light"},
    "古言": {"label": "古言", "style": "ancient Chinese romance illustration, delicate period hanfu and hair ornaments, soft pastel palette, gentle light"},
    "言情": {"label": "言情", "style": "romance illustration, soft luminous lighting, stylish contemporary fashion, warm pastel palette"},
    "悬疑": {"label": "悬疑", "style": "mystery thriller art, cold desaturated palette, heavy shadow and fog, restrained realism"},
    "灵异": {"label": "灵异", "style": "supernatural horror illustration, muted cold tones, faint ghostly light, eerie atmosphere"},
    "无限流": {"label": "无限流", "style": "infinite-flow survival art, mixed-world genre mashup, semi-realistic CG, high-tension framing"},
    "游戏": {"label": "游戏", "style": "game key-art illustration, polished semi-realistic rendering, heroic posing, vivid magic effects"},
    "军事": {"label": "军事", "style": "military illustration, realistic tactical gear and camouflage, harsh natural light, muted field palette"},
    "现实": {"label": "现实", "style": "realistic contemporary illustration, natural proportions and lighting, muted documentary palette"},
    "其他": {"label": "其他", "style": "semi-realistic character illustration, neutral art direction, clean lighting"},
}

# 固定的一致性尾巴：无论题材如何，同一本书的每张肖像都追加同一段
CONSISTENCY_TAIL = (
    "consistent character design and unified art direction across the whole series, "
    "identical rendering medium and brush treatment, identical color grading and lighting, "
    "identical level of stylization and detail, half-body portrait, clean simple background, "
    "no text, no watermark, no logo"
)


def genre_options() -> list[dict]:
    """给前端用的题材选项列表"""
    return [{"value": k, "label": v["label"]} for k, v in GENRE_LIBRARY.items()]


def _state_path(project_dir: Path) -> Path:
    return project_dir.joinpath(*STATE_REL)


def load_style(project_dir: Path) -> dict | None:
    """读取本书风格档案（不存在返回 None）"""
    path = _state_path(project_dir)
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return None
    style = state.get(STYLE_KEY)
    return style if isinstance(style, dict) and style.get("genre") else None


def save_style(project_dir: Path, profile: dict) -> dict:
    """写入本书风格档案（保留 state.json 中的其它字段）"""
    path = _state_path(project_dir)
    state: dict = {}
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            state = {}
    profile = dict(profile or {})
    profile["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    state[STYLE_KEY] = profile
    atomic_write_json(path, state)
    return profile


def resolve_style_prompt(profile: dict | None) -> str:
    """得出最终英文画风串：手填优先，其次按题材查表，最后退回「其他」"""
    if not profile:
        return ""
    base = str(profile.get("style_prompt") or "").strip()
    if not base:
        entry = GENRE_LIBRARY.get(str(profile.get("genre") or "")) or GENRE_LIBRARY["其他"]
        base = entry["style"]
    return base


def style_suffix(profile: dict | None) -> str:
    """生图时追加到提示词末尾的风格串（题材基线 + 调性 + 一致性尾巴）"""
    if not profile:
        return ""
    parts = [resolve_style_prompt(profile)]
    tone = str(profile.get("tone") or "").strip()
    if tone:
        parts.append(tone)
    parts.append(CONSISTENCY_TAIL)
    return ", ".join(p.strip(" ,") for p in parts if p and p.strip())


def style_guidance(profile: dict | None) -> str:
    """给「生成人物提示词」的模型看的画风约束说明（中文）"""
    if not profile:
        return ""
    label = str(profile.get("genre") or "").strip()
    subs = [str(s) for s in (profile.get("sub_genres") or []) if str(s).strip()]
    if subs:
        label = label + "／" + "、".join(subs)
    return (
        f"本书题材：{label}。画风已由「出品风格档案」统一规定，禁止自行更改画风或媒介：\n"
        f"{resolve_style_prompt(profile)}\n"
        "因此你只需描述人物本身（外貌、服饰、气质、年龄），"
        "不要写任何画风、媒介、渲染方式相关的词（如 oil painting / anime / 3D render 等）。"
    )


def _extract_json(raw: str) -> dict:
    """从模型输出里抠出 JSON 对象（容忍 Markdown 围栏与前后说明）"""
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def normalize_profile(data: dict, source: str = "auto") -> dict:
    """把模型/前端来的原始数据规范成风格档案"""
    genre = str(data.get("genre") or "").strip()
    if genre not in GENRE_LIBRARY:
        genre = "其他"

    subs = []
    for s in (data.get("sub_genres") or []):
        s = str(s).strip()
        if s and s != genre and s in GENRE_LIBRARY and s not in subs:
            subs.append(s)
    subs = subs[:3]

    return {
        "genre": genre,
        "sub_genres": subs,
        "tone": str(data.get("tone") or "").strip()[:200],
        "style_prompt": str(data.get("style_prompt") or "").strip()[:600],
        "source": "manual" if source == "manual" else "auto",
    }


def _build_messages(sample_text: str) -> list[dict]:
    options = "、".join(GENRE_LIBRARY.keys())
    system = (
        "你是资深网文编辑，负责为一部小说确定「题材标签」与「整体视觉调性」，供后续统一绘制人物肖像使用。\n"
        "只输出一个 JSON 对象，不要任何解释或 Markdown 围栏。\n"
        '结构：{"genre": "题材", "sub_genres": ["细分题材"], "tone": "visual tone in English", "reason": "判断依据"}'
    )
    user = (
        f"可选题材（genre 必须从下列中选一个，sub_genres 可再从下列中选最多 3 个细分）：\n{options}\n\n"
        "tone 用不超过 12 个英文单词描述整体视觉调性（如光线、色温、氛围），"
        "用于让同书肖像的光影与气质统一。\n\n"
        "以下是本书正文节选，请据此判断：\n---\n"
        f"{sample_text}\n---"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def infer_style(sample_text: str, llm=None) -> dict:
    """用一次 LLM 调用识别题材与调性，返回规范化的风格档案"""
    if llm is None:
        llm = get_llm_provider()
    raw = await llm.generate(
        _build_messages(sample_text), role="STRUCTURE_ANALYST", temperature=0.3, max_tokens=600
    )
    data = _extract_json(raw)
    if not data:
        data = {"genre": "其他", "tone": ""}
    return normalize_profile(data, source="auto")
