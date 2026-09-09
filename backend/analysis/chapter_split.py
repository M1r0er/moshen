"""
墨参 · 章节分割器
按章节边界切割长文本（全本小说），绝不在章节中间切断。
支持的章节标题形式：
  第一章 初遇 / 第1章 / 第一百二十三回 / 第3节 / 第二卷 风云再起 / 上篇
  Chapter 1 / CHAPTER 2: xxx
未检测到章节结构时返回 None（调用方回退到整文件/按字数分批）。
移植自星图 server/chapterSplit.js
"""
import re

# 章节标题正则：第[数字/汉字数词]+[章节回卷部篇话集]
CN_CHAPTER = re.compile(r"^第\s*[0-9一二三四五六七八九十百千万零两]+\s*[章节回卷部篇话集]")
# 英文章节：Chapter 1、CHAPTER 12、Ch. 3
EN_CHAPTER = re.compile(r"^chapter\s*[#.:]?\s*\d+", re.IGNORECASE)
# 卷/部（无数字）：第一卷 → 已被上面覆盖；纯"上卷/中卷/下卷"、单独的"卷首/楔子/序章/尾声/番外"
SECTION = re.compile(
    r"^(卷首|楔子|序章|序言|引子|尾声|番外|上卷|中卷|下卷|第一部|第二部|第三部|第[一二三四五六七八九十]+部)"
)


def is_chapter_heading(line: str) -> bool:
    """判断一行是否为章节标题"""
    t = line.strip()
    if not t:
        return False
    if CN_CHAPTER.search(t):
        return True
    if EN_CHAPTER.search(t):
        return True
    if SECTION.search(t) and len(t) <= 20:
        return True
    return False


def split_by_chapters(content: str) -> list[dict] | None:
    """按章节边界切割文本

    Args:
        content: 全文

    Returns:
        章节块列表（保持原顺序）；若未检测到章节结构返回 None
        [{"heading": str|None, "text": str}, ...]
    """
    if not content or not isinstance(content, str):
        return None

    lines = content.split("\n")
    chapters = []
    cur_heading = None
    cur: list[str] = []

    for line in lines:
        if is_chapter_heading(line):
            if cur_heading is not None or len(cur) > 0:
                chapters.append({"heading": cur_heading, "text": "\n".join(cur)})
            cur_heading = line.strip()
            cur = []
        else:
            cur.append(line)

    # 收尾
    if cur_heading is not None or len(cur) > 0:
        chapters.append({"heading": cur_heading, "text": "\n".join(cur)})

    # 至少 2 个章节块才算检测到章节结构（避免把零星标题误判为整本结构）
    if len(chapters) < 2:
        return None

    # 去除完全空白的章节块
    non_empty = [c for c in chapters if c["text"].strip() or c["heading"]]
    return non_empty if len(non_empty) >= 2 else None
