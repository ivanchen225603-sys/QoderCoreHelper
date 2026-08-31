#!/usr/bin/env python
# test_pipeline.py —— 这套脚本自己的回归测试。
#
# 为什么每个用例都注明"钉死的缺陷"：这是这套工具唯一能防止自己退化的
# 手段（不变量 16）。正常路径看不出这些缺陷——门禁静默不跑、白名单被
# 悄悄放宽、智能体自我放行——它们只在"看起来一切正常"中发生。
# 改任何脚本后先跑本文件。
#
# 运行: python test_pipeline.py -v
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import layout                     # noqa: E402
import pathnorm                   # noqa: E402
import fslock                     # noqa: E402
import gate                       # noqa: E402
import probes                     # noqa: E402


def run_cli(script_args, cwd=None):
    """跑流水线脚本，返回 (退出码, 合并输出)。"""
    r = subprocess.run([sys.executable] + script_args,
                       capture_output=True, text=True, cwd=cwd,
                       encoding="utf-8", errors="replace")
    return r.returncode, r.stdout + r.stderr


def last_json(text):
    """从输出里取最后一个顶层 JSON 对象（pio 输出顶层键都在行首）。"""
    idx = text.rfind("\n{")
    start = idx + 1 if idx >= 0 else (0 if text.startswith("{") else -1)
    if start < 0:
        return {}
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    return obj


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pipe-test-")
        self.S = os.path.join(self.tmp, "proj")
        os.makedirs(self.S)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def script(self, name):
        return os.path.join(HERE, name)

    def mk_project(self):
        """放一个 pyproject 让技术栈可探测，然后初始化。"""
        with open(os.path.join(self.S, "pyproject.toml"), "w") as f:
            f.write("[project]\nname='t'\n")
        rc, out = run_cli([self.script("init_pipeline.py"),
                           "--project", self.S])
        self.assertEqual(rc, 0, out)
        return out

    def cfg_path(self):
        return os.path.join(self.S, ".pipeline", "config.json")

    def load_cfg(self):
        with open(self.cfg_path(), encoding="utf-8") as f:
            return json.load(f)

    def save_cfg(self, cfg):
        with open(self.cfg_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)

    def make_s1_artifacts(self):
        arts = ["docs/requirements.md", "docs/acceptance.md",
                "docs/architecture.md", "docs/adr/ADR-001.md",
                "docs/api-contract.md", "prototype/index.html",
                "docs/open-items.md"]
        for a in arts:
            p = os.path.join(self.S, a)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write("# x\n")


class TestInit(Base):
    def test_empty_project_refused(self):
        """钉死缺陷：探测不到技术栈却瞎猜一个，门禁对错误工具链空转。"""
        rc, out = run_cli([self.script("init_pipeline.py"),
                           "--project", self.S])
        self.assertEqual(rc, layout.EXIT_CONFIG)
        self.assertIn("探测不到技术栈", out)

    def test_whitelist_widened_refused(self):
        """钉死缺陷：子 Agent 工具白名单被悄悄放宽，隔离失效不可见。"""
        self.mk_project()  # 正常安装一次
        import init_pipeline as ip
        fake_assets = os.path.join(self.tmp, "assets")
        shutil.copytree(os.path.join(os.path.dirname(HERE), "assets"),
                        fake_assets)
        bad = os.path.join(fake_assets, "agents", "reviewer.json")
        with open(bad, encoding="utf-8") as f:
            spec = json.load(f)
        spec["tools"].append("SearchReplace")  # 评审者获得编辑工具
        with open(bad, "w", encoding="utf-8") as f:
            json.dump(spec, f)
        old = ip.ASSETS
        ip.ASSETS = fake_assets
        try:
            pp = layout.pipeline_paths(self.S)
            with self.assertRaises(SystemExit) as ctx:
                ip.install_agent_defs(self.S, pp)
            self.assertEqual(ctx.exception.code, layout.EXIT_VIOLATION)
        finally:
            ip.ASSETS = old


