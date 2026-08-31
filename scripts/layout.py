# layout.py —— 全流程流水线的常量、契约表与退出码。
# 为什么集中在这一份文件：契约表是上下游交接的唯一事实来源（不变量 5），
# 散落在各脚本里就会出现"上游改了路径下游不知道"的静默断裂。
import os

# ── 退出码约定（不变量：区分"未通过"和"配置错"）──────────────────────
# 0 = 通过/成功；2 = 业务性未通过（门禁未达标、认领冲突等，属预期内结果）
# 3 = 配置错误（模板装错、引用不存在的环境等，检查根本没跑，绝不能显示通过）
# 4 = 协议违例（智能体自我放行、跳过关卡等不变量级别的拒绝）
# 1 = 未预期异常
EXIT_OK = 0
EXIT_FAIL = 2
EXIT_CONFIG = 3
EXIT_VIOLATION = 4

# ── 七个环节与七道关卡 ──────────────────────────────────────────────
# G(n) 守在 S(n) 出口：G(n) 未放行，S(n+1) 不得开始（不变量 2）。
STAGES = ["S1", "S2", "S3", "S4", "S5", "S6", "S7"]
STAGE_NAMES = {
    "S1": "需求与架构",
    "S2": "编码实现",
    "S3": "测试验证",
    "S4": "独立评审",
    "S5": "安全扫描",
    "S6": "发布准备",
    "S7": "发布",
}
GATES = ["G1", "G2", "G3", "G4", "G5", "G6", "G7"]
# 关卡类型（不变量 1：两类关卡分开）
# human = 环节关卡，人判断"该不该做成这样"；env = 环境门禁，脚本判断"做得对不对"
GATE_KIND = {
    "G1": "human",   # 需求+架构+原型合并呈现（三份产出缺一不可）
    "G2": "env",     # 构建/lint/类型/单测/覆盖率/密钥
    "G3": "env",     # 独立测试套件 + 覆盖率
    "G4": "human",   # 评审结论处置（人判断）
    "G5": "env",     # 安全扫描（密钥零豁免）
    "G6": "human",   # 发布准备审查（含生成待批清单）
    "G7": "human",   # 发布：第三档，按按钮的必须是人
}
# 环境门禁在哪个环境执行（阈值随环境递增，不变量 13）
GATE_ENV = {"G2": "dev", "G3": "dev", "G5": "staging", "G6": "prod"}
GATE_OF_STAGE = {"S1": "G1", "S2": "G2", "S3": "G3", "S4": "G4",
                 "S5": "G5", "S6": "G6", "S7": "G7"}

# ── 契约表：上游产出 = 下游输入，必须是同一份文件（不变量 4/5）──────
# 每个环节结束后，approve 会逐条校验这些产出真实存在（不变量 2），
# 下游环节的派发包 inputs 只允许引用这些路径。
CONTRACT = {
    "S1": [
        "docs/requirements.md",      # 需求 + 范围
        "docs/acceptance.md",        # 验收标准（测试/评审的共同输入）
        "docs/architecture.md",      # 技术方案与架构
        "docs/adr/ADR-001.md",       # 架构决策记录（生成待批）
        "docs/api-contract.md",      # 接口契约
        "prototype/index.html",      # 原型（交互方向，生成待批）
        "docs/open-items.md",        # 需决策项 / 待批项清单
    ],
    # S2 的产出是工单账本里的 dod 文件，由工单系统校验，不在此列死路径。
    "S2": ["@tickets_done", ".pipeline/reports/latest-dev.json"],
    "S3": ["@tickets_done", "reports/test-run.json",
           ".pipeline/reports/latest-dev.json"],
    "S4": ["reports/changes.diff", "reports/review.md"],
    "S5": ["reports/security-scan.json",
           ".pipeline/reports/latest-staging.json"],
    "S6": ["Dockerfile", "ci/ci.yml", "docs/runbook.md",
           "docs/rollback.md", "docs/release-notes.md",
           "reports/publish-checklist.md"],
    "S7": [],  # 发布由人执行，智能体只到清单为止
}

