# pio.py —— 流水线数据的原子读写与统一报错出口。
# 为什么所有落盘都走这里：状态/账本/配置一旦写一半崩溃，流水线会停在
# 一个"看起来在走其实数据损坏"的状态；原子替换保证任何时刻文件完整。
import json
import os
import sys
import tempfile


def _force_utf8_console():
    """为什么：GBK 终端上输出中文/✅ 会直接崩溃（实跑中真实发生过，
    当时靠外部 PYTHONIOENCODING 绕过）。根因修在脚本入口，
    不依赖用户记得设环境变量。重定向到管道/文件时同样生效。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass  # 被宿主接管的标准流没有 reconfigure，容忍


_force_utf8_console()


def load_json(path, default=None):
    if not os.path.exists(path):
        if default is not None:
            return default
        die(3, "文件不存在: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        die(3, "文件损坏或不可读 %s: %s" % (path, e))


def save_json(path, data):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)  # 原子替换
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def die(code, message, **extra):
    """统一报错出口：机器可读 + 人类可读同时给出。

    为什么退出码严格区分（不变量：区分"未通过"和"配置错"）：
    调用方（主会话/门禁执行者）必须能分清"这次检查没达标"（2，要呈现给用户）
    和"流水线自己装错了"（3，检查根本没跑，绝不能当成通过），
    以及"违反不变量"（4，必须停下并说明原因）。
    """
    payload = {"ok": False, "exit_code": code, "error": message}
    payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.exit(code)


def ok(message, **extra):
    payload = {"ok": True, "message": message}
    payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
