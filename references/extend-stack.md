# 扩展新技术栈（操作手册）

门禁模板是开放目录设计：加一个栈不改核心逻辑，照下面四步走，
每步都有脚本自检兜底。

## 步骤
1. **加探测标记**：`scripts/init_pipeline.py` 的 `MARKERS` 里加
   `"<stack>": ["<工程文件>", ...]`。标记必须是"存在即成立"的文件，
   不许用内容推断（猜错的代价是门禁对错误工具链空转）。
2. **加门禁模板**：`assets/gates/<stack>/gates.json`，字段规范照抄
   python 模板。三条硬约束（`gate.py validate-config` 会校验）：
   - 每项 `env` 必须是 `environments` 的子集（否则装载即拒）
   - `thresholds` 必须随环境顺序非递减
   - `adapter` 必须已有适配器声明
3. **加适配器声明**：`assets/adapters/<工具>.json`。`detect` 四选一：
   `binary`（PATH 查找）/ `python_module` / `file` / `always`。
   `degrade.mode` 二选一：`fallback_builtin`（必须有内置兜底命令，
   密钥类检查只允许这个）/ `mark_degraded`（标记降级，上关卡说明）。
4. **跑回归 + 模板自检**：
   ```
   python scripts/test_pipeline.py
   python scripts/init_pipeline.py --project <测试项目> --stack <stack>
   python scripts/gate.py --project <测试项目> verify-config
   ```
   任一失败不许合入。

## 已知边界（如实声明）
- 探针 `coverage` 目前支持：coverage.py JSON / lcov / cobertura XML /
  istanbul(c8) JSON / 纯数字与控制台文本。jacoco、go cover、opencover
  未覆盖——补格式时在 `probes.py` 加分支并配一条回归用例。
- 单仓库多栈：`init --stack` 目前单选；双栈并存需要合并两份模板的
  checks（手工合进 config，`verify-config` 会把关）。