# @标记的伪产出含义
PSEUDO_ARTIFACTS = {"@tickets_done": "所有工单状态为 done 且 dod 文件存在"}

# ── 放行人性校验（不变量 3：只有人能放行）──────────────────────────
# 智能体给自己盖章是最常见也最致命的失效，脚本层直接拒绝这些署名。
AGENT_NAME_BLOCKLIST = {
    "agent", "subagent", "assistant", "qoder", "bot", "ai", "auto",
    "copilot", "claude", "gpt", "llm", "pipeline", "system", "ci",
    "script", "robot",
}

# ── 角色工具策略（不变量 11：能用机制强制的，不只写在提示词里）──────
# 白名单之外的工具一律视为"被悄悄放宽"，verify 会拒绝（回归测试钉死）。
ROLE_TOOL_POLICY = {
    # 评审者：只读。不给编辑工具（顺手改就没人复核了），不给命令行
    # （翻仓库历史会读到实现脉络，破坏隔离）；diff 由主会话预渲染成文件。
    "reviewer": {"allow": ["Read", "Grep", "SearchCodebase", "LSP", "Glob"]},
    # 门禁执行者：只跑门禁脚本、只读。不给任何写工具——它不可能
    # "顺手把门禁弄绿"，结论才可信（不变量 12）。
    "gate-runner": {"allow": ["Bash", "Read", "Grep", "Glob"]},
    # 测试编写者：只读契约/验收 + 写测试目录。拿不到实现代码（不变量 10）。
    "tester": {"allow": ["Read", "Grep", "Glob", "SearchCodebase",
                         "Write", "SearchReplace", "Bash"]},
    # 实现者：全量开发工具。
    "implementer": {"allow": ["Read", "Write", "SearchReplace", "edit_file",
                              "Bash", "Grep", "Glob", "SearchCodebase",
                              "LSP", "GetProblems"]},
    # 需求/架构分析师：读 + 写文档，不给命令行（该环节不需要跑任何东西）。
    "analyst": {"allow": ["Read", "Write", "SearchReplace", "Grep", "Glob",
                          "SearchCodebase"]},
    # 发布工程师：可写发布物与脚本，但发布执行本身是第三档（人来按）。
    "release": {"allow": ["Read", "Write", "SearchReplace", "Bash", "Grep",
                          "Glob", "SearchCodebase"]},
    # 文档作者：不给命令行（不变量 11 举例）。
    "doc-writer": {"allow": ["Read", "Write", "SearchReplace", "Grep",
                             "Glob", "SearchCodebase"]},
}

# 门禁脚本允许执行的前缀白名单：门禁执行者角色只能跑这些命令，
# 防止"评审/门禁角色借 Bash 做别的"。
GATE_RUNNER_ALLOWED_CMDS = ("python", "python3", "node", "npm", "npx",
                            "pytest", "ruff", "mypy", "gitleaks", "trivy",
                            "pip", "git")

# 自愈上限（不变量：2 轮，轮次落盘、跨会话不重置）
SELF_HEAL_MAX_ROUNDS = 2

# ── 目录布局 ─────────────────────────────────────────────────────────
PIPELINE_DIR = ".pipeline"

def pipeline_paths(project_root):
    """返回 .pipeline 下的标准路径，全部相对 project_root。"""
    p = os.path.join(project_root, PIPELINE_DIR)
    return {
        "root": p,
        "config": os.path.join(p, "config.json"),
        "state": os.path.join(p, "state.json"),
        "tickets": os.path.join(p, "tickets.json"),
        "reports": os.path.join(p, "reports"),
        "packs": os.path.join(p, "packs"),
        "roles": os.path.join(p, "roles"),
        "adapters": os.path.join(p, "adapters"),
        "locks": os.path.join(p, "locks"),
    }
