"""
墨参 · 意图路由器
通过关键词匹配识别用户意图，路由到对应能力模块和模型角色
"""
import re
from dataclasses import dataclass


@dataclass
class IntentResult:
    """意图识别结果"""
    intent: str           # 意图类别
    model_role: str       # 推荐模型角色
    confidence: float     # 置信度 0-1
    keywords: list[str]   # 匹配到的关键词


# 意图关键词映射表
INTENT_KEYWORDS = {
    "outline_discuss": {
        "keywords": ["大纲", "主线", "剧情线", "结构", "节奏", "起承转合", "卷纲", "分卷", "宏图", "蓝图", "舞台路线", "章节规划"],
        "model_role": "STRUCTURE_ANALYST",
        "description": "大纲讨论",
    },
    "character_dev": {
        "keywords": ["人物", "角色", "主角", "反派", "配角", "性格", "动机", "背景", "弧光", "成长线", "人设", "灵魂烙印", "驱动力", "关系", "感情线"],
        "model_role": "TEXT_MASTER",
        "description": "人物完善",
    },
    "setting_build": {
        "keywords": ["设定", "世界观", "力量体系", "境界", "规则", "禁令", "时代背景", "修炼", "魔法", "系统", "金手指", "面板"],
        "model_role": "STRUCTURE_ANALYST",
        "description": "设定构建",
    },
    "foreshadow_manage": {
        "keywords": ["伏笔", "钩子", "悬念", "回收", "埋", "铺垫", "线索", "暗示", "揭示", "真相"],
        "model_role": "STRUCTURE_ANALYST",
        "description": "伏笔管理",
    },
    "logic_check": {
        "keywords": ["逻辑", "合理", "矛盾", "漏洞", "冲突", "因果", "时间线", "一致性", "连续性", "bug"],
        "model_role": "STRUCTURE_ANALYST",
        "description": "逻辑检查",
    },
    "style_analyze": {
        "keywords": ["文笔", "文风", "风格", "AI味", "去AI", "语言", "描写", "对话", "节奏感", "笔力", "遣词", "造句", "翻译腔"],
        "model_role": "TEXT_MASTER",
        "description": "文风分析",
    },
    "reference_dissect": {
        "keywords": ["拆书", "拆解", "分析这本", "学习这部", "蒸馏", "参考小说", "仿写", "借鉴"],
        "model_role": "KNOWLEDGE_BUILDER",
        "description": "拆书蒸馏",
    },
}


class IntentRouter:
    """意图路由器"""

    def __init__(self):
        self._keyword_map = INTENT_KEYWORDS

    def detect(self, user_input: str) -> IntentResult:
        """识别用户意图"""
        scores: dict[str, tuple[float, list[str]]] = {}

        for intent, config in self._keyword_map.items():
            matched = []
            score = 0.0
            for kw in config["keywords"]:
                if kw in user_input:
                    matched.append(kw)
                    # 精确匹配加分，词组越长权重越高
                    score += 1.0 + len(kw) * 0.1
            if score > 0:
                scores[intent] = (score, matched)

        if not scores:
            # 默认意图：自由对话
            return IntentResult(
                intent="free_chat",
                model_role="DIALOGUE_PARTNER",
                confidence=0.3,
                keywords=[],
            )

        # 取最高分意图
        best_intent = max(scores, key=lambda k: scores[k][0])
        best_score, best_keywords = scores[best_intent]
        total_score = sum(s for s, _ in scores.values())
        confidence = min(best_score / total_score if total_score > 0 else 0, 1.0)

        return IntentResult(
            intent=best_intent,
            model_role=self._keyword_map[best_intent]["model_role"],
            confidence=confidence,
            keywords=best_keywords,
        )

    def get_intent_description(self, intent: str) -> str:
        """获取意图描述"""
        return self._keyword_map.get(intent, {}).get("description", "自由对话")

    def list_intents(self) -> list[dict]:
        """列出所有意图类型"""
        return [
            {"intent": k, "description": v["description"], "model_role": v["model_role"]}
            for k, v in self._keyword_map.items()
        ]


# 全局单例
_router: IntentRouter | None = None


def get_intent_router() -> IntentRouter:
    global _router
    if _router is None:
        _router = IntentRouter()
    return _router
