# fslock.py —— 文件独占的操作系统级强制（不变量 7："文件独占是机制，不是约定"）。
#
# 为什么用影子锁文件而不是直接锁源码文件：
# 1) 直接锁源码文件会与编辑器/构建工具竞争，且只读文件会锁失败；
# 2) 影子锁路径由归一化路径推导，"src/a.py 与 ./src\a.py" 落进同一把锁。
#
# 诚实声明边界：进程退出锁即释放，所以跨会话的持久独占靠工单账本的
# owns 检查；本模块拦截的是"两个进程同时认领"的并发竞态。两层缺一不可：
# 只靠账本拦不住并发，只靠文件锁拦不住跨会话。
import contextlib
import errno
import os
import sys
import time


class LockError(Exception):
    """获取独占锁失败。msg 里带原因（通常含竞争方信息）。"""


def lock_file_path(pipeline_root, norm_path):
    """归一化路径 → 影子锁文件路径。"""
    safe = norm_path.replace("/", "__").replace("\\", "__")
    return os.path.join(pipeline_root, "locks", safe + ".lock")


def ledger_lock(pipeline_root):
    """账本/状态文件的全局互斥锁。

    为什么：工单/状态文件是"读-改-写"，两个进程同时写会互相覆盖——
    owns 独占锁只保护了工单声明的文件，保护不了账本自己。
    约定锁序：永远先拿账本锁，再拿 owns 锁，消灭死锁可能。
    timeout=10：账本锁是"排队"语义（并发写应等待而不是崩溃）；
    owns 锁仍是立即失败语义（那是业务冲突，不是排队）。
    """
    return FileLock(os.path.join(pipeline_root, "locks",
                                 "__ledger__.lock"), timeout=10.0)


class FileLock(object):
    """对单个影子锁文件的独占锁。with FileLock(...) as lk: ...
    timeout>0 时，拿不到锁会按 50ms 间隔重试，超时才报 LockError。"""

    def __init__(self, path, timeout=0.0):
        self.path = path
        self.timeout = timeout
        self._fd = None

    def acquire(self):
        deadline = time.time() + self.timeout
        while True:
            try:
                return self._acquire_once()
            except LockError:
                if time.time() >= deadline:
                    raise
                time.sleep(0.05)

    def _acquire_once(self):
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if os.name == "nt":
                import msvcrt
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                except OSError as e:
                    os.close(fd)
                    raise LockError(
                        "文件已被另一进程独占: %s (%s)" % (self.path, e))
            else:
                import fcntl
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as e:
                    os.close(fd)
                    raise LockError(
                        "文件已被另一进程独占: %s (%s)" % (self.path, e))
            # 记录持锁者信息，冲突报错时可读出来
            with os.fdopen(os.dup(fd), "w") as f:
                f.seek(0)
                f.truncate()
                f.write("pid=%d time=%s\n" % (os.getpid(),
                                               time.strftime("%Y-%m-%dT%H:%M:%S")))
                f.flush()
            self._fd = fd
            return self
        except LockError:
            raise
        except Exception:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise

    def release(self):
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                with contextlib.suppress(OSError):
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                with contextlib.suppress(OSError):
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            with contextlib.suppress(OSError):
                os.close(fd)

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False
