# pathnorm.py —— 路径归一化。
# 为什么单独一个模块：不变量 7 要求 "src/a.py 与 ./src\a.py 是同一个文件"。
# 归一化如果不集中实现，各脚本口径不一致，独占检查就会出现"看起来不重叠"的
# 假阴性——两个 Agent 照常互相覆盖。
import os
import sys


def norm(path, project_root=None):
    """把任意写法的路径归一化成项目内相对路径（POSIX 风格）。

    规则：
    - 反斜杠转正斜杠
    - 展开 ./ ../ 与重复分隔符
    - 绝对路径：若在项目根之内，转成相对路径；否则原样返回（供报错用）
    - Windows 上大小写折叠（盘符不敏感是文件系统事实，不是偏好）
    """
    if path is None:
        raise ValueError("路径为空")
    p = str(path).strip().replace("\\", "/")
    # 去掉 ./ 前缀与重复斜杠
    if project_root:
        root = os.path.abspath(project_root).replace("\\", "/")
        ap = p
        if not p.startswith("/"):
            # 相对路径：基于项目根展开
            ap = os.path.normpath(os.path.join(root, p)).replace("\\", "/")
        else:
            ap = os.path.normpath(p).replace("\\", "/")
        if _eq(ap, root):
            raise ValueError("不允许独占项目根目录: %s" % path)
        if _startswith(ap, root + "/"):
            p = ap[len(root) + 1:]
        elif os.path.isabs(str(path)):
            # 项目之外的绝对路径：不允许作为 owns/inputs 之外的产物声明
            raise ValueError("路径在项目之外: %s" % path)
    p = os.path.normpath(p).replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    if p in (".", ""):
        raise ValueError("路径为空或指向根: %s" % path)
    if _CASEFOLD:
        p = p.casefold()
    return p


# Windows/macOS 文件系统大小写不敏感；归一化必须跟随平台事实。
_CASEFOLD = sys.platform in ("win32", "darwin")


def _eq(a, b):
    return a.casefold() == b.casefold() if _CASEFOLD else a == b


def _startswith(a, prefix):
    return a.casefold().startswith(prefix.casefold()) if _CASEFOLD \
        else a.startswith(prefix)


def overlaps(a, b):
    """判断两个归一化路径是否冲突：同一文件，或互为父子目录。

    为什么父子目录也算冲突：独占的是"文件边界"（不变量 8），
    一张工单拥有 src/ 目录就等于拥有其中每个文件，另一张工单写
    src/a.py 必然被覆盖。
    """
    if a == b:
        return True
    return a.startswith(b + "/") or b.startswith(a + "/")
