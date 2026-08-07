# v1.0 → v1.0.1 Regression Benchmark

## A/B 边界

`compare-skill-artifacts` 只比较用户消费内容：`SKILL.md`、`references/`、
`assets/` 和 `scripts/`。质量报告、兼容报告、规范化快照、编译清单和元数据是
诊断层，不会伪装成正文回归。

判定规则：新增、删除或改变任一消费文件都会得到 `content_stable=false`；只新增
1.0.1 诊断文件应保持 `content_stable=true`。

## 回归集映射

| 类别 | 仓库证据 | 关注点 |
|---|---|---|
| 结构清晰短文 | `tests/test_benchmark_slots.py` A 类 | 稳定结构、来源覆盖 |
| 流程型方法资料 | B 类与 Build fixtures | 工作流可执行性 |
| 复杂方法论资料 | C 类、冲突/审核 fixtures | 冲突保留、预算与拆分建议 |
| 格式困难样本 | extractor 的损坏、加密、扫描、编码测试 | 明确降级与错误码 |
| 不适合转换反例 | 合法性、DRM、空文件、注入测试 | 拒绝而非生成 |

本轮模板版本保持 `skill-writer-v1`，因此灰度策略是“正文模板不切换，仅增加可
回滚的诊断层”。后续模板升级必须复用相同 fixture 运行 A/B，并单独提升模板版本。
