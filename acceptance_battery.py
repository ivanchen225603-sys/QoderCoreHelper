"""验收违例串联：依次触发每个"应被拒绝"的动作，核对退出码。

用法:
  python acceptance_battery.py [--scripts <脚本目录>] [--lab <实验项目目录>]
  --scripts 默认: 本脚本同级的 scripts/ 目录
  --lab     默认: 系统临时目录下的 pipe-battery-lab（每次运行先清空重建）

退出码: 全部符合预期 = 0；任一项不符 = 1。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

# 与流水线脚本同款的控制台修复：GBK 终端上打印含 ⟷/✅ 的错误摘要会崩。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ap = argparse.ArgumentParser()
ap.add_argument("--scripts",
                default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "scripts"))
ap.add_argument("--lab",
                default=os.path.join(tempfile.gettempdir(), "pipe-battery-lab"))
args = ap.parse_args()

LAB = os.path.abspath(args.lab)
SCRIPTS = os.path.abspath(args.scripts)
PY = sys.executable
FAILED = []


def run(label, script_args, expect):
    r = subprocess.run([PY] + script_args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=LAB)
    mark = "OK " if r.returncode == expect else "!! "
    if r.returncode != expect:
        FAILED.append(label)
    out = (r.stdout + r.stderr).strip().splitlines()
    key = next((l for l in out if '"error"' in l), (out[-1] if out else ""))
    print(f"{mark}[{label}] 期望退出码 {expect}，实际 {r.returncode} | {key.strip()[:90]}")
    return r


def s(name):
    return os.path.join(SCRIPTS, name)


shutil.rmtree(LAB, ignore_errors=True)
os.makedirs(os.path.join(LAB, "src"))
open(os.path.join(LAB, "pyproject.toml"), "w").write("[project]\nname='lab'\n")
open(os.path.join(LAB, "src", "app.py"), "w").write("def f() -> int:\n    return 1\n")

run("init", [s("init_pipeline.py"), "--project", LAB], 0)

# 验收2：G1 未放行就启动 S2
run("关卡未放行启动下一环节", [s("state.py"), "--project", LAB,
    "start", "--stage", "S2"], 4)

# 验收3a：产出缺失时放行
run("启动S1", [s("state.py"), "--project", LAB, "start", "--stage", "S1"], 0)
run("产出缺失去放行", [s("state.py"), "--project", LAB,
    "approve", "--gate", "G1", "--by", "张三"], 2)

# 验收3b：智能体署名放行（先补齐产出）
for a in ["docs/requirements.md", "docs/acceptance.md", "docs/architecture.md",
          "docs/adr/ADR-001.md", "docs/api-contract.md", "prototype/index.html",
          "docs/open-items.md"]:
    p = os.path.join(LAB, a)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w", encoding="utf-8").write("# x\n")
run("智能体署名放行", [s("state.py"), "--project", LAB,
    "approve", "--gate", "G1", "--by", "Qoder-Agent"], 4)

# 验收4：两工单同一文件，不同写法
run("工单A", [s("tickets.py"), "--project", LAB, "add", "--id", "TA",
    "--title", "a", "--role", "implementer", "--owns", "src/app.py"], 0)
run("工单B同文件不同写法", [s("tickets.py"), "--project", LAB, "add",
    "--id", "TB", "--title", "b", "--role", "implementer",
    "--owns", r".\src\app.py"], 2)

# 验收5：门禁配置引用不存在的环境
cfgp = os.path.join(LAB, ".pipeline", "config.json")
cfg = json.load(open(cfgp, encoding="utf-8"))
cfg["gates"]["checks"].append({"id": "ghost", "adapter": "python-builtin",
                               "run": [["python", "-c", ""]],
                               "env": ["dev", "qa"], "gates": ["G2"],
                               "on_fail": "self_heal"})
json.dump(cfg, open(cfgp, "w", encoding="utf-8"), ensure_ascii=False)
run("幽灵环境门禁运行", [s("gate.py"), "--project", LAB,
    "run", "--gate", "G2"], 3)
cfg = json.load(open(cfgp, encoding="utf-8"))
cfg["gates"]["checks"] = [c for c in cfg["gates"]["checks"]
                          if c["id"] != "ghost"]
json.dump(cfg, open(cfgp, "w", encoding="utf-8"), ensure_ascii=False)

# 验收8：密钥扫描豁免
run("密钥豁免", [s("gate.py"), "--project", LAB, "exempt",
    "--check", "secrets", "--env", "dev", "--by", "李四",
    "--until", "2099-01-01", "--reason", "开发环境无所谓"], 4)

# 验收10：自愈轮次上限
run("自愈第1轮", [s("state.py"), "--project", LAB,
    "heal-round", "--gate", "G2"], 0)
run("自愈第2轮(应要求停止)", [s("state.py"), "--project", LAB,
    "heal-round", "--gate", "G2"], 2)

print("\n== 违例串联结束 ==")
if FAILED:
    print("不符合预期: %s" % ", ".join(FAILED))
    sys.exit(1)
print("全部符合预期。")
sys.exit(0)
