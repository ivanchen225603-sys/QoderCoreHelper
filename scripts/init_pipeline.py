#!/usr/bin/env python
# init_pipeline.py —— 初始化：探测技术栈、写配置、装门禁模板与子 Agent 定义。
#
# 硬性规则（需求原文）：探测不到技术栈就明确报错，不许瞎猜一个。
# 探测到多个也报错让用户显式指定——猜错的代价是整套门禁对着错误的
# 工具链空转，且看起来一切正常。
import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import layout
import gate
import pio

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(SKILL_DIR, "assets")

MARKERS = {
    "python": ["pyproject.toml", "setup.py", "requirements.txt"],
    "node": ["package.json"],
}


def detect_stacks(project):
    found = []
    for stack, files in MARKERS.items():
        if any(os.path.exists(os.path.join(project, f)) for f in files):
            found.append(stack)
    return found


def detect_capabilities(project, args):
    """探测目标平台能力（第六节适配表），探测结果写入 config 并如实打印。"""
    caps = {}
    caps["script_exec"] = True  # 能跑本脚本即成立
    caps["git"] = shutil.which("git") is not None
    caps["worktree"] = caps["git"]
    # 子 Agent 派发：Qoder 平台具备（Agent 工具）；单会话平台置否
    caps["subagents"] = not args.no_subagents
    # 子 Agent 工具白名单：Qoder 自定义子代理（.qoder/agents/*.md）支持
    # tools 字段（2026-09 实测确认）——隔离是机制，不是约定。
    # 非 Qoder 平台用 --no-tool-whitelist 显式降级，降级必须声明。
    caps["tool_whitelist"] = not args.no_tool_whitelist
    return caps


