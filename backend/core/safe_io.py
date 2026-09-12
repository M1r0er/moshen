"""
墨参 · 安全文件 IO

提供两类能力，用于替代直接 `path.write_text(...)`：

1. 原子写（atomic_write_text / atomic_write_json）：
   先写同目录临时文件并 fsync，再用 os.replace 原子替换目标文件。
   进程被中断或并发写入时，目标文件要么是旧内容、要么是新内容，
   不会出现"半截 JSON / 空文件"导致的文件损坏。

2. 读改写串行化（locked_write / write_lock / update_json）：
   JSON 存储普遍是"整文件读 → 改 → 整文件写回"。并发请求下两次读改写
   会互相覆盖（丢失更新）。这里用进程内可重入锁把读改写序列串行化。
   单用户桌面应用场景下，一个全局写锁即可消除竞态，代价可控。
"""
import functools
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

# 全局写锁：用于串行化"读 → 改 → 写回"的复合操作（可重入）
_WRITE_LOCK = threading.RLock()

# 路径级锁：需要针对单个文件精细串行化时使用
_path_locks: dict[str, threading.RLock] = {}
_path_locks_guard = threading.Lock()


@contextmanager
def write_lock():
    """获取全局写锁（可重入），用于保护 JSON 存储的读改写序列"""
    _WRITE_LOCK.acquire()
    try:
        yield
    finally:
        _WRITE_LOCK.release()


def locked_write(fn: Callable) -> Callable:
    """方法装饰器：在整个方法执行期间持有全局写锁

    用于标记"读 → 改 → 写回"的复合写入方法，使其并发安全。
    锁可重入，因此被装饰方法之间可以互相调用。
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _WRITE_LOCK:
            return fn(*args, **kwargs)
    return wrapper


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.absolute())
    with _path_locks_guard:
        lock = _path_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _path_locks[key] = lock
        return lock


@contextmanager
def path_lock(path):
    """获取某个文件路径的进程内可重入锁"""
    lock = _lock_for(Path(path))
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def atomic_write_text(path, content: str, encoding: str = "utf-8") -> None:
    """原子写入文本文件：同目录临时文件 + fsync + os.replace"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(target.parent), prefix=".tmp_", suffix=target.suffix or ".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path, data: Any, indent: int = 2) -> None:
    """原子写入 JSON 文件（UTF-8，不转义非 ASCII）"""
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=indent))


def read_json(path, default: Any = None) -> Any:
    """安全读取 JSON，文件不存在或损坏时返回 default"""
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return default


def update_json(path, mutate: Callable, default: Any = None) -> Any:
    """在全局写锁保护下执行 read-modify-write，并原子写回

    Args:
        path: JSON 文件路径
        mutate: 接收当前数据（缺失/损坏时为 default 的深拷贝），原地修改后返回即可
        default: 文件不存在时的初始值

    Returns:
        mutate 的返回值（返回 None 时使用修改后的数据）
    """
    p = Path(path)
    with write_lock():
        data = read_json(p, None)
        if data is None:
            data = json.loads(json.dumps(default, ensure_ascii=False)) if default is not None else {}
        result = mutate(data)
        out = data if result is None else result
        atomic_write_json(p, out)
        return out
