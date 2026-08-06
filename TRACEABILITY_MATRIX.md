# 需求追踪矩阵

| 需求 | 设计/Schema | 任务 | 验收 |
|---|---|---|---|
| FR-01/02 | source-manifest/extraction-map/FORMAT_ADAPTERS | B2S-M1/M2 | 格式、错误、哈希样本 |
| FR-03 | analysis-bundle | B2S-M1-03/M3-02/M4 | 四模式 E2E |
| FR-04/05 | skill-ir/knowledge-unit | B2S-M3 | Skill 结构与来源 |
| FR-06 | conflict/override/diff | B2S-M4 | 更新、冲突、回滚 |
| FR-07/08 | quality-report/validators | B2S-M5 | 安全与版权样本 |
| FR-09/10 | CLI/run-record | B2S-M0/M4/M5 | 命令与恢复证据 |
| FR-11 | extension manifest/SDK/registry | B2S-M5-03 | 安装、依赖、升级、回滚 |
| FR-12 | release manifest/package | B2S-M5-04/M6 | 干净环境顺序安装 |
| FR-14 | target-spec/compatibility-report/validator-result | B2S-M7-01/02/04 | 锁定规范；内部/官方/第三方结果分层；结构/安装/运行状态分级 |
| FR-15 | normalized-bundle/migration/replay manifest | B2S-M7-03/04 | 固定输入稳定、来源链可回放、v1 往返与回滚、不形成第二事实源 |
| FR-16 | skill-design review/trigger fixtures | B2S-M8-01/02/04 | 单任务边界、相邻负向触发、执行断言、新旧模板对比 |
| FR-05/06/16 | evidence/input-output contracts | B2S-M8-03 | 证据分级、前置条件、不变量、核心规则来源约束 |

## v1.0.1 实施证据

- FR-14：`domain/contracts.py`、`validation/compatibility.py`、`compatibility-report.schema.json`、`docs/COMPATIBILITY_MATRIX.md`。
- FR-15：`application/normalized_bundle.py`、`normalized-bundle.schema.json`、ADR-002、迁移与篡改/回放测试。
- FR-16：`application/skill_design.py`、设计审查/fixture Schema、`application/regression.py` 与内容 A/B 测试。
- FR-05/06/16：`EvidenceLevel`、`EvidenceBoundaryCheck`、typed execution contract 和旧 v1 默认值迁移测试。
