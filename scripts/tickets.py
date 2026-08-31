#!/usr/bin/env python
# tickets.py —— 工单账本：多 Agent 协作的唯一载体（不变量 6）。
#
# 每张工单四个字段撑起整个协作：
#   inputs   只读这些；读多了会被无关信息带偏
#   owns     只写这些，且独占；写多了会覆盖别人的工作
#   requires 前置工单；拿半成品输入会在很远的地方失败
#   dod      完成判据；没有判据，"做完了"只是一句自述
#
# 子命令：add / claim / done / next / status / conflicts / brief
# 退出码：0 成功；2 冲突/判据不满足（业务性未通过）；3 配置/用法错误。
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import layout
import pathnorm
import pio
import fslock


def _ledger_path(project):
    return layout.pipeline_paths(project)["tickets"]


def _load(project):
    return pio.load_json(_ledger_path(project), default={"tickets": []})


def _save(project, data):
    pio.save_json(_ledger_path(project), data)


def _find(data, tid):
    for t in data["tickets"]:
        if t["id"] == tid:
            return t
    return None


def _norm_list(paths, project):
    out = []
    for p in paths:
        try:
            out.append(pathnorm.norm(p, project))
        except ValueError as e:
            pio.die(layout.EXIT_CONFIG, "路径非法: %s" % e)
    return out


def _owns_conflict(data, ticket_id, owns):
    """在未完成工单里查 owns 重叠。返回 (冲突工单, 冲突路径) 或 (None, None)。"""
    for other in data["tickets"]:
        if other["id"] == ticket_id or other["status"] == "done":
            continue
        for a in owns:
            for b in other["owns"]:
                if pathnorm.overlaps(a, b):
                    return other, (a, b)
    return None, None


def cmd_add(args):
    pp = layout.pipeline_paths(args.project)
    with fslock.ledger_lock(pp["root"]):
        data = _load(args.project)
        if _find(data, args.id):
            pio.die(layout.EXIT_FAIL, "工单 %s 已存在" % args.id)
        if args.role not in layout.ROLE_TOOL_POLICY:
            pio.die(layout.EXIT_CONFIG,
                    "未知角色 %s（可用: %s）" % (args.role,
                    ", ".join(sorted(layout.ROLE_TOOL_POLICY))))
        owns = _norm_list(args.owns, args.project)
        inputs = _norm_list(args.inputs, args.project) if args.inputs else []
        dod = _norm_list(args.dod, args.project) if args.dod else []
        # 拆分边界是文件边界（不变量 8）：登记时就查重，别等认领时才发现撞车。
        holder, pair = _owns_conflict(data, args.id, owns)
        if holder:
            pio.die(layout.EXIT_FAIL,
                    "owns 与工单 %s（%s，状态 %s）冲突: %s ⟷ %s。"
                    "拆分边界是文件边界：拆不开就改成串行（把 %s 写进 requires）。"
                    % (holder["id"], holder["title"], holder["status"],
                       pair[0], pair[1], holder["id"]),
                    conflicts_with=holder["id"])
        for r in (args.requires or []):
            if not _find(data, r):
                pio.die(layout.EXIT_CONFIG,
                        "前置工单 %s 不存在（先用 add 登记，或去掉该依赖）" % r)
        ticket = {
            "id": args.id, "title": args.title, "role": args.role,
            "inputs": inputs, "owns": owns, "requires": args.requires or [],
            "dod": dod, "status": "open",
            "claimed_by": None, "claimed_at": None, "done_at": None,
        }
        data["tickets"].append(ticket)
        _save(args.project, data)
    pio.ok("工单 %s 已登记" % args.id, ticket=ticket)


