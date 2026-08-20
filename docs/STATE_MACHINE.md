# Update 生命周期状态机

Update journal 使用稳定的 `TransactionState` 值记录 Skill 发布与 Schema 提交之间的恢复边界。所有持久化状态变更都经过 `UpdateTransactionStore.update()` 的转移守卫；未知或跳跃转移返回 `INVALID_STATE_TRANSITION`，不会覆盖 journal。

```text
prepared
   │
   ▼
publishing ───────────────► rollback_required
   │                                  ▲
   ▼                                  │
skill_published ──► schema_committed ┘
   │                       │
   └──────────────► committed ◄────────┘
```

允许的正常路径是：

`prepared → publishing → skill_published → schema_committed → committed`

崩溃恢复可以在确认 Skill 与 Schema 都已可见时直接补齐 `committed`；任何无法自动恢复的失败进入 `rollback_required`，需要人工核验后处理。终态 `committed` 与 `rollback_required` 不允许回到其他状态。

领域层的 `ExtractionStatus`、`KnowledgeStatus`、`ConflictStatus` 和 `PublishStatus` 仍是数据语义枚举，不与 Update journal 的持久化事务状态混用；发布器自身的快照 journal 也保持独立，以避免跨事务复用状态值。
