#!/usr/bin/env python
# state.py —— 环节状态机：记录七个环节走到哪、关卡谁放行的。
#
# 核心机制（不变量 2/3）：
# - G(n) 未放行，S(n+1) 无法 start（脚本层拒绝，不是提示词约定）
# - approve 前逐条校验产出真实存在；环境门禁还要求最新报告判定为通过
# - 放行人署名不得是智能体（脚本层硬拒，退出码 4）
# - 自愈轮次落盘在 state 文件里，跨会话不重置（文件即持久化）
#
# 子命令：status / start / approve / reject / heal-round / next-action
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import layout
import pathnorm
import pio
import fslock


def _paths(project):
    return layout.pipeline_paths(project)


def _load_state(project):
    return pio.load_json(_paths(project)["state"])


def _save_state(project, st):
    pio.save_json(_paths(project)["state"], st)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _check_human_name(name):
    """不变量 3：只有人能放行。脚本只记录，不替人判断。"""
    if not name or not name.strip():
        pio.die(layout.EXIT_VIOLATION,
                "缺少放行人姓名：放行必须落到具体的人，没有姓名的放行无效。")
    low = name.strip().lower()
    for bad in layout.AGENT_NAME_BLOCKLIST:
        if low == bad or bad in low.split() or low.startswith(bad + "-") \
                or low.endswith("-" + bad):
            pio.die(layout.EXIT_VIOLATION,
                    "署名 '%s' 被拒绝：智能体给自己盖章是这套东西最致命失效。"
                    "放行只能由人做出，请让用户提供真实姓名后由主会话执行。"
                    % name, exit_hint="向用户呈现关卡，等待放行决定")
    return name.strip()


def _gate_index(gate):
    return layout.GATES.index(gate)


def _previous_gate_approved(st, gate):
    i = _gate_index(gate)
    if i == 0:
        return True
    return st["gates"][layout.GATES[i - 1]]["status"] == "approved"


def _verify_artifacts(project, stage):
    """契约表逐条校验产出真实存在（不变量 2/5）。"""
    missing, verified = [], []
    tickets_path = _paths(project)["tickets"]
    for art in layout.CONTRACT.get(stage, []):
        if art == "@tickets_done":
            data = pio.load_json(tickets_path, default={"tickets": []})
            act = [t for t in data["tickets"] if t["status"] != "done"]
            if act:
                missing.append("@tickets_done（未完成: %s）"
                               % ", ".join(t["id"] for t in act))
            else:
                verified.append("@tickets_done（%d 张全部完成）"
                                % len(data["tickets"]))
            continue
        p = os.path.join(project, art)
        if os.path.exists(p) and (art.endswith("/") or os.path.isfile(p)):
            verified.append(art)
        else:
            missing.append(art)
    return verified, missing


def _check_env_gate_report(project, gate):
    """环境门禁：最新报告必须判定通过，或失败项全部持有有效豁免。"""
    env = layout.GATE_ENV[gate]
    latest = os.path.join(_paths(project)["reports"], "latest-%s.json" % env)
    if not os.path.exists(latest):
        return False, "环境门禁 %s（env=%s）没有运行记录：先执行门禁再谈放行。" \
            % (gate, env)
    rep = pio.load_json(latest)
    if rep.get("verdict") == "pass":
        return True, "门禁报告通过（%s，%s）" % (rep.get("env"),
                                              rep.get("finished_at"))
    if rep.get("verdict") == "exempted":
        return True, "门禁通过（全部为豁免项，已单独显示）"
    return False, "门禁报告判定为 %s，未通过不得放行（失败项: %s）" % (
        rep.get("verdict"),
        ", ".join(c["id"] for c in rep.get("checks", [])
                  if c["status"] not in ("pass", "exempted")))


def cmd_status(args):
    st = _load_state(args.project)
    out = {"stages": {}, "gates": {}, "self_heal": st.get("self_heal", {})}
    for s in layout.STAGES:
        out["stages"][s] = {"name": layout.STAGE_NAMES[s],
                            "status": st["stages"][s]["status"]}
    for g in layout.GATES:
        gd = st["gates"][g]
        out["gates"][g] = {
            "kind": layout.GATE_KIND[g], "status": gd["status"],
            "approved_by": gd.get("approved_by"),
            "approved_at": gd.get("approved_at"),
        }
    pio.ok("流水线状态", **out)