class TestStateMachine(Base):
    def test_approve_without_artifacts_refused(self):
        """钉死缺陷：产出文件不存在，放行记录却写了进去。"""
        self.mk_project()
        run_cli([self.script("state.py"), "--project", self.S,
                 "start", "--stage", "S1"])
        rc, out = run_cli([self.script("state.py"), "--project", self.S,
                           "approve", "--gate", "G1", "--by", "张三"])
        self.assertEqual(rc, layout.EXIT_FAIL)
        self.assertIn("产出缺失", out)

    def test_approve_by_agent_refused(self):
        """钉死缺陷：智能体给自己盖章（最常见也最致命的失效）。"""
        self.mk_project()
        run_cli([self.script("state.py"), "--project", self.S,
                 "start", "--stage", "S1"])
        self.make_s1_artifacts()
        for name in ("Qoder-Agent", "assistant", "CI Bot", "agent-01"):
            rc, out = run_cli([self.script("state.py"), "--project", self.S,
                               "approve", "--gate", "G1", "--by", name])
            self.assertEqual(rc, layout.EXIT_VIOLATION, name + out)
            self.assertIn("拒绝", out)

    def test_next_stage_blocked_until_gate_approved(self):
        """钉死缺陷：关卡未放行，下一环节却被启动了。"""
        self.mk_project()
        rc, out = run_cli([self.script("state.py"), "--project", self.S,
                           "start", "--stage", "S3"])
        self.assertEqual(rc, layout.EXIT_VIOLATION)
        self.assertIn("未放行", out)
        self.assertIn("G2", out)

    def test_approve_without_start_refused(self):
        """钉死缺陷：环节没开始（没有产出过程）却被放行。"""
        self.mk_project()
        self.make_s1_artifacts()
        rc, out = run_cli([self.script("state.py"), "--project", self.S,
                           "approve", "--gate", "G1", "--by", "张三"])
        self.assertEqual(rc, layout.EXIT_VIOLATION)

    def test_next_action_single(self):
        """钉死缺陷：next-action 忘记 return，把七个环节的指引全部吐出来，
        主会话无从判断当前到底在哪一步。"""
        self.mk_project()
        rc, out = run_cli([self.script("state.py"), "--project", self.S,
                           "next-action"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.count('"action"'), 1)
        j = last_json(out)
        self.assertEqual(j["stage"], "S1")
        self.assertEqual(j["action"], "start_stage")

    def test_self_heal_stops_at_limit(self):
        """钉死缺陷：自愈无限循环烧钱，用户看不见。"""
        self.mk_project()
        for i in (1,):
            rc, out = run_cli([self.script("state.py"), "--project", self.S,
                               "heal-round", "--gate", "G2"])
            self.assertEqual(rc, 0)
        rc, out = run_cli([self.script("state.py"), "--project", self.S,
                           "heal-round", "--gate", "G2"])
        self.assertEqual(rc, layout.EXIT_FAIL)
        j = last_json(out)
        self.assertTrue(j.get("stop"))
        # 轮次落盘：新进程读到的仍是 2，不会重置
        st = json.load(open(os.path.join(self.S, ".pipeline", "state.json"),
                            encoding="utf-8"))
        self.assertEqual(st["self_heal"]["G2"], 2)


class TestTickets(Base):
    def add(self, tid, owns, requires=None, dod=None):
        args = [self.script("tickets.py"), "--project", self.S, "add",
                "--id", tid, "--title", tid, "--role", "implementer",
                "--owns"] + owns
        for r in (requires or []):
            args += ["--requires", r]
        for d in (dod or []):
            args += ["--dod", d]
        return run_cli(args)

    def test_claim_conflict_refused_with_holder(self):
        """钉死缺陷：两个 Agent 同时改一个文件，后写的悄悄覆盖先写的。"""
        self.mk_project()
        rc, _ = self.add("T1", ["src/app.py"], dod=["src/app.py"])
        self.assertEqual(rc, 0)
        rc, out = self.add("T2", ["src/app.py"])
        self.assertEqual(rc, layout.EXIT_FAIL)
        self.assertIn("T1", out)

    def test_path_notation_equivalence(self):
        """钉死缺陷：同一文件不同写法（分隔符/./）骗过独占检查。"""
        self.mk_project()
        rc, _ = self.add("T1", ["src/a.py"])
        self.assertEqual(rc, 0)
        rc, out = self.add("T2", [r".\src\a.py"])
        self.assertEqual(rc, layout.EXIT_FAIL, out)
        # 目录与文件互为包含也算冲突
        rc, _ = self.add("T3", ["src"])
        self.assertEqual(rc, layout.EXIT_FAIL)

    def test_claim_requires_done(self):
        """钉死缺陷：拿到半成品输入，在很远的地方才失败。"""
        self.mk_project()
        self.add("T1", ["src/a.py"], dod=["src/a.py"])
        self.add("T2", ["src/b.py"], requires=["T1"], dod=["src/b.py"])
        rc, out = run_cli([self.script("tickets.py"), "--project", self.S,
                           "claim", "--id", "T2", "--by", "w1"])
        self.assertEqual(rc, layout.EXIT_FAIL)
        self.assertIn("前置", out)

    def test_done_requires_dod_files(self):
        """钉死缺陷：'做完了'只是自述，dod 文件根本不存在。"""
        self.mk_project()
        self.add("T1", ["src/a.py"], dod=["src/a.py"])
        run_cli([self.script("tickets.py"), "--project", self.S,
                 "claim", "--id", "T1", "--by", "w1"])
        rc, out = run_cli([self.script("tickets.py"), "--project", self.S,
                           "done", "--id", "T1"])
        self.assertEqual(rc, layout.EXIT_FAIL)
        self.assertIn("dod", out)
        os.makedirs(os.path.join(self.S, "src"))
        open(os.path.join(self.S, "src", "a.py"), "w").write("x=1\n")
        rc, _ = run_cli([self.script("tickets.py"), "--project", self.S,
                         "done", "--id", "T1"])
        self.assertEqual(rc, 0)

    def test_unclaim_recovery(self):
        """钉死缺陷：认领后子 Agent 崩溃，工单永久卡在 claimed。"""
        self.mk_project()
        self.add("T1", ["src/a.py"])
        run_cli([self.script("tickets.py"), "--project", self.S,
                 "claim", "--id", "T1", "--by", "w1"])
        rc, out = run_cli([self.script("tickets.py"), "--project", self.S,
                           "unclaim", "--id", "T1"])
        self.assertEqual(rc, 0, out)
        rc, _ = run_cli([self.script("tickets.py"), "--project", self.S,
                         "claim", "--id", "T1", "--by", "w2"])
        self.assertEqual(rc, 0)

    def test_concurrent_adds_no_lost_write(self):
        """钉死缺陷：两个进程同时写账本，后写覆盖先写，工单记录丢失。"""
        import threading
        self.mk_project()
        results = []

        def worker(i):
            rc, _ = run_cli([self.script("tickets.py"), "--project", self.S,
                             "add", "--id", "C%d" % i, "--title", "c",
                             "--role", "implementer",
                             "--owns", "src/c%d.py" % i])
            results.append(rc)

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results, [0] * 6)
        rc, out = run_cli([self.script("tickets.py"), "--project", self.S,
                           "status"])
        j = last_json(out)
        self.assertEqual(j["counts"]["open"], 6)


