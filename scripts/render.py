#!/usr/bin/env python
# render.py —— 渲染层：派发包与关卡卡片必须由工具渲染，不许手工拼。
#
# 为什么（不变量 9）：派发包 = 角色说明全文 + 只读/只写/判据 + 禁区清单。
# 手工拼的问题不在于麻烦，在于"这次忘了写禁区"不会有任何提示，只会在
# 某天以"有个 Agent 把门禁阈值改低了"的形式暴露。工具渲染保证禁区清单
# 每次都从账本实时计算，缺不了。
#
# 子命令：
#   pack      --ticket T   渲染派发包（角色全文 + 边界 + 禁区 + 隔离声明）
#   gate-card --report F   把门禁报告渲染成固定格式的关卡卡片
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import layout
import pio

# 永不进入任何派发包的禁区（第三档 + 流水线自身状态）
HARD_FORBIDDEN = [
    ".pipeline/**            流水线状态/账本/门禁配置——任何子 Agent 不得写",
    "config 中门禁阈值与检查  改门禁配置属于生成待批项，只能由主会话呈交用户",
    "真实凭据/证书/令牌       只允许生成占位符与注入机制",
    "云账号/计费/域名         绝不触碰",
    "生产环境实际执行         绝不触碰",
    "不可逆操作              删库/清缓存/批量订正/强推历史——绝不触碰",
    "法律合规文本            绝不生成",
]


def _load_tickets(project):
    pp = layout.pipeline_paths(project)
    return pio.load_json(pp["tickets"], default={"tickets": []})


def cmd_pack(args):
    project = args.project
    pp = layout.pipeline_paths(project)
    data = _load_tickets(project)
    t = next((x for x in data["tickets"] if x["id"] == args.ticket), None)
    if not t:
        pio.die(layout.EXIT_CONFIG, "工单 %s 不存在" % args.ticket)
    if t["status"] not in ("open", "claimed"):
        pio.die(layout.EXIT_FAIL,
                "工单 %s 状态为 %s，无需派发" % (args.ticket, t["status"]))

    role_file = os.path.join(pp["roles"], t["role"] + ".md")
    if not os.path.exists(role_file):
        pio.die(layout.EXIT_CONFIG,
                "角色说明缺失: %s（init 应已安装；缺失说明安装不完整）"
                % role_file)
    with open(role_file, "r", encoding="utf-8") as f:
        role_md = f.read()

    cfg = pio.load_json(pp["config"])
    caps = cfg.get("capabilities", {})

    # 禁区 = 其他活跃工单的 owns（实时从账本算，不靠记忆）
    forbidden_owns = []
    for other in data["tickets"]:
        if other["id"] == t["id"] or other["status"] == "done":
            continue
        for p in other["owns"]:
            forbidden_owns.append("%s（工单 %s 正在做：%s）"
                                  % (p, other["id"], other["title"]))

    # 隔离声明（不变量 10）：按角色写明"不给什么"
    isolation = []
    if t["role"] == "reviewer":
        isolation += [
            "不给你实现者的思路/说明文档：只给 diff 文件 + 验收标准 + 契约。",
            "读了实现者的解释再看代码，你会沿着他的思路验证他的思路。",
            "你没有编辑工具：发现问题只记录，不许顺手改。",
        ]
    if t["role"] == "tester":
        isolation += [
            "不给你实现代码：只给验收标准 + 接口契约。",
            "照着实现写的测试会把实现里的理解错误固化成'预期行为'。",
            "只写 owns 列出的测试路径，不许改源码；跑挂了改测试前先怀疑实现。",
        ]
    if t["role"] == "gate-runner":
        isolation += [
            "你没有任何写工具：不可能'顺手把门禁弄绿'。",
            "原始日志全部落到证据文件，你只回传关卡卡片：判定+计数+归类+证据路径。",
            "你不判定放行，也没有问用户的能力；放行是主会话对用户做的事。",
        ]

    whitelist = layout.ROLE_TOOL_POLICY[t["role"]]["allow"]
    tool_note = ""
    if not caps.get("tool_whitelist"):
        tool_note = (
            "\n> ⚠ **隔离强度声明**：当前平台不支持给子 Agent 限定工具，"
            "工具白名单退回为纯约定（本派发包已如实打印此声明）。"
            "评审/门禁结论的可信度按'约定级隔离'对待，关卡呈现时会再次注明。\n")

    lines = [
        "# 派发包 %s —— %s" % (t["id"], t["title"]),
        "",
        "- 生成时间: %s（工具渲染，非手工拼接）" % time.strftime("%Y-%m-%dT%H:%M:%S"),
        "- 角色: %s" % t["role"],
        "- 工具白名单（平台支持时按此强制）: %s" % ", ".join(whitelist),
        tool_note,
        "## 一、角色职责说明（全文）",
        "",
        role_md,
        "",
        "## 二、只读输入（inputs）——只读这些，读多了会被无关信息带偏",
        "",
    ]
    lines += ["- %s" % p for p in (t["inputs"] or ["（无）"])]
    lines += [
        "",
        "## 三、只写范围（owns）——只写这些，且独占",
        "",
    ]
    lines += ["- %s" % p for p in t["owns"]]
    lines += [
        "",
        "## 四、完成判据（dod）——done 时脚本会逐条核验存在性",
        "",
    ]
    lines += ["- %s" % p for p in (t["dod"] or ["（由角色说明定义）"])]
    if t["requires"]:
        lines += ["", "## 前置工单（已全部完成才可认领）", ""]
        lines += ["- %s" % r for r in t["requires"]]
    lines += [
        "",
        "## 五、禁区清单（实时从账本计算）",
        "",
    ]
    lines += ["- %s" % f for f in forbidden_owns] if forbidden_owns \
        else ["- （当前无其他活跃工单的 owns）"]
    lines += ["", "### 全局禁区（任何角色）", ""]
    lines += ["- %s" % f for f in HARD_FORBIDDEN]
    if isolation:
        lines += ["", "## 六、上下文隔离声明（必读）", ""]
        lines += ["- %s" % s for s in isolation]
    out_path = os.path.join(pp["packs"], "%s-pack.md" % t["id"])
    os.makedirs(pp["packs"], exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    pio.ok("派发包已渲染: %s" % os.path.relpath(out_path, project),
           pack=os.path.relpath(out_path, project).replace("\\", "/"),
           isolation_degraded=not caps.get("tool_whitelist"))


def cmd_diff(args):
    """预渲染评审面：把已完成工单（除测试角色）的 owns 文件汇成一份带行号的
    diff 文件。为什么机制化：评审者只能看这份文件——"这次评审看了哪些行"
    成为可审计的产物；S4 放行校验会检查它存在，忘了渲染就过不了关。"""
    project = args.project
    pp = layout.pipeline_paths(project)
    data = _load_tickets(project)
    paths, seen = [], set()
    for t in data["tickets"]:
        if t["status"] != "done" or t["role"] == "tester":
            continue
        for p in t["owns"]:
            if p not in seen:
                seen.add(p)
                paths.append(p)
    if not paths:
        pio.die(layout.EXIT_FAIL,
                "没有可渲染的评审面：没有已完成（非测试角色）的工单。"
                "评审对象必须是实现产出，先完成实现工单。")
    out_rel = args.out
    out_path = os.path.join(project, out_rel)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    lines = ["# 评审面（工具渲染）| 生成于 %s" % time.strftime(
        "%Y-%m-%dT%H:%M:%S"),
        "# 评审者只能看本文件内的内容；范围外的代码一律不得评审判定。", ""]
    count = 0
    for p in paths:
        full = os.path.join(project, p)
        targets = []
        if os.path.isdir(full):
            for dp, _, fns in os.walk(full):
                for fn in sorted(fns):
                    targets.append(os.path.join(dp, fn))
        elif os.path.isfile(full):
            targets = [full]
        for fp in targets:
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError):
                continue  # 二进制/不可读文件不进评审面
            rel = os.path.relpath(fp, project).replace("\\", "/")
            lines.append("=== %s ===" % rel)
            lines.extend("%5d| %s" % (i, ln) for i, ln in enumerate(
                content.splitlines(), 1))
            lines.append("")
            count += 1
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    pio.ok("评审面已渲染: %s（%d 个文件）" % (out_rel, count),
           path=out_rel, files=count)


