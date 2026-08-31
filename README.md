# swe-pipeline —— 软件工程全流程流水线 Skill（Qoder）

把一句话需求或一份 PRD 自动推进到可发布的最后一步：七个环节
（需求架构 → 实现 → 测试 → 评审 → 安全 → 发布准备 → 发布）由状态机串联，
只在必须人担责的关卡停下等放行。用户不写代码、不写配置、不调流水线，
只在关卡上回答"放行还是打回"。

## 换设备恢复（3 步）

```
1. git clone https://github.com/ivanchen225603-sys/QoderCoreHelper.git
2. 把本仓库整个目录复制为技能目录（目录名必须叫 swe-pipeline）：
   - 个人级（跨项目可用）:  ~/.qoder-cn/skills/swe-pipeline/
   - 项目级（随仓库共享）:  <项目>/.qoder/skills/swe-pipeline/
3. 在 Qoder 里说"从需求到上线做一个 ×××"即可触发；
   首次使用会自动执行初始化（需要 python 在 PATH）。
```

Windows 示例（PowerShell）：

```powershell
git clone https://github.com/ivanchen225603-sys/QoderCoreHelper.git
Copy-Item -Recurse .\QoderCoreHelper "$env:USERPROFILE\.qoder-cn\skills\swe-pipeline"
```

## 恢复后自检（1 分钟）

```
python scripts/test_pipeline.py      # 期望：Ran 29 tests — OK
```

回归测试每个用例钉死一个真实缺陷类；跑不过说明环境或文件有缺，
先修再用（这是该 skill 自己的硬性规则）。

## 目录结构

```
SKILL.md              主执行契约（智能体加载时只读这一份）
agents/               7 个角色的职责说明（派发子 Agent 时随工单给出）
assets/agents/        子 Agent 注册定义（工具白名单，安装进目标项目）
assets/gates/         门禁模板（python / node 两套，阈值随环境递增）
assets/adapters/      外部工具适配器声明（编码/单测/扫描/CI…，各带降级方案）
references/           按需加载的参考文档（SKILL.md 内有路由表）
scripts/              真正干活的脚本 + 自身回归测试
```

## 依赖

- Python 3.10+（标准库即可，脚本层零第三方依赖）
- 目标项目的工具链（pytest/coverage/ruff/mypy 等）：缺失时门禁按适配器
  声明降级并在关卡上明示，不会静默跳过

## 已知边界

- 目标平台若不支持给子 Agent 限定工具，隔离退回纯约定——初始化与每次
  派发包/关卡卡片都会显式打印此声明，不会静默降级。
- 详见 `references/platform-adaptation.md` 与 `references/extend-stack.md`。