class TestGate(Base):
    def test_unknown_env_refused_not_silent_pass(self):
        """钉死缺陷：检查声明在不存在的环境里执行 → 根本不跑而门禁显示通过。"""
        self.mk_project()
        cfg = self.load_cfg()
        cfg["gates"]["checks"].append({
            "id": "ghost", "adapter": "python-builtin",
            "run": [["python", "-c", ""]], "env": ["dev", "qa"],
            "gates": ["G2"], "on_fail": "self_heal"})
        self.save_cfg(cfg)
        rc, out = run_cli([self.script("gate.py"), "--project", self.S,
                           "run", "--gate", "G2"])
        self.assertEqual(rc, layout.EXIT_CONFIG, out)
        self.assertIn("qa", out)

    def test_threshold_must_increase_with_env(self):
        """钉死缺陷：更严格的环境拿更宽松的标准（阈值递减）。"""
        self.mk_project()
        cfg = self.load_cfg()
        for c in cfg["gates"]["checks"]:
            if c["id"] == "unit_tests":
                c["thresholds"] = {"dev": 80, "staging": 60, "prod": 90}
        self.save_cfg(cfg)
        rc, out = run_cli([self.script("gate.py"), "--project", self.S,
                           "verify-config"])
        self.assertEqual(rc, layout.EXIT_CONFIG)
        self.assertIn("递减", out)

    def test_secret_zero_exemption(self):
        """钉死缺陷：密钥扫描被豁免放行（'这只是开发环境'）。"""
        self.mk_project()
        rc, out = run_cli([self.script("gate.py"), "--project", self.S,
                           "exempt", "--check", "secrets", "--env", "dev",
                           "--by", "张三", "--until", "2099-01-01",
                           "--reason", "开发环境无所谓"])
        self.assertEqual(rc, layout.EXIT_VIOLATION)
        self.assertIn("零豁免", out)

    def test_expired_exemption_reblocks(self):
        """钉死缺陷：豁免到期后仍静默放行（没有重新阻断）。"""
        self.mk_project()
        cfg = self.load_cfg()
        cfg["gates"]["checks"] = [{
            "id": "always_fail", "adapter": "python-builtin",
            "run": [["python", "-c", "import sys; sys.exit(1)"]],
            "env": ["dev", "staging", "prod"], "gates": ["G2"],
            "on_fail": "needs_decision"}]
        self.save_cfg(cfg)
        rc, out = run_cli([self.script("gate.py"), "--project", self.S,
                           "exempt", "--check", "always_fail", "--env", "dev",
                           "--by", "李四", "--until", "2099-01-01",
                           "--reason", "临时绕过等待修复计划"])
        self.assertEqual(rc, 0, out)
        rc, out = run_cli([self.script("gate.py"), "--project", self.S,
                           "run", "--gate", "G2"])
        self.assertEqual(rc, 0)  # 已豁免 → 不算未通过，但状态是 exempted
        rep = last_json(out)
        self.assertEqual(rep["checks"][0]["status"], "exempted")
        # 到期日改成过去 → 重新阻断
        cfg = self.load_cfg()
        cfg["exemptions"][0]["until"] = "2020-01-01"
        self.save_cfg(cfg)
        rc, out = run_cli([self.script("gate.py"), "--project", self.S,
                           "run", "--gate", "G2"])
        self.assertEqual(rc, layout.EXIT_FAIL)
        rep = last_json(out)
        self.assertEqual(rep["checks"][0]["status"], "fail")

    def test_degraded_not_silent_pass(self):
        """钉死缺陷：外部工具不可用 → 检查被静默跳过、门禁显示通过。"""
        self.mk_project()
        cfg = self.load_cfg()
        cfg["gates"]["checks"] = [{
            "id": "needs_tool", "adapter": "missingtool",
            "run": [["missingtool", "--x"]],
            "env": ["dev", "staging", "prod"], "gates": ["G2"],
            "on_fail": "needs_decision"}]
        self.save_cfg(cfg)
        ad = {"name": "missingtool", "kind": "test",
              "detect": {"type": "binary", "value": "missingtool_xyz_zzz"},
              "degrade": {"mode": "mark_degraded", "notice": "工具缺失"}}
        with open(os.path.join(self.S, ".pipeline", "adapters",
                               "missingtool.json"), "w") as f:
            json.dump(ad, f)
        rc, out = run_cli([self.script("gate.py"), "--project", self.S,
                           "run", "--gate", "G2"])
        self.assertEqual(rc, layout.EXIT_FAIL, out)
        rep = last_json(out)
        self.assertEqual(rep["verdict"], "fail")
        self.assertEqual(rep["checks"][0]["status"], "degraded")
        self.assertTrue(rep["degraded"])

    def test_heal_exhausted_forces_needs_decision(self):
        """钉死缺陷：自愈第三轮自动发生（轮次超限仍继续修）。"""
        self.mk_project()
        cfg = self.load_cfg()
        cfg["gates"]["checks"] = [{
            "id": "always_fail", "adapter": "python-builtin",
            "run": [["python", "-c", "import sys; sys.exit(1)"]],
            "env": ["dev", "staging", "prod"], "gates": ["G2"],
            "on_fail": "self_heal"}]
        self.save_cfg(cfg)
        for _ in range(2):
            run_cli([self.script("state.py"), "--project", self.S,
                     "heal-round", "--gate", "G2"])
        rc, out = run_cli([self.script("gate.py"), "--project", self.S,
                           "run", "--gate", "G2"])
        self.assertEqual(rc, layout.EXIT_FAIL)
        rep = last_json(out)
        self.assertTrue(rep["heal_exhausted"])
        self.assertEqual(rep["checks"][0]["classification"], "needs_decision")


    def test_fail_streak_auto_upgrade(self):
        """钉死缺陷：没人记录自愈轮次时无上限——同一检查连败三次仍在自愈。
        门禁自动计数：连败 3 次的自愈项自动升级为需决策。"""
        self.mk_project()
        cfg = self.load_cfg()
        cfg["gates"]["checks"] = [{
            "id": "always_fail", "adapter": "python-builtin",
            "run": [["python", "-c", "import sys; sys.exit(1)"]],
            "env": ["dev", "staging", "prod"], "gates": ["G2"],
            "on_fail": "self_heal"}]
        self.save_cfg(cfg)
        for i in (1, 2):
            rc, out = run_cli([self.script("gate.py"), "--project", self.S,
                               "run", "--gate", "G2"])
            self.assertEqual(rc, layout.EXIT_FAIL)
            rep = last_json(out)
            self.assertEqual(rep["checks"][0]["classification"], "self_heal",
                             "第%d次连败仍应自愈" % i)
        rc, out = run_cli([self.script("gate.py"), "--project", self.S,
                           "run", "--gate", "G2"])
        rep = last_json(out)
        self.assertEqual(rep["checks"][0]["classification"],
                         "needs_decision", "连败 3 次必须自动升级为需决策")
        # 修复后通过 → 连败计数清零（下一次失败仍可自愈）
        cfg = self.load_cfg()
        cfg["gates"]["checks"][0]["run"] = [["python", "-c", ""]]
        self.save_cfg(cfg)
        rc, out = run_cli([self.script("gate.py"), "--project", self.S,
                           "run", "--gate", "G2"])
        self.assertEqual(rc, 0)
        st = json.load(open(os.path.join(self.S, ".pipeline", "state.json"),
                            encoding="utf-8"))
        self.assertEqual(st["fail_streak"]["G2"]["always_fail"], 0)

    def test_archive_rotation(self):
        """钉死缺陷：报告归档无限膨胀，磁盘被自己的历史吃掉。"""
        import gate as gate_mod
        rd = os.path.join(self.tmp, "reports")
        os.makedirs(rd)
        for i in range(25):
            open(os.path.join(rd, "gate-G2-2026%04d.json" % i),
                 "w").write("{}")
        removed = gate_mod.rotate_archives(rd, keep=20)
        self.assertEqual(len(removed), 5)
        self.assertEqual(len(os.listdir(rd)), 20)