def cmd_claim(args):
    pp = layout.pipeline_paths(args.project)
    # 锁序：先账本锁再 owns 锁（全局约定，消灭死锁）
    with fslock.ledger_lock(pp["root"]):
        data = _load(args.project)
        t = _find(data, args.id)
        if not t:
            pio.die(layout.EXIT_CONFIG, "工单 %s 不存在" % args.id)
        if t["status"] == "done":
            pio.die(layout.EXIT_FAIL, "工单 %s 已完成，不能认领" % args.id)
        if t["status"] == "claimed" and t["claimed_by"] != args.by:
            pio.die(layout.EXIT_FAIL,
                    "工单 %s 已被 %s 认领（%s）" % (args.id, t["claimed_by"],
                    t["claimed_at"]))
        # 前置必须全部完成（不变量 6：半成品输入会在很远的地方失败）
        missing = [r for r in t["requires"]
                   if not (_find(data, r) or {}).get("status") == "done"]
        if missing:
            pio.die(layout.EXIT_FAIL,
                    "前置工单未完成，拒绝认领: %s" % ", ".join(missing),
                    pending_requires=missing)
        # 独占检查（不变量 7）：账本层查重，报告占着的人是谁
        holder, pair = _owns_conflict(data, t["id"], t["owns"])
        if holder:
            pio.die(layout.EXIT_FAIL,
                    "拒绝认领 %s：文件 %s 被工单 %s（%s，状态 %s）占着。"
                    % (args.id, pair[0], holder["id"],
                       holder.get("claimed_by") or "未认领", holder["status"]),
                    conflicts_with=holder["id"], path=pair[0])
        # OS 文件锁层（并发竞态）：全部 owns 同时拿到锁才算认领成功
        locks = []
        try:
            for p in t["owns"]:
                lp = fslock.lock_file_path(pp["root"], p)
                lk = fslock.FileLock(lp)
                try:
                    lk.acquire()
                except fslock.LockError as e:
                    for x in locks:
                        x.release()
                    pio.die(layout.EXIT_FAIL,
                            "拒绝认领 %s：%s" % (args.id, e))
                locks.append(lk)
            t["status"] = "claimed"
            t["claimed_by"] = args.by
            t["claimed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            _save(args.project, data)
        finally:
            for lk in locks:
                lk.release()
    pio.ok("工单 %s 已由 %s 认领" % (args.id, args.by), ticket=t)


def cmd_unclaim(args):
    """认领后子 Agent 崩溃/中断的恢复通道：退回 open 让下一个人接。
    为什么需要：claimed 状态若无人处理，工单会永久卡死。"""
    pp = layout.pipeline_paths(args.project)
    with fslock.ledger_lock(pp["root"]):
        data = _load(args.project)
        t = _find(data, args.id)
        if not t:
            pio.die(layout.EXIT_CONFIG, "工单 %s 不存在" % args.id)
        if t["status"] != "claimed":
            pio.die(layout.EXIT_FAIL,
                    "工单 %s 状态为 %s，只有 claimed 可以退回" % (args.id,
                    t["status"]))
        t["status"] = "open"
        t["claimed_by"] = None
        t["claimed_at"] = None
        _save(args.project, data)
    pio.ok("工单 %s 已退回待认领（原认领者中断恢复）" % args.id, ticket=t)


def cmd_done(args):
    pp = layout.pipeline_paths(args.project)
    with fslock.ledger_lock(pp["root"]):
        data = _load(args.project)
        t = _find(data, args.id)
        if not t:
            pio.die(layout.EXIT_CONFIG, "工单 %s 不存在" % args.id)
        if t["status"] != "claimed":
            pio.die(layout.EXIT_FAIL,
                    "工单 %s 状态为 %s，只有 claimed 状态可以标记完成"
                    % (args.id, t["status"]))
        # 完成判据（dod）必须真实存在（不变量 2 同款校验："做完了"不能只是自述）
        missing = [p for p in t["dod"]
                   if not os.path.exists(os.path.join(args.project, p))]
        if missing:
            pio.die(layout.EXIT_FAIL,
                    "工单 %s 的 dod 产出缺失，拒绝标记完成: %s"
                    % (args.id, ", ".join(missing)), missing=missing)
        t["status"] = "done"
        t["done_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _save(args.project, data)
    pio.ok("工单 %s 已完成（dod 已核验）" % args.id, ticket=t)


def cmd_next(args):
    data = _load(args.project)
    out = []
    for t in data["tickets"]:
        if t["status"] != "open":
            continue
        if not all((_find(data, r) or {}).get("status") == "done"
                   for r in t["requires"]):
            continue
        holder, _ = _owns_conflict(data, t["id"], t["owns"])
        if holder:
            continue  # 有活跃冲突的工单不许出现在可认领列表里
        out.append(t)
    pio.ok("可认领工单 %d 张" % len(out), tickets=out)


def cmd_status(args):
    data = _load(args.project)
    counts = {"open": 0, "claimed": 0, "done": 0}
    for t in data["tickets"]:
        counts[t["status"]] += 1
    pio.ok("工单状态", counts=counts, tickets=data["tickets"])


def cmd_conflicts(args):
    data = _load(args.project)
    found = []
    act = [t for t in data["tickets"] if t["status"] != "done"]
    for i in range(len(act)):
        for j in range(i + 1, len(act)):
            for a in act[i]["owns"]:
                for b in act[j]["owns"]:
                    if pathnorm.overlaps(a, b):
                        found.append({"tickets": [act[i]["id"], act[j]["id"]],
                                      "paths": [a, b]})
    if found:
        pio.die(layout.EXIT_FAIL,
                "存在 %d 处 owns 重叠，必须先重新拆分再并行" % len(found),
                conflicts=found)
    pio.ok("无 owns 冲突")


def cmd_brief(args):
    data = _load(args.project)
    lines = []
    for t in data["tickets"]:
        req = ",".join(t["requires"]) or "-"
        lines.append("[%s] %-6s %s | role=%s requires=%s owns=%d dod=%d"
                     % (t["id"], t["status"], t["title"], t["role"],
                        req, len(t["owns"]), len(t["dod"])))
    print("\n".join(lines) if lines else "(账本为空)")


def main():
    ap = argparse.ArgumentParser(description="工单账本")
    ap.add_argument("--project", default=".", help="目标项目根目录")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add")
    p.add_argument("--id", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--inputs", nargs="*", default=[])
    p.add_argument("--owns", nargs="+", required=True)
    p.add_argument("--requires", nargs="*", default=[])
    p.add_argument("--dod", nargs="*", default=[])
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("claim")
    p.add_argument("--id", required=True)
    p.add_argument("--by", required=True, help="认领者署名（子 Agent 名）")
    p.set_defaults(fn=cmd_claim)

    p = sub.add_parser("done")
    p.add_argument("--id", required=True)
    p.set_defaults(fn=cmd_done)

    p = sub.add_parser("unclaim")
    p.add_argument("--id", required=True)
    p.set_defaults(fn=cmd_unclaim)

    for name in ("next", "status", "conflicts", "brief"):
        p = sub.add_parser(name)
        p.set_defaults(fn=globals()["cmd_" + name])

    args = ap.parse_args()
    args.project = os.path.abspath(args.project)
    args.fn(args)


if __name__ == "__main__":
    main()
