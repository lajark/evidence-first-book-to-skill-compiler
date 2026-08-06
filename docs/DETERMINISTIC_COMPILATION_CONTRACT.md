# Deterministic Compilation Contract

Book2Skill 1.0.1 的可回放阶段链为：

`AnalysisBundle → NormalizedBundle → SkillIR → Skill package → QualityReport / CompatibilityReport`

## NormalizedBundle

- 文件名：`normalized-bundle.json`
- Schema：`schemas/normalized-bundle.schema.json`
- 身份：`normalized_bundle_id` 是规范 JSON 的 SHA-256。
- 输入引用：`input_bundle_sha256` 记录上游候选合同或 Schema 当前视图。
- 版本锁：记录 Core 版本、模板版本和精确 Agent Skills 规范 Commit。
- 隐私：引文正文不重复落盘；仅记录 `quote_sha256` 与 `quote_chars`。
- 排序：来源、单位、冲突和审核队列按稳定键排序，异步完成顺序不影响结果。

可用命令：

```powershell
book2skill normalize output/bundles/bundle.json --output normalized-bundle.json
```

`NormalizedBundle` 是派生快照而非事实源。更新知识时应修改/追加 Schema 记录，
再重新生成快照；不得直接编辑快照冒充成功编译。

## 失败与恢复

- 重复单位 ID 由 `AnalysisBundle` 合同拒绝。
- 悬空或 `unknown` 来源在规范化阶段拒绝。
- 篡改后的快照在加载和证据边界校验时拒绝。
- Build 使用 staging 和原子替换；Publish 在质量门通过前不触碰当前版本。
- 开放冲突和未批准单位继续由既有发布前置条件阻断，草稿可保留供审核。

## SDK

SDK 0.2 新增 `NormalizationService`、`CompatibilityService`、
`NormalizedBundle`、`CompatibilityReport`、`TargetSpec` 与 `EvidenceLevel`。
下游扩展应只通过 `book2skill.sdk` 使用这些合同。