def cmd_start(args):
    pp = _paths(args.project)
    with fslock.ledger_lock(pp["root"]):
        st = _load_state(args.project)
        stage = args.stage
        if stage not in layout.STAGES:
            pio.die(layout.EXIT_CONFIG, "未知环节 %s" % stage)
        i = layout.STAGES.index(stage)
        if i > 0:
            prev_gate = layout.GATES[i - 1]
            if not _previous_gate_approved(st, stage_gate(stage)):
                gd = st["gates"][prev_gate]
                pio.die(layout.EXIT_VIOLATION,
                        "不能开始 %s（%s）：上一道关卡 %s 未放行（当前状态: %s）。"
                        "G(n) 未放行绝不开始 S(n+1)——这是阻断式关卡。"
                        % (stage, layout.STAGE_NAMES[stage], prev_gate,
                           gd["status"]),
                        blocked_by=prev_gate)
        if st["stages"][stage]["status"] == "approved":
            pio.die(layout.EXIT_FAIL, "%s 已放行完结，无需重新开始" % stage)
        st["stages"][stage]["status"] = "in_progress"
        st.setdefault("log", []).append(
            {"at": _now(), "event": "stage_start", "stage": stage})
        _save_state(args.project, st)
    pio.ok("%s（%s）已开始" % (stage, layout.STAGE_NAMES[stage]))


def stage_gate(stage):
    return layout.GATE_OF_STAGE[stage]


# 返工工单的角色归属：打回后由谁接返工（与环节主角色一致，评审打回由实现者修）
REWORK_ROLE = {"S1": "analyst", "S2": "implementer", "S3": "tester",
               "S4": "implementer", "S5": "implementer", "S6": "release",
               "S7": "release"}


def _add_rework_ticket(project, gate, stage, reason, seq):
    """打回时自动登记返工工单：把打回原因带进下游，而不是躺在日志里。

    返工说明必须写进 docs/rework-<gate>.md（dod 会核验存在）：
    逐条回应打回原因怎么解决的，没写清楚就不算返工完成。
    同一关卡已有未完成返工单时不重复登记（owns 会撞）。"""
    pp = _paths(project)
    tickets = pio.load_json(pp["tickets"], default={"tickets": []})
    note = "docs/rework-%s.md" % gate.lower()
    for t in tickets["tickets"]:
        if t["status"] != "done" and note in t["owns"]:
            return None  # 已有未完成返工单，不重复登记
    tid = "R-%s-%d" % (gate, seq)
    brief = reason if len(reason) <= 40 else reason[:39] + "…"
    tickets["tickets"].append({
        "id": tid, "title": "返工 %s：%s" % (gate, brief),
        "role": REWORK_ROLE[stage], "inputs": [],
        "owns": [note], "requires": [], "dod": [note],
        "status": "open", "claimed_by": None, "claimed_at": None,
        "done_at": None,
    })
    pio.save_json(pp["tickets"], tickets)
    return tid


def cmd_approve(args):
    pp = _paths(args.project)
    with fslock.ledger_lock(pp["root"]):
        st = _load_state(args.project)
        gate = args.gate
        if gate not in layout.GATES:
            pio.die(layout.EXIT_CONFIG, "未知关卡 %s" % gate)
        name = _check_human_name(args.by)
        # 顺序性：不许跳过未放行的前序关卡
        if not _previous_gate_approved(st, gate):
            i = _gate_index(gate)
            prev = layout.GATES[i - 1]
            pio.die(layout.EXIT_VIOLATION,
                    "不能放行 %s：前序关卡 %s 尚未放行（当前状态: %s）。"
                    "关卡是阻断式的，不允许跳关。"
                    % (gate, prev, st["gates"][prev]["status"]),
                    blocked_by=prev)
        stage = layout.STAGES[_gate_index(gate)]
        if st["stages"][stage]["status"] not in ("in_progress",):
            pio.die(layout.EXIT_VIOLATION,
                    "不能放行 %s：环节 %s 尚未开始（状态 %s）。"
                    "没有产出就没有可放行的东西。"
                    % (gate, stage, st["stages"][stage]["status"]))
        # 产出真实存在校验（不变量 2）
        verified, missing = _verify_artifacts(args.project, stage)
        if missing:
            pio.die(layout.EXIT_FAIL,
                    "放行被拒绝：%s 的产出缺失 %s。放行前产出必须真实存在。"
                    % (stage, ", ".join(missing)), missing=missing)
        # 环境门禁还要求报告判定通过
        if layout.GATE_KIND[gate] == "env":
            good, msg = _check_env_gate_report(args.project, gate)
            if not good:
                pio.die(layout.EXIT_FAIL, "放行被拒绝：%s" % msg)
        gd = st["gates"][gate]
        gd["status"] = "approved"
        gd["approved_by"] = name
        gd["approved_at"] = _now()
        gd["verified_artifacts"] = verified
        st["stages"][stage]["status"] = "approved"
        st.setdefault("log", []).append(
            {"at": _now(), "event": "gate_approved", "gate": gate,
             "by": name})
        _save_state(args.project, st)
    pio.ok("关卡 %s 已放行（放行人: %s，时间: %s）" % (gate, name, gd["approved_at"]),
           verified=verified,
           next_stage=(layout.STAGES[_gate_index(gate) + 1]
                       if _gate_index(gate) + 1 < len(layout.STAGES) else None))


