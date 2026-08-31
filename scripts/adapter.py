#!/usr/bin/env python
# adapter.py —— 工具适配层：声明式接入外部工具（不变量 17）。
#
# 外部工具不可用是常态，不是异常。每个适配器在 JSON 里声明：
#   detect   怎么判断可用（python_module / binary / file / always）
#   degrade  不可用时怎么办（fallback_builtin = 换内置兜底；
#            mark_degraded = 标记降级并上关卡说明，绝不允许静默通过）
#
# 子命令：doctor（体检） / run（执行适配器声明的命令）
import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import layout
import pio


def adapters_dir(project):
    return layout.pipeline_paths(project)["adapters"]


def load_adapters(project):
    d = adapters_dir(project)
    out = {}
    if not os.path.isdir(d):
        pio.die(layout.EXIT_CONFIG,
                "适配器目录不存在: %s（先运行 init）" % d)
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".json"):
            data = pio.load_json(os.path.join(d, fn))
            out[data["name"]] = data
    if not out:
        pio.die(layout.EXIT_CONFIG, "没有任何适配器声明文件: %s" % d)
    return out


def detect(project, spec):
    """执行适配器的 detect 声明，返回 (可用, 详情)。"""
    kind = spec.get("type", "binary")
    val = spec.get("value", "")
    if kind == "always":
        return True, "内置，恒可用"
    if kind == "python_module":
        try:
            subprocess.run([sys.executable, "-c", "import %s" % val],
                           check=True, capture_output=True, timeout=30)
            return True, "python 模块 %s 可导入" % val
        except Exception as e:
            return False, "python 模块 %s 不可用: %s" % (val, e)
    if kind == "binary":
        p = shutil.which(val)
        return (p is not None), ("位于 %s" % p) if p else "PATH 中找不到 %s" % val
    if kind == "file":
        p = os.path.join(project, val)
        return os.path.exists(p), p
    return False, "未知 detect 类型: %s" % kind


def cmd_doctor(args):
    adapters = load_adapters(args.project)
    report = []
    all_ok = True
    for name in sorted(adapters):
        spec = adapters[name]
        avail, detail = detect(args.project, spec.get("detect", {}))
        deg = spec.get("degrade", {})
        report.append({
            "name": name, "kind": spec.get("kind"),
            "available": avail, "detail": detail,
            "degrade_mode": deg.get("mode", "mark_degraded"),
            "degrade_notice": deg.get("notice", ""),
        })
        if not avail:
            all_ok = False
    # doctor 是诊断信息，永远正常退出；可用性在 JSON 里。
    pio.ok("适配器体检：%d 个，%d 个可用" % (len(report),
           sum(1 for r in report if r["available"])),
           adapters=report, all_available=all_ok)


def cmd_run(args):
    adapters = load_adapters(args.project)
    spec = adapters.get(args.name)
    if not spec:
        pio.die(layout.EXIT_CONFIG, "适配器 %s 未声明（现有: %s）"
                % (args.name, ", ".join(sorted(adapters))))
    if "run" not in spec:
        pio.die(layout.EXIT_CONFIG,
                "适配器 %s 没有声明 run 命令（它只提供探测/降级语义）" % args.name)
    avail, detail = detect(args.project, spec.get("detect", {}))
    if not avail:
        deg = spec.get("degrade", {})
        pio.die(layout.EXIT_FAIL,
                "适配器 %s 不可用（%s）。降级方案: %s"
                % (args.name, detail, deg.get("notice", "无声明")),
                degraded=True)
    cmd = list(spec["run"]) + (args.extra or [])
    exe = os.path.basename(cmd[0])
    if exe not in layout.GATE_RUNNER_ALLOWED_CMDS:
        pio.die(layout.EXIT_VIOLATION,
                "命令 %s 不在门禁执行白名单内，拒绝运行" % cmd[0])
    r = subprocess.run(cmd, cwd=args.project, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = {"adapter": args.name, "returncode": r.returncode,
           "stdout_tail": (r.stdout or "")[-2000:],
           "stderr_tail": (r.stderr or "")[-2000:]}
    if r.returncode == 0:
        pio.ok("适配器 %s 执行成功" % args.name, **out)
    else:
        pio.die(layout.EXIT_FAIL, "适配器 %s 执行未通过（退出码 %d）"
                % (args.name, r.returncode), **out)


def main():
    ap = argparse.ArgumentParser(description="工具适配层")
    ap.add_argument("--project", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("doctor"); p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("run")
    p.add_argument("--name", required=True)
    p.add_argument("extra", nargs="*")
    p.set_defaults(fn=cmd_run)
    args = ap.parse_args()
    args.project = os.path.abspath(args.project)
    args.fn(args)


if __name__ == "__main__":
    main()
