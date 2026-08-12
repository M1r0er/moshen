"""
墨参 · 创作规范库
预置网文写作规范，用于辅助分析和建议
"""


class RulesKB:
    """创作规范知识库"""

    # AI 指纹黑名单七大类
    AI_FINGERPRINT_BLACKLIST = {
        "套路化连接词": ["然而", "与此同时", "不仅...而且", "众所周知", "值得一提的是", "综上所述", "换句话说", "总而言之"],
        "万能形容词": ["无与伦比", "美轮美奂", "叹为观止", "举世瞩目", "淋漓尽致", "不可思议", "震撼人心"],
        "客观陈述腔": ["需要注意的是", "不难看出", "显而易见", "毫无疑问", "毋庸置疑", "不言而喻"],
        "凑字副词": ["非常", "十分", "相当", "极其", "特别", "分外", "格外"],
        "比喻陈词滥调": ["眼睛像星星", "笑容像阳光", "心情像过山车", "如释重负", "心如刀割", "如鲠在喉"],
        "排比堆砌": "缺乏内在逻辑递进的三段式排比",
        "大段心理分析": "紧张场景中超过段落25%的心理独白",
    }

    # 冲突值量化加权因子
    CONFLICT_WEIGHTS = {
        "核心角色状态根本性改变": 8,
        "Tier-1伏笔回收": 8,
        "Tier-1伏笔埋设": 8,
        "Tier-2伏笔回收": 5,
        "Tier-2伏笔埋设": 5,
        "奇点事件": 5,
        "核心角色参与": 2,  # 每位
        "重要实体状态改变": 3,
    }

    # 缓冲比系统参数
    PACING_PARAMS = {
        "total_buffer_ratio": 0.37,
        "usable_buffer_ratio": 0.30,
        "calibration_buffer_ratio": 0.07,
        "macro_pacing": {"起": 20, "承": 30, "转": 30, "合": 20},
        "peak_safe_spacing": 2,
    }

    # 伏笔系统参数
    FORESHADOW_PARAMS = {
        "obscurity_rate_min": 0.80,
        "core_retrieval_min": 0.70,
        "cross_volume_retrieval_min": 0.50,
        "cross_volume_foreshadow_min": 0.40,
        "intra_volume_closure_min": 0.85,
        "dormant_threshold": 50,
        "backstory_cycle": 70,
    }

    # 网文常见题材
    GENRES = [
        "仙侠", "武侠", "玄幻", "奇幻", "科幻", "都市", "言情", "古言",
        "历史", "军事", "体育", "悬疑", "恐怖", "灵异", "末日", "游戏",
        "同人", "无限流", "系统", "穿越重生", "种田", "现实", "短篇爽文",
        "耽美", "轻小说",
    ]

    # 爽文循环模型
    SATISFACTION_CYCLE = [
        "处境建立", "目标确立", "危机铺垫", "情绪拉扯",
        "尝试解决", "核心爽点", "结果反馈", "收获盘点", "新困境开启",
    ]

    # 爽点类型
    SATISFACTION_TYPES = {
        "逆袭型": "被低估后展露实力，旁观者震惊",
        "打脸型": "之前被轻视/欺辱，后续实力碾压",
        "收获型": "获得关键资源/能力/盟友",
        "成长型": "突破瓶颈、领悟新能力",
        "悬念揭示型": "伏笔回收带来的恍然大悟",
        "情感型": "关系突破、感情升温",
        "智斗型": "以智谋取胜，对手输得心服口服",
    }

    @classmethod
    def get_ai_fingerprint_checklist(cls) -> str:
        """获取 AI 指纹检查清单（用于文风诊断）"""
        lines = ["## AI 指纹七大类检查清单\n"]
        for category, items in cls.AI_FINGERPRINT_BLACKLIST.items():
            lines.append(f"### {category}")
            if isinstance(items, list):
                lines.append(f"关键词：{', '.join(items)}")
            else:
                lines.append(f"描述：{items}")
            lines.append("")
        return "\n".join(lines)

    @classmethod
    def get_conflict_score_formula(cls) -> str:
        """获取冲突值量化公式说明"""
        lines = [
            "## 冲突值量化公式\n",
            "冲突值 = 基础分(1) + Σ(权重 × 触发次数)\n",
            "### 加权因子表",
        ]
        for factor, weight in cls.CONFLICT_WEIGHTS.items():
            lines.append(f"- {factor}: +{weight}")
        lines.append("\n### 五星评级")
        lines.append("- ★☆☆☆☆ 低冲突: < 5")
        lines.append("- ★★☆☆☆ 次要冲突: 5-7")
        lines.append("- ★★★☆☆ 中度冲突: 8-11")
        lines.append("- ★★★★☆ 重要冲突: 12-15")
        lines.append("- ★★★★★ 核心峰值: ≥ 16")
        return "\n".join(lines)

    @classmethod
    def get_pacing_guidelines(cls) -> str:
        """获取节奏控制指南"""
        p = cls.PACING_PARAMS
        return f"""## 节奏控制指南

### 宏观节奏比例
- 起（铺垫建立）: {p['macro_pacing']['起']}%
- 承（发展推进）: {p['macro_pacing']['承']}%
- 转（高潮转折）: {p['macro_pacing']['转']}%
- 合（收束结局）: {p['macro_pacing']['合']}%

### 缓冲比系统
- 总缓冲比基准: {p['total_buffer_ratio']:.0%}
- 可用缓冲比: {p['usable_buffer_ratio']:.0%}
- 校准章缓冲比: {p['calibration_buffer_ratio']:.0%}
- 峰值安全间距: {p['peak_safe_spacing']} 章

### 呼吸法
约每 2-3 章高潮/进展章节后，安排 1 章缓冲章。
缓冲章类型：缓冲-代价 / 缓冲-对话 / 缓冲-线索"""

    @classmethod
    def get_foreshadow_guidelines(cls) -> str:
        """获取伏笔系统指南"""
        f = cls.FORESHADOW_PARAMS
        return f"""## 伏笔系统指南

### 三级分级
- Tier-1 战略级：影响世界结局/主角命运
- Tier-2 战役级：影响当前卷/主要派系
- Tier-3 战术级：影响局部冲突

### 核心参数
- 暗埋率建议 ≥ {f['obscurity_rate_min']:.0%}
- 核心回收率建议 ≥ {f['core_retrieval_min']:.0%}
- 跨卷回收率建议 ≥ {f['cross_volume_retrieval_min']:.0%}
- 跨卷伏笔占比 ≥ {f['cross_volume_foreshadow_min']:.0%}
- 卷内闭环率 ≥ {f['intra_volume_closure_min']:.0%}
- 沉睡伏笔预警阈值: {f['dormant_threshold']} 章
- 伏笔子链周期: {f['backstory_cycle']} 章"""

    @classmethod
    def get_all_rules(cls) -> str:
        """获取完整创作规范"""
        parts = [
            "# 墨参 · 创作规范库\n",
            cls.get_ai_fingerprint_checklist(),
            cls.get_conflict_score_formula(),
            cls.get_pacing_guidelines(),
            cls.get_foreshadow_guidelines(),
            "\n## 爽文循环模型\n",
            " → ".join(cls.SATISFACTION_CYCLE),
            "\n\n## 爽点类型\n",
        ]
        for st, desc in cls.SATISFACTION_TYPES.items():
            parts.append(f"- {st}: {desc}")
        parts.append(f"\n## 支持题材\n{', '.join(cls.GENRES)}")
        return "\n".join(parts)