def cmd_reject(args):
    pp = _paths(args.project)
    with fslock.ledger_lock(pp["root"]):
        st = _load_state(args.project)
        gate = args.gate
        if gate not in layout.GATES:
            pio.die(layout.EXIT_CONFIG, "未知关卡 %s" % gate)
        name = _check_human_name(args.by)
        stage = layout.STAGES[_gate_index(gate)]
        gd = st["gates"][gate]
        gd["status"] = "rejected"
        gd.setdefault("rejections", []).append(
            {"by": name, "at": _now(), "reason": args.reason})
        st["stages"][stage]["status"] = "in_progress"  # 打回返工
        st.setdefault("log", []).append(
            {"at": _now(), "event": "gate_rejected", "gate": gate, "by": name,
             "reason": args.reason})
        _save_state(args.project, st)
        rework_id = _add_rework_ticket(args.project, gate, stage, args.reason,
                                       len(gd["rejections"]))
    msg = "关卡 %s 已打回（打回人: %s），%s 返工中。原因: %s" % (
        gate, name, stage, args.reason)
    if rework_id:
        msg += "。已自动登记返工工单 %s（返工说明写入 docs/rework-%s.md）" % (
            rework_id, gate.lower())
    else:
        msg += "。该关卡已有未完成返工工单，未重复登记"
    pio.ok(msg)


def cmd_heal_round(args):
    """自愈轮次 +1 并落盘。上限 2 轮：超过就要求停下交给用户。"""
    pp = _paths(args.project)
    with fslock.ledger_lock(pp["root"]):
        st = _load_state(args.project)
        heal = st.setdefault("self_heal", {})
        n = heal.get(args.gate, 0) + 1
        heal[args.gate] = n
        st.setdefault("log", []).append(
            {"at": _now(), "event": "self_heal_round", "gate": args.gate,
             "round": n})
        _save_state(args.project, st)
    if n >= layout.SELF_HEAL_MAX_ROUNDS:
        pio.die(layout.EXIT_FAIL,
                "关卡 %s 自愈已达 %d 轮。两次针对性修复都没过，说明对失败原因的"
                "理解很可能是错的；第三轮多半只是换个姿势撞同一堵墙，而用户"
                "看不到这个循环在烧钱。停止自愈，把失败项作为需决策项呈现给用户。"
                % (args.gate, n), rounds=n, stop=True)
    pio.ok("关卡 %s 自愈第 %d 轮已记录（上限 %d）"
           % (args.gate, n, layout.SELF_HEAL_MAX_ROUNDS), rounds=n)


def cmd_next_action(args):
    """机器可读的下一步指引：主会话据此决定派发/呈现/停下。"""
    st = _load_state(args.project)
    heal = st.get("self_heal", {})
    for i, stage in enumerate(layout.STAGES):
        gate = layout.GATES[i]
        if st["gates"][gate]["status"] == "approved":
            continue
        out = {"stage": stage, "stage_name": layout.STAGE_NAMES[stage],
               "gate": gate, "gate_kind": layout.GATE_KIND[gate]}
        # 返工中的环节：把最近一次打回原因带出来，主会话不用翻日志
        rejs = st["gates"][gate].get("rejections")
        if rejs:
            out["last_rejection"] = rejs[-1]
        if heal.get(gate, 0) >= layout.SELF_HEAL_MAX_ROUNDS \
                and layout.GATE_KIND[gate] == "env":
            out["action"] = "stop_for_user"
            out["reason"] = "自愈轮次已达上限，需用户决策"
        elif st["stages"][stage]["status"] in ("pending", "rejected"):
            out["action"] = "start_stage"
        elif layout.GATE_KIND[gate] == "env":
            out["action"] = "run_env_gate"
            out["env"] = layout.GATE_ENV.get(gate)
        else:
            out["action"] = "present_to_user"
        pio.ok("下一步", **out)
        return
    pio.ok("全部关卡已放行：流水线到达发布终点。发布执行属第三档，"
           "把 reports/publish-checklist.md 交给用户，由人执行。",
           action="publish_by_human")


def main():
    ap = argparse.ArgumentParser(description="环节状态机")
    ap.add_argument("--project", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status"); p.set_defaults(fn=cmd_status)
    p = sub.add_parser("start")
    p.add_argument("--stage", required=True); p.set_defaults(fn=cmd_start)
    p = sub.add_parser("approve")
    p.add_argument("--gate", required=True)
    p.add_argument("--by", required=True)
    p.set_defaults(fn=cmd_approve)
    p = sub.add_parser("reject")
    p.add_argument("--gate", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--reason", required=True)
    p.set_defaults(fn=cmd_reject)
    p = sub.add_parser("heal-round")
    p.add_argument("--gate", required=True)
    p.set_defaults(fn=cmd_heal_round)
    p = sub.add_parser("next-action"); p.set_defaults(fn=cmd_next_action)

    args = ap.parse_args()
    args.project = os.path.abspath(args.project)
    args.fn(args)


if __name__ == "__main__":
    main()