def _md_frontmatter(path):
    """解析 .md 子代理定义的 YAML frontmatter（只取顶层 k: v）。
    utf-8-sig 同时容忍带/不带 BOM 的文件（跨平台生成的产物不一致）。"""
    with open(path, encoding="utf-8-sig") as f:
        raw = f.read()
    if not raw.startswith("---"):
        pio.die(layout.EXIT_CONFIG, "子代理定义缺 frontmatter: %s" % path)
    end = raw.find("---", 3)
    fields = {}
    for line in raw[3:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fields[k.strip()] = v.strip()
    return fields


def install_agent_defs(project, pp):
    """把带工具白名单的子 Agent 定义装进目标项目。
    - assets/agents-md/*.md → 项目 .qoder/agents/（Qoder 原生子代理，
      tools 白名单由平台强制执行）
    - assets/agents/*.json  → .pipeline/agent-defs/（注册记录留档）
    两者安装前都校验白名单未被放宽（不变量 11 的机制）。"""
    installed = []
    md_src = os.path.join(ASSETS, "agents-md")
    if os.path.isdir(md_src):
        tgt = os.path.join(project, ".qoder", "agents")
        os.makedirs(tgt, exist_ok=True)
        for fn in sorted(os.listdir(md_src)):
            if not fn.endswith(".md"):
                continue
            spec = _md_frontmatter(os.path.join(md_src, fn))
            role = spec.get("name")
            policy = layout.QODER_NATIVE_TOOL_POLICY.get(role)
            if policy is not None:
                tools = [t.strip() for t in spec.get("tools", "").split(",")
                         if t.strip()]
                extra = set(tools) - set(policy)
                if extra:
                    pio.die(layout.EXIT_VIOLATION,
                            "子代理定义 %s 的工具白名单被放宽（多出: %s），"
                            "拒绝安装。放宽白名单 = 隔离失效。"
                            % (fn, sorted(extra)))
            shutil.copy2(os.path.join(md_src, fn), os.path.join(tgt, fn))
            os.makedirs(os.path.join(pp["root"], "agent-defs"), exist_ok=True)
            shutil.copy2(os.path.join(md_src, fn),
                         os.path.join(pp["root"], "agent-defs", fn))
            installed.append(fn)

    src = os.path.join(ASSETS, "agents")
    if not os.path.isdir(src):
        pio.die(layout.EXIT_CONFIG, "assets/agents 缺失，安装包不完整: %s" % src)
    for fn in sorted(os.listdir(src)):
        if not fn.endswith(".json"):
            continue
        spec = pio.load_json(os.path.join(src, fn))
        role = spec.get("name")
        policy = layout.ROLE_TOOL_POLICY.get(role)
        if policy:
            extra = set(spec.get("tools", [])) - set(policy["allow"])
            if extra:
                pio.die(layout.EXIT_VIOLATION,
                        "子 Agent 定义 %s 的工具白名单被放宽（多出: %s），"
                        "拒绝安装。放宽白名单 = 隔离失效。" % (fn, sorted(extra)))
        adir = os.path.join(pp["root"], "agent-defs")
        os.makedirs(adir, exist_ok=True)
        shutil.copy2(os.path.join(src, fn), os.path.join(adir, fn))
    return installed


def main():
    ap = argparse.ArgumentParser(description="初始化流水线")
    ap.add_argument("--project", default=".", help="目标项目根目录")
    ap.add_argument("--stack", choices=list(MARKERS),
                    help="显式指定技术栈（探测有歧义时必须指定）")
    ap.add_argument("--environments", nargs="+",
                    default=["dev", "staging", "prod"])
    ap.add_argument("--no-subagents", action="store_true",
                    help="目标平台不能派发子 Agent（单会话串行）")
    ap.add_argument("--no-tool-whitelist", action="store_true",
                    help="目标平台不支持限定子 Agent 工具（隔离退回纯约定）")
    ap.add_argument("--force", action="store_true",
                    help="已有 .pipeline 时覆盖重装")
    args = ap.parse_args()
    project = os.path.abspath(args.project)
    if not os.path.isdir(project):
        pio.die(layout.EXIT_CONFIG, "项目目录不存在: %s" % project)

    pp = layout.pipeline_paths(project)
    if os.path.exists(pp["config"]) and not args.force:
        pio.die(layout.EXIT_FAIL,
                "项目已初始化（%s）。重新安装请加 --force（会覆盖配置，"
                "放行记录与账本保留在 state/tickets 中）。" % pp["config"])

    # ── 技术栈探测：探测不到就报错，不许瞎猜 ─────────────────────────
    found = detect_stacks(project)
    if not found:
        pio.die(layout.EXIT_CONFIG,
                "探测不到技术栈：%s 下没有 %s。初始化拒绝继续——"
                "猜一个技术栈会让整套门禁对着错误的工具链空转。"
                "请先给项目放入工程文件，或用 --stack 显式指定。"
                % (project, " / ".join(sum(MARKERS.values(), []))))
    if len(found) > 1 and not args.stack:
        pio.die(layout.EXIT_CONFIG,
                "探测到多个技术栈（%s），无法替你决定。"
                "请用 --stack 显式指定主栈。" % ", ".join(found))
    stack = args.stack or found[0]

    # ── 门禁模板装载 ─────────────────────────────────────────────────
    tpl = os.path.join(ASSETS, "gates", stack, "gates.json")
    if not os.path.exists(tpl):
        pio.die(layout.EXIT_CONFIG,
                "没有 %s 栈的门禁模板: %s（现有模板: %s）"
                % (stack, tpl,
                   ", ".join(os.listdir(os.path.join(ASSETS, "gates")))
                   if os.path.isdir(os.path.join(ASSETS, "gates")) else "无"))
    gates_section = pio.load_json(tpl)

    for d in ("reports", "packs", "roles", "adapters", "locks",
              "agent-defs", "scripts"):
        os.makedirs(os.path.join(pp["root"], d), exist_ok=True)

    # ── 适配器声明安装 ───────────────────────────────────────────────
    ad_src = os.path.join(ASSETS, "adapters")
    for fn in sorted(os.listdir(ad_src)):
        if fn.endswith(".json"):
            shutil.copy2(os.path.join(ad_src, fn),
                         os.path.join(pp["adapters"], fn))

    # ── 角色说明安装（派发包渲染要用）────────────────────────────────
    roles_src = os.path.join(SKILL_DIR, "agents")
    for fn in sorted(os.listdir(roles_src)):
        if fn.endswith(".md"):
            shutil.copy2(os.path.join(roles_src, fn),
                         os.path.join(pp["roles"], fn))

    # 脚本副本安装：门禁模板里的命令引用 .pipeline/scripts/*，
    # 项目因此自包含，跨会话不依赖 skill 目录位置。
    scripts_src = os.path.dirname(os.path.abspath(__file__))
    scripts_dst = os.path.join(pp["root"], "scripts")
    for fn in sorted(os.listdir(scripts_src)):
        if fn.endswith(".py"):
            shutil.copy2(os.path.join(scripts_src, fn),
                         os.path.join(scripts_dst, fn))

    caps = detect_capabilities(project, args)

    config = {
        "version": 1,
        "stack": stack,
        "environments": list(args.environments),
        "gates": gates_section,
        "gates_template_hash": gate.gates_hash(gates_section),
        "exemptions": [],
        "capabilities": caps,
    }
    # 装完立刻自检：模板本身不合法就当场报错，别把坏配置写进去
    errs = gate.validate_config(config)
    if errs:
        pio.die(layout.EXIT_CONFIG, "门禁模板自检失败", errors=errs)
    pio.save_json(pp["config"], config)

    # ── 状态机初始化：所有关卡一律 pending（不变量 3）────────────────
    if not os.path.exists(pp["state"]):
        st = {
            "stages": {s: {"status": "pending"} for s in layout.STAGES},
            "gates": {g: {"status": "pending", "approved_by": None,
                          "approved_at": None} for g in layout.GATES},
            "self_heal": {},
            "log": [{"at": "init", "event": "initialized", "stack": stack}],
        }
        pio.save_json(pp["state"], st)
    if not os.path.exists(pp["tickets"]):
        pio.save_json(pp["tickets"], {"tickets": []})

    agent_files = install_agent_defs(project, pp)

    # ── 能力声明（第六节）：降级必须显式说出来 ───────────────────────
    decl = [
        "技术栈: %s | 环境: %s" % (stack, ", ".join(args.environments)),
        "脚本执行: 可用（门禁/账本/探针走脚本）",
        "子 Agent 派发: %s" % ("可用（按角色冷启动派发）"
                              if caps["subagents"] else
                              "不可用 → 单会话串行执行；工单账本照常使用，"
                              "它同时是审计记录"),
        "独立工作副本: %s" % ("git 可用（并行时物理隔离）"
                             if caps["worktree"] else
                             "不可用 → 靠文件独占检查控制并行度"),
        "子 Agent 工具白名单: %s" % (
            "可用（隔离是机制：原生子代理的 tools 字段由平台强制执行）"
            if caps["tool_whitelist"] else
            "不可用 → **隔离已退回成纯约定**（评审无编辑工具等规则仅靠"
            "提示词约束）。本声明会在每次派发包与关卡卡片中重复。"),
        "子 Agent 定义已安装: %s" % ", ".join(agent_files),
    ]
    pio.ok("流水线初始化完成：%s" % pp["root"],
           declarations=decl, stack=stack,
           config=os.path.relpath(pp["config"], project))


if __name__ == "__main__":
    main()
