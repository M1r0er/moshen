"""
墨参 · 统一分析层
所有文本分析器（伏笔检测、关系图谱、文风诊断、拆书等）的统一基类与实现。
"""
from analysis.base import BaseTextAnalyzer
from analysis.foreshadowing_analyzer import ForeshadowingAnalyzer
from analysis.relation_graph_analyzer import RelationGraphMultiAnalyzer

__all__ = [
    "BaseTextAnalyzer",
    "ForeshadowingAnalyzer",
    "RelationGraphMultiAnalyzer",
]
