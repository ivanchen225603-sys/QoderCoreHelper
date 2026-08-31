# 契约表：环节产出 = 下游输入（同一份文件，写死在此）

环节之间靠文件交接，不靠对话。每个子 Agent 冷启动，对话传不过去；
上游的产出就是下游的输入，落成文件才能跨会话、跨 Agent、跨几天的中断
继续。下表在 `scripts/layout.py` 的 `CONTRACT` 中由脚本强制：放行前
逐条校验存在，派发包的 inputs 只许引用这些路径。

| 环节 | 产出（= 下游输入） | 交给谁 |
|------|-------------------|--------|
| S1 需求与架构 | `docs/requirements.md` 需求与范围 | 全体下游 |
| | `docs/acceptance.md` 可判定验收标准 | 测试（S3）、评审（S4） |
| | `docs/architecture.md` 技术方案 | 实现（S2） |
| | `docs/adr/ADR-001.md` 决策记录（生成待批） | 评审、发布 |
| | `docs/api-contract.md` 接口契约 | 测试、评审、实现 |
| | `prototype/index.html` 原型（生成待批） | 用户判断交互方向 |
| | `docs/open-items.md` 需决策/待批清单 | 关卡呈现 |
| S2 编码实现 | 工单 dod 文件（账本校验）+ `latest-dev.json` 门禁报告 | 测试、评审 |
| S3 测试验证 | 工单 dod（测试文件）+ `reports/test-run.json` + `latest-dev.json` | 评审、发布 |
| S4 独立评审 | `reports/changes.diff`（`render diff` 工具渲染的评审面）+ `reports/review.md` | G4 关卡、实现者返工输入 |
| S5 安全扫描 | `reports/security-scan.json` + `latest-staging.json` | 发布 |
| S6 发布准备 | `Dockerfile`、`ci/ci.yml`、`docs/runbook.md`、`docs/rollback.md`、`docs/release-notes.md`、`reports/publish-checklist.md` | G7 由人执行 |
| S7 发布 | 无智能体产出——执行属第三档，人来做 | — |

## 执行含义
- **"口头交接"在多 Agent 里等于没有交接**：没进契约表的文件，下游不许读。
- 评审者的输入不是实现者的代码库全貌，而是 `render diff` 预渲染的评审面文件
  （+上表的验收标准与契约）——"这次评审看了哪些行"成为可审计的产物；
  S4 放行校验强制检查该文件存在。
- 测试编写者的输入只有验收标准与接口契约，不含实现代码。
- 任何环节想新增产出给下游：先改 `layout.py` 的 CONTRACT 再生产，
  让交接始终有单一事实来源。
