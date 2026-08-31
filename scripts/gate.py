#!/usr/bin/env python
# gate.py —— 环境门禁：跑检查、比阈值、应用豁免、出 JSON 报告。
#
# 关键机制：
# - 退出码区分"未通过"（2）和"配置错"（3）：配置错的检查根本没跑，
#   绝不能显示成通过（不变量 16 钉死的缺陷类）。
# - 阈值按环境从 config.environments 的顺序取，且必须非递减（不变量 13）。
# - 豁免三要件：人名 + 到期日 + 理由；密钥检查 no_exempt，豁免通道对其关闭。
# - 豁免项显示为 "exempted"，不是 "pass"：用户要一眼看出绿色里有多少水分。
# - 外部工具不可用 → 按适配器声明降级；密钥扫描降级为内置扫描器（不许不扫）。
#
# 子命令：run / exempt / verify-config
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import layout
import adapter
import fslock
import pio
import state as state_mod

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_config(project):
    return pio.load_json(layout.pipeline_paths(project)["config"])


def _save_config(project, cfg):
    pio.save_json(layout.pipeline_paths(project)["config"], cfg)


def gates_hash(gates_section):
    canon = json.dumps(gates_section, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


# ── 配置校验 ─────────────────────────────────────────────────────────
def validate_config(cfg):
    """返回错误列表。空列表 = 配置合法。

    为什么严格到偏执：一条"引用了不存在的环境"的检查，效果是这个检查
    在任何环境都不执行、而门禁照样显示通过——这是"失效不可见"的教科书案例。
    """
    errors = []
    envs = cfg.get("environments", [])
    if not envs:
        errors.append("config.environments 为空")
    checks = cfg.get("gates", {}).get("checks", [])
    if not checks:
        errors.append("config.gates.checks 为空：没有任何检查的门禁等于没有门禁")
    ids = set()
    for c in checks:
        cid = c.get("id", "<缺 id>")
        if cid in ids:
            errors.append("检查 id 重复: %s" % cid)
        ids.add(cid)
        for e in c.get("env", []):
            if e not in envs:
                errors.append(
                    "检查 %s 声明在环境 %s 执行，但 environments 里没有该环境。"
                    "这条检查根本不会跑而门禁会显示通过——拒绝装载。"
                    % (cid, e))
        for g in c.get("gates", []):
            if g not in layout.GATES:
                errors.append("检查 %s 引用了不存在的关卡 %s" % (cid, g))
        th = c.get("thresholds")
        if th:
            prev = None
            for e in envs:  # 按声明顺序，阈值必须非递减（不变量 13）
                if e not in th:
                    if e in c.get("env", []):
                        errors.append("检查 %s 在环境 %s 有执行声明却没有阈值"
                                      % (cid, e))
                    continue
                if prev is not None and th[e] < prev:
                    errors.append(
                        "检查 %s 的阈值随环境递减（%s）：阈值必须随环境递增，"
                        "更严格的环境不允许更宽松的标准。" % (cid, th))
                prev = th[e]
        if c.get("no_exempt") and c.get("allow_exempt"):
            errors.append("检查 %s 同时声明 no_exempt 与 allow_exempt" % cid)
    return errors


def config_drift(cfg):
    """当前 gates 配置相对安装模板的漂移（不变量 14 的机制部分）。

    阈值被调低、检查被删掉，都会在关卡卡片上作为待批项显式列出——
    用户改阈值是把关，智能体改阈值是绕开把关，漂移必须可见。
    """
    expected = cfg.get("gates_template_hash")
    if not expected:
        return False, "无模板哈希（旧配置），跳过漂移检查"
    actual = gates_hash(cfg.get("gates", {}))
    if actual == expected:
        return False, ""
    return True, ("门禁配置与安装模板不一致：任何门禁配置改动都属于"
                  "生成待批项，必须在关卡上单独列出并说明理由。")


# ── 豁免 ─────────────────────────────────────────────────────────────
def find_exemption(cfg, check_id, env, today):
    for ex in cfg.get("exemptions", []):
        if ex["check"] == check_id and ex["env"] == env \
                and ex["until"] >= today:
            return ex
    return None  # 过期豁免自动失效 → 检查重新阻断（不变量 15）


def cmd_exempt(args):
    cfg = _load_config(args.project)
    check = None
    for c in cfg.get("gates", {}).get("checks", []):
        if c["id"] == args.check:
            check = c
            break
    if not check:
        pio.die(layout.EXIT_CONFIG, "检查 %s 不存在于门禁配置" % args.check)
    if check.get("no_exempt"):
        pio.die(layout.EXIT_VIOLATION,
                "检查 %s 标记为 no_exempt：密钥扫描在所有环境零豁免。"
                "凭据一旦进版本库历史就等于已经公开，'这只是开发环境'"
                "这个判断在提交那一刻就失效了。唯一的出路是清除凭据并轮换。"
                % args.check)
    env = args.env or layout.GATE_ENV.get(args.gate)
    if env not in cfg.get("environments", []):
        pio.die(layout.EXIT_CONFIG, "环境 %s 未声明" % env)
    if env not in check.get("env", []):
        pio.die(layout.EXIT_CONFIG,
                "检查 %s 不在环境 %s 执行，无从豁免" % (args.check, env))
    state_mod._check_human_name(args.by)  # 只有人能批准豁免
    try:
        time.strptime(args.until, "%Y-%m-%d")
    except ValueError:
        pio.die(layout.EXIT_CONFIG, "到期日格式应为 YYYY-MM-DD")
    if args.until <= time.strftime("%Y-%m-%d"):
        pio.die(layout.EXIT_CONFIG, "到期日必须是未来的日期")
    if len(args.reason.strip()) < 8:
        pio.die(layout.EXIT_CONFIG,
                "豁免理由过短：理由要能让到期审查者判断是否续期")
    cfg.setdefault("exemptions", []).append({
        "check": args.check, "env": env, "by": args.by.strip(),
        "until": args.until, "reason": args.reason.strip(),
        "granted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    _save_config(args.project, cfg)
    pio.ok("豁免已登记：%s @ %s，批准人 %s，到期 %s（状态将显示为'已豁免'而非'通过'）"
           % (args.check, env, args.by, args.until))


# ── 执行 ─────────────────────────────────────────────────────────────
def _run_cmd(cmds, project, evidence_path):
    """按序执行命令序列（run 字段是命令列表），任一失败即停。"""
    os.makedirs(os.path.dirname(evidence_path), exist_ok=True)
    log_parts, rc_final, out_final, err_final = [], 0, "", ""
    for cmd in cmds:
        try:
            r = subprocess.run(cmd, cwd=project, capture_output=True,
                               text=True, timeout=900,
                               encoding="utf-8", errors="replace")
        except FileNotFoundError as e:
            rc_final, out_final, err_final = 127, "", str(e)
            log_parts.append("$ %s\n命令无法启动: %s" % (" ".join(cmd), e))
            break
        except subprocess.TimeoutExpired:
            rc_final, out_final, err_final = 124, "", "timeout"
            log_parts.append("$ %s\n命令超时(900s)" % " ".join(cmd))
            break
        log_parts.append("$ %s\n-- stdout --\n%s\n-- stderr --\n%s"
                         % (" ".join(cmd), r.stdout, r.stderr))
        rc_final, out_final, err_final = r.returncode, r.stdout, r.stderr
        if r.returncode != 0:
            break
    with open(evidence_path, "w", encoding="utf-8") as f:
        f.write("\n".join(log_parts) + "\n")
    return rc_final, out_final, err_final


def _probe_value(project, probe_spec):
    """通过探针取值（格式归一在探针层，门禁只认数字）。"""
    kind = probe_spec["type"]
    cmd = [sys.executable, os.path.join(SCRIPTS_DIR, "probes.py"), kind,
           "--file", os.path.join(project, probe_spec["file"])]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return None, "探针失败(退出码 %d): %s" % (
            r.returncode, (r.stdout + r.stderr).strip()[-300:])
    try:
        return json.loads(r.stdout)["value"], ""
    except (json.JSONDecodeError, KeyError) as e:
        return None, "探针输出无法解析: %s" % e


def rotate_archives(reports_dir, keep=20):
    """归档轮转：只保留最近 keep 份门禁报告，防止长期运行无限膨胀。
    latest-*.json 与 evidence/ 不在轮转范围（前者每次覆盖，后者按检查覆盖）。"""
    import glob as _glob
    arcs = sorted(_glob.glob(os.path.join(reports_dir, "gate-*.json")))
    removed = []
    for old in arcs[:-keep] if len(arcs) > keep else []:
        try:
            os.remove(old)
            removed.append(old)
        except OSError:
            pass
    return removed


def _update_fail_streak(project, gate, results):
    """同一检查连续失败自动计数（自愈上限的机制化）。

    为什么：heal-round 靠主会话自觉记录，不记录就没有上限；门禁自己
    数连续失败，第 3 次连败的 self_heal 项自动升级为需决策——
    "第 3 轮不许发生"从约定变成机制。通过/豁免即清零。
    """
    pp = layout.pipeline_paths(project)
    with fslock.ledger_lock(pp["root"]):
        st = pio.load_json(pp["state"])
        streak = st.setdefault("fail_streak", {}).setdefault(gate, {})
        changed = False
        for r in results:
            if r["status"] == "fail":
                streak[r["id"]] = streak.get(r["id"], 0) + 1
                changed = True
                if streak[r["id"]] >= 3 and \
                        r.get("classification") == "self_heal":
                    r["classification"] = "needs_decision"
                    r["detail"] += ("（同一检查已连续失败 %d 次，"
                                     "自愈自动停用，需用户决策）"
                                     % streak[r["id"]])
            elif r["status"] in ("pass", "exempted") and streak.get(r["id"]):
                streak[r["id"]] = 0
                changed = True
        if changed:
            pio.save_json(pp["state"], st)
        return dict(streak)


def cmd_run(args):
    project = args.project
    cfg = _load_config(project)
    pp = layout.pipeline_paths(project)

    errors = validate_config(cfg)
    if errors:
        pio.die(layout.EXIT_CONFIG,
                "门禁配置校验失败（检查根本没跑，不算未通过，算配置错）",
                errors=errors)

    gate = args.gate
    if gate not in layout.GATE_KIND or layout.GATE_KIND[gate] != "env":
        pio.die(layout.EXIT_CONFIG,
                "%s 不是环境门禁（类型: %s），没有可执行的检查。环节关卡由人判断。"
                % (gate, layout.GATE_KIND.get(gate, "未知")))
    env = layout.GATE_ENV[gate]
    checks = [c for c in cfg["gates"]["checks"] if gate in c.get("gates", [])]
    if not checks:
        pio.die(layout.EXIT_CONFIG, "关卡 %s 没有配置任何检查项" % gate)

    adapters = adapter.load_adapters(project)
    undeclared = sorted({c.get("adapter") for c in checks}
                        - set(adapters) - {None})
    if undeclared:
        pio.die(layout.EXIT_CONFIG,
                "检查引用了未声明的适配器: %s（适配器声明在 .pipeline/adapters/）"
                % ", ".join(undeclared))
    today = time.strftime("%Y-%m-%d")
    started = time.strftime("%Y-%m-%dT%H:%M:%S")

    # 自愈轮次：落盘、跨会话不重置；超限后失败项一律升级为需决策
    st = pio.load_json(pp["state"])
    heal_rounds = st.get("self_heal", {}).get(gate, 0)
    heal_exhausted = heal_rounds >= layout.SELF_HEAL_MAX_ROUNDS

    results, degraded_notices = [], []
    for c in checks:
        cid = c["id"]
        ev_path = os.path.join(pp["reports"], "evidence", gate,
                               cid + ".log")
        entry = {"id": cid, "adapter": c.get("adapter"),
                 "status": "pending", "classification": None,
                 "evidence": os.path.relpath(ev_path, project).replace("\\", "/"),
                 "exemption": None, "detail": ""}
        spec = adapters.get(c.get("adapter", ""), {})
        avail, why = adapter.detect(project, spec.get("detect", {"type": "always"}))

        run_cmds = c.get("run")
        if not avail:
            mode = spec.get("degrade", {}).get("mode", "mark_degraded")
            if mode == "fallback_builtin" and c.get("builtin"):
                run_cmds = c["builtin"]
                entry["detail"] = "外部工具 %s 不可用（%s），已切换内置兜底实现。" % (c.get("adapter"), why)
                degraded_notices.append(
                    "%s: 使用内置兜底（精度低于专业工具），%s" % (cid, why))
                avail = True
            else:
                entry["status"] = "degraded"
                entry["classification"] = "needs_decision"
                notice = spec.get("degrade", {}).get(
                    "notice", "工具不可用且无兜底")
                entry["detail"] = "%s（%s）" % (notice, why)
                degraded_notices.append("%s: %s" % (cid, notice))
                results.append(entry)
                continue

        rc, out, err = _run_cmd(run_cmds, project, ev_path)
        entry["returncode"] = rc
        ok = rc == 0

        # 探针阈值（如覆盖率）：命令过 ≠ 达标，还要比值
        if ok and c.get("thresholds"):
            value, perr = _probe_value(project, c.get("probe", {})) \
                if c.get("probe") else (None, "缺少 probe 声明")
            if value is None:
                ok = False
                entry["detail"] = perr
                entry["classification"] = "needs_decision"
            else:
                need = c["thresholds"].get(env)
                entry["value"], entry["threshold"] = value, need
                if value < need:
                    ok = False
                    entry["detail"] = (
                        "取值 %.1f 低于环境 %s 阈值 %.1f" % (value, env, need))
        if ok:
            entry["status"] = "pass"
        else:
            if not entry["detail"]:
                entry["detail"] = "命令退出码 %d（证据见 evidence）" % rc
            entry["status"] = "fail"
            entry["classification"] = c.get("on_fail", "self_heal")
            if heal_exhausted and entry["classification"] == "self_heal":
                entry["classification"] = "needs_decision"
                entry["detail"] += "（自愈轮次已达上限，升级为需决策）"
            # 豁免只在失败时查（不变量 15：三要件 + 单独显示）
            ex = find_exemption(cfg, cid, env, today)
            if ex:
                entry["status"] = "exempted"
                entry["classification"] = None
                entry["exemption"] = {"by": ex["by"], "until": ex["until"],
                                      "reason": ex["reason"]}
        results.append(entry)

    streak_snapshot = _update_fail_streak(project, gate, results)
    drift, drift_msg = config_drift(cfg)
    statuses = [r["status"] for r in results]
    if all(s in ("pass", "exempted") for s in statuses):
        verdict = "exempted" if all(s == "exempted" for s in statuses) \
            and statuses else "pass"
    else:
        verdict = "fail"
    report = {
        "gate": gate, "env": env, "verdict": verdict,
        "started_at": started,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "checks": results,
        "counts": {s: statuses.count(s) for s in
                   ("pass", "fail", "exempted", "degraded")},
        "degraded": degraded_notices,
        "config_drift": {"present": drift, "notice": drift_msg},
        "self_heal_rounds": heal_rounds,
        "heal_exhausted": heal_exhausted,
        "fail_streak": streak_snapshot,
    }
    stamp = time.strftime("%Y%m%d-%H%M%S")
    arc = os.path.join(pp["reports"], "gate-%s-%s.json" % (gate, stamp))
    latest = os.path.join(pp["reports"], "latest-%s.json" % env)
    pio.save_json(arc, report)
    pio.save_json(latest, report)
    report["rotated"] = rotate_archives(pp["reports"])
    report["report_path"] = os.path.relpath(arc, project).replace("\\", "/")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(layout.EXIT_OK if verdict in ("pass", "exempted")
             else layout.EXIT_FAIL)


def cmd_verify_config(args):
    cfg = _load_config(args.project)
    errors = validate_config(cfg)
    drift, drift_msg = config_drift(cfg)
    if errors:
        pio.die(layout.EXIT_CONFIG, "门禁配置非法，拒绝执行任何门禁",
                errors=errors, drift=drift, drift_notice=drift_msg)
    pio.ok("门禁配置合法", drift=drift, drift_notice=drift_msg,
           checks=[c["id"] for c in cfg["gates"]["checks"]],
           environments=cfg.get("environments"))


def main():
    ap = argparse.ArgumentParser(description="环境门禁")
    ap.add_argument("--project", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run")
    p.add_argument("--gate", required=True)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("exempt")
    p.add_argument("--check", required=True)
    p.add_argument("--env")
    p.add_argument("--gate")
    p.add_argument("--by", required=True)
    p.add_argument("--until", required=True)
    p.add_argument("--reason", required=True)
    p.set_defaults(fn=cmd_exempt)

    p = sub.add_parser("verify-config")
    p.set_defaults(fn=cmd_verify_config)

    args = ap.parse_args()
    args.project = os.path.abspath(args.project)
    args.fn(args)


if __name__ == "__main__":
    main()
