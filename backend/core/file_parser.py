"""
墨参 · 本地文件解析器
支持 .txt / .md / .docx 文件读取与编码检测
"""
import os
import re
from pathlib import Path
import chardet


class FileParser:
    """文件解析器"""

    @staticmethod
    def detect_encoding(raw: bytes) -> str:
        """检测字节流的编码（接受 bytes 而非文件路径，避免重复读取）

        兼容旧调用：传入文件路径字符串时仍按旧逻辑读取文件前 65536 字节。
        """
        if isinstance(raw, str) and os.path.exists(raw):
            # 旧路径调用兼容
            with open(raw, "rb") as f:
                raw = f.read(65536)
        elif isinstance(raw, str):
            # 字符串但不是路径：直接返回 utf-8
            return "utf-8"
        result = chardet.detect(raw)
        encoding = result.get("encoding", "utf-8")
        # 常见编码映射修正
        if encoding and encoding.lower() in ("gb2312", "gbk"):
            encoding = "gb18030"
        return encoding or "utf-8"

    @staticmethod
    def decode_bytes(raw: bytes) -> str:
        """统一字节流解码逻辑：chardet 检测 → gb18030 修正 → utf-8 兜底

        原 routes/files.py 与 routes/knowledge.py 各自重复此逻辑，统一至此。
        """
        detected = chardet.detect(raw)
        encoding = detected.get("encoding", "utf-8")
        if encoding and encoding.lower() in ("gb2312", "gbk"):
            encoding = "gb18030"
        try:
            return raw.decode(encoding or "utf-8", errors="replace")
        except (UnicodeDecodeError, LookupError):
            return raw.decode("utf-8", errors="replace")

    @staticmethod
    def parse_upload_file(raw: bytes, filename: str) -> str:
        """解析上传文件的原始字节流，返回文本内容

        统一封装原 routes/files.py 的 upload_file 与 routes/knowledge.py 的
        parse_uploaded_file 中重复的 docx 临时文件 + chardet 编码检测逻辑。

        Args:
            raw: 文件原始字节
            filename: 文件名（用于判断扩展名）

        Returns:
            解析后的文本内容
        """
        ext = Path(filename).suffix.lower()
        if ext == ".docx":
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
                tmp.write(raw)
                tmp_path = tmp.name
            try:
                return FileParser.read_docx(tmp_path)
            finally:
                os.unlink(tmp_path)
        return FileParser.decode_bytes(raw)

    @staticmethod
    def read_text(file_path: str, encoding: str | None = None) -> str:
        """读取文本文件，自动检测编码"""
        if encoding is None:
            encoding = FileParser.detect_encoding(file_path)
        try:
            with open(file_path, "r", encoding=encoding, errors="replace") as f:
                return f.read()
        except (UnicodeDecodeError, LookupError):
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()

    @staticmethod
    def read_docx(file_path: str) -> str:
        """读取 .docx 文件"""
        try:
            from docx import Document
            doc = Document(file_path)
            return "\n\n".join(para.text for para in doc.paragraphs if para.text.strip())
        except ImportError:
            raise RuntimeError("需要安装 python-docx 才能读取 .docx 文件")

    @staticmethod
    def parse_file(file_path: str) -> str:
        """根据文件类型自动选择解析方式"""
        ext = Path(file_path).suffix.lower()
        if ext == ".docx":
            return FileParser.read_docx(file_path)
        else:
            return FileParser.read_text(file_path)

    @staticmethod
    def split_chapters(text: str) -> list[dict]:
        """从文本中识别章节

        支持：
        - 第X章/回/节 标题
        - 第X卷 标题
        - 数字.标题
        """
        chapters = []
        # 章节标题正则
        chapter_patterns = [
            r"^(第[一二三四五六七八九十百千零\d]+[章回节卷].*)$",
            r"^(\d+[\.、]\s*.+)$",
            r"^(Chapter\s+\d+.*)$",
        ]
        combined = "|".join(f"(?:{p})" for p in chapter_patterns)
        lines = text.split("\n")
        current_chapter = None
        current_content: list[str] = []

        for line in lines:
            line_stripped = line.strip()
            match = re.match(combined, line_stripped, re.MULTILINE)
            if match:
                if current_chapter is not None:
                    chapters.append({
                        "title": current_chapter,
                        "content": "\n".join(current_content).strip(),
                        "char_count": len("\n".join(current_content)),
                    })
                current_chapter = line_stripped
                current_content = []
            else:
                current_content.append(line)

        if current_chapter is not None:
            chapters.append({
                "title": current_chapter,
                "content": "\n".join(current_content).strip(),
                "char_count": len("\n".join(current_content)),
            })

        return chapters

    @staticmethod
    def get_file_info(file_path: str) -> dict:
        """获取文件基本信息"""
        stat = os.stat(file_path)
        return {
            "filename": os.path.basename(file_path),
            "size": stat.st_size,
            "size_text": FileParser._format_size(stat.st_size),
            "ext": Path(file_path).suffix.lower(),
        }

    @staticmethod
    def _format_size(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        else:
            return f"{size / (1024 * 1024):.1f} MB"