class TestProbes(Base):
    def test_coverage_formats_normalize_equal(self):
        """钉死缺陷：格式差异污染门禁逻辑，同一覆盖率被判成不同结果。"""
        samples = {
            "cov.json": '{"totals": {"percent_covered": 80.0}}',
            "info.lcov": "SF:a.py\nLF:100\nLH:80\nend_of_record\n",
            "cobertura.xml": '<?xml version="1.0"?>'
                             '<coverage line-rate="0.8" version="1"/>',
            "istanbul.json": '{"total": {"lines": {"pct": 80}}}',
            "plain.txt": "80",
        }
        for fn, content in samples.items():
            p = os.path.join(self.tmp, fn)
            with open(p, "w") as f:
                f.write(content)
            rc, out = run_cli([self.script("probes.py"), "coverage",
                               "--file", p])
            self.assertEqual(rc, 0, fn + out)
            self.assertAlmostEqual(json.loads(out)["value"], 80.0,
                                   msg=fn)

    def test_multistage_user_spoof(self):
        """钉死缺陷：多阶段构建用中间阶段的 USER 骗过权限检查。"""
        d1 = os.path.join(self.tmp, "Dockerfile.good")
        with open(d1, "w") as f:
            f.write("FROM base AS build\nUSER root\nFROM base\nUSER appuser\n")
        rc, out = run_cli([self.script("probes.py"), "image-user",
                           "--file", d1])
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out)["value"])
        d2 = os.path.join(self.tmp, "Dockerfile.bad")
        with open(d2, "w") as f:
            f.write("FROM base AS build\nUSER appuser\nFROM base\n")
        rc, out = run_cli([self.script("probes.py"), "image-user",
                           "--file", d2])
        self.assertEqual(rc, 0)
        self.assertFalse(json.loads(out)["value"])

    def test_migration_rollback(self):
        """钉死缺陷：不可回滚迁移混过发布准备（drop 列没有 down 段）。"""
        m1 = os.path.join(self.tmp, "m1.py")
        with open(m1, "w") as f:
            f.write("def up(): ...\ndef down(): ...\n")
        m2 = os.path.join(self.tmp, "m2.sql")
        with open(m2, "w") as f:
            f.write("ALTER TABLE t DROP COLUMN c;\n")
        _, out1 = run_cli([self.script("probes.py"), "migration-rollback",
                           "--file", m1])
        _, out2 = run_cli([self.script("probes.py"), "migration-rollback",
                           "--file", m2])
        self.assertTrue(json.loads(out1)["value"])
        self.assertFalse(json.loads(out2)["value"])


