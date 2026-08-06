# v1.0.1 Migration and Rollback

## 向后兼容迁移

- `CandidateUnit` / `KnowledgeUnit` 缺少 `evidence_level` 时按 `primary` 读取。
- `evidence_note` 默认 `null`；`inferred` 与 `user_added` 必须提供非空说明。
- `SuggestedSkill` 新增的任务边界、路由、触发例和拆分字段均有默认值。
- `AnalysisBundle`、`KnowledgeUnit` 和 `SkillIR` 的 `schema_version` 仍为 1；
  本次没有 Schema 主版本迁移。
- SDK 从 0.1 增量升级为 0.2；既有公开名称不删除、不改签名。

重新执行 Build 会新增：

- `normalized-bundle.json`
- `skill-design-review.json`
- `skill-fixtures.json`
- `compatibility-report.json` / `.md`

`SKILL.md`、references、assets 和 scripts 的生成模板保持
`skill-writer-v1`。可用下列命令验证内容未回退：

```powershell
book2skill compare-skill-artifacts path/to/v1.0 path/to/v1.0.1 --json
```

## 回滚

1. 发布失败时沿用现有原子发布/快照回滚，不提交无效快照。
2. 下游暂不消费新合同，可忽略新增诊断文件并继续读取原有 v1 字段。
3. 若需退回 1.0.0，先确认没有扩展依赖 SDK 0.2 新名称，再安装旧包；产品
   Raw、Schema 历史和已发布 Skill 不需要降级或重写。
4. 不直接编辑或删除 Raw；需要重建时从已验证的 AnalysisBundle/Schema 当前视图
   重新运行规范化和 Build。