def cmd_gate_card(args):
    rep = pio.load_json(args.report)
    V = {"pass": "✅ 通过", "fail": "❌ 未通过", "exempted": "🟡 通过（全为豁免）"}
    counts = rep.get("counts", {})
    lines = [
        "# 关卡卡片 %s（环境 %s）" % (rep["gate"], rep["env"]),
        "",
        "## 判定: %s" % V.get(rep["verdict"], rep["verdict"]),
        "",
        "计数: 通过 %s | 未通过 %s | 已豁免 %s | 降级 %s"
        % (counts.get("pass", 0), counts.get("fail", 0),
           counts.get("exempted", 0), counts.get("degraded", 0)),
        "",
        "## 逐项",
        "",
    ]
    cls_name = {"self_heal": "自愈", "needs_decision": "需决策",
                "out_of_bounds": "越界"}
    for c in rep.get("checks", []):
        if c["status"] == "pass":
            lines.append("- ✅ %s" % c["id"])
        elif c["status"] == "exempted":
            ex = c.get("exemption", {})
            lines.append("- 🟡 %s【已豁免，非通过】批准人 %s，到期 %s，理由: %s"
                         % (c["id"], ex.get("by"), ex.get("until"),
                            ex.get("reason")))
        else:
            cls = cls_name.get(c.get("classification"), "?")
            lines.append("- ❌ %s [%s] %s | 证据: %s"
                         % (c["id"], cls, c.get("detail", ""),
                            c.get("evidence", "-")))
    if rep.get("degraded"):
        lines += ["", "## 降级声明（本次绿色/红色里这些检查没按原样跑）", ""]
        lines += ["- %s" % d for d in rep["degraded"]]
    drift = rep.get("config_drift", {})
    if drift.get("present"):
        lines += ["", "## ⚠ 门禁配置漂移", "", "- %s" % drift["notice"]]
    if rep.get("heal_exhausted"):
        lines += ["", "## ⚠ 自愈轮次已达上限（%d 轮）：失败项不再自愈，需用户决策"
                  % rep.get("self_heal_rounds", 0)]
    lines += ["", "报告: %s | 生成于 %s"
              % (rep.get("report_path", args.report),
                 rep.get("finished_at", ""))]
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description="渲染层")
    ap.add_argument("--project", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack")
    p.add_argument("--ticket", required=True)
    p.set_defaults(fn=cmd_pack)
    p = sub.add_parser("gate-card")
    p.add_argument("--report", required=True)
    p.set_defaults(fn=cmd_gate_card)
    p = sub.add_parser("diff")
    p.add_argument("--out", default="reports/changes.diff")
    p.set_defaults(fn=cmd_diff)
    args = ap.parse_args()
    args.project = os.path.abspath(args.project)
    args.fn(args)


if __name__ == "__main__":
    main()