class TestFsLock(Base):
    def test_second_process_cannot_sneak_lock(self):
        """钉死缺陷：第二个句柄/进程绕过独占检查直接拿锁。"""
        lp = os.path.join(self.tmp, "x.lock")
        lk1 = fslock.FileLock(lp)
        lk1.acquire()
        try:
            lk2 = fslock.FileLock(lp)
            with self.assertRaises(fslock.LockError):
                lk2.acquire()
        finally:
            lk1.release()
        # 释放后可以再拿（锁没有泄漏）
        with fslock.FileLock(lp):
            pass

    def test_lock_path_normalized(self):
        """钉死缺陷：不同路径写法落进不同锁文件，独占被绕过。"""
        self.mk_project()
        pp = layout.pipeline_paths(self.S)
        a = fslock.lock_file_path(pp["root"], pathnorm.norm("src/a.py", self.S))
        b = fslock.lock_file_path(pp["root"],
                                  pathnorm.norm(r".\src\a.py", self.S))
        self.assertEqual(a, b)


class TestRender(Base):
    def test_diff_surface_enforced_for_review(self):
        """钉死缺陷：主会话忘了渲染评审面，评审者拿到全库访问——
        '看了哪些行'不可审计。S4 放行必须强制检查 changes.diff 存在。"""
        import state as state_mod
        self.mk_project()
        os.makedirs(os.path.join(self.S, "src"))
        open(os.path.join(self.S, "src", "z.py"), "w").write("z = 42\n")
        run_cli([self.script("tickets.py"), "--project", self.S, "add",
                 "--id", "I1", "--title", "impl", "--role", "implementer",
                 "--owns", "src/z.py", "--dod", "src/z.py"])
        run_cli([self.script("tickets.py"), "--project", self.S,
                 "claim", "--id", "I1", "--by", "w1"])
        run_cli([self.script("tickets.py"), "--project", self.S,
                 "done", "--id", "I1"])
        # 未渲染 → S4 产出校验报缺失（放行过不去）
        verified, missing = state_mod._verify_artifacts(self.S, "S4")
        self.assertIn("reports/changes.diff", missing)
        # 工具渲染 → 内容含实现文件与行号，且校验通过该项
        rc, out = run_cli([self.script("render.py"), "--project", self.S,
                           "diff"])
        self.assertEqual(rc, 0, out)
        text = open(os.path.join(self.S, "reports", "changes.diff"),
                    encoding="utf-8").read()
        self.assertIn("src/z.py", text)
        self.assertIn("z = 42", text)
        verified, missing = state_mod._verify_artifacts(self.S, "S4")
        self.assertNotIn("reports/changes.diff", missing)

    def test_pack_contains_forbidden_zone(self):
        """钉死缺陷：手工拼派发包忘了写禁区，Agent 改了门禁阈值。"""
        self.mk_project()
        run_cli([self.script("tickets.py"), "--project", self.S, "add",
                 "--id", "T1", "--title", "a", "--role", "implementer",
                 "--owns", "src/a.py"])
        run_cli([self.script("tickets.py"), "--project", self.S, "add",
                 "--id", "T2", "--title", "b", "--role", "implementer",
                 "--owns", "src/b.py"])
        rc, out = run_cli([self.script("render.py"), "--project", self.S,
                           "pack", "--ticket", "T1"])
        self.assertEqual(rc, 0, out)
        pack = os.path.join(self.S, ".pipeline", "packs", "T1-pack.md")
        text = open(pack, encoding="utf-8").read()
        self.assertIn("src/b.py", text)          # 对方工单的 owns 进禁区
        self.assertIn(".pipeline/**", text)      # 全局禁区在
        self.assertIn("隔离强度声明", text)       # 白名单不可用时如实声明


if __name__ == "__main__":
    unittest.main(verbosity=2)
