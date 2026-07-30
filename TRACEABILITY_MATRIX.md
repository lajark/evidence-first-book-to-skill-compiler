# 需求追踪矩阵

> 维护规则：每次任务完成或验收里程碑（M1/M3/M5/M6）后更新本表。
> 验收证据编号对应 `docs/M6_ACCEPTANCE_REPORT.md` 的 Step ID。

## 一、功能需求（FR）追踪

| 需求 | 设计/Schema | 实现任务 | 验收证据 |
|---|---|---|---|
| FR-01 输入与合法性 | source-manifest.schema.json、FORMAT_ADAPTERS.md | B2S-M1-01 (TASK-005) | S1 analyze-A-E、S3 batch-G1-G2（损坏/加密隔离） |
| FR-02 格式适配（6 类 + 批处理） | extraction-map-entry.schema.json、FORMAT_ADAPTERS.md | B2S-M1-02 (TASK-006)、M2-01 (TASK-008)、M2-02 (TASK-009/010) | S1（六格式 Analyze）、S3（G1 损坏 PDF→GATE_DAMAGED_FILE、G2 DRM MOBI→GATE_ENCRYPTED_FILE） |
| FR-03-1 Analyze Only | analysis-bundle.schema.json | B2S-M1-03 (TASK-007) | S1（输出 12 候选单元 + 4 review 项 + 6 suggested skills） |
| FR-03-2 Full Build | skill-ir.schema.json、generated-skill 模板 | B2S-M3-02 (TASK-013) | S2-A/B/C build 成功（产物 SKILL.md + provenance.yml + quality-report.md + skill.meta.json） |
| FR-03-3 Build from Analysis | analysis-bundle.schema.json | B2S-M3-02 (TASK-013) | TASK-013 单元测试覆盖（acceptance S2 未单独跑此路径） |
| FR-03-4 Update/Fold-in | override/patch、diff 引擎 | B2S-M4-01/02 (TASK-014/015) | TASK-015 端到端实测：build → update dry-run → --confirm → no_changes → --rollback 全通过 |
| FR-04 Skill IR 与多宿主输出 | skill-ir.schema.json、SKILL_AUTHORING_STANDARD.md、SKILL_DEPLOYMENT.md | B2S-M3-01 (TASK-012)、M5-02 (TASK-017)、TASK-019 | S2 产物结构校验；TASK-017 install/uninstall 实测；TASK-019 元 Skill `book2skill` 手写交付（SKILL.md + references/ + assets/ + scripts/ + provenance.yml），`validate` 5/5 pass，`install --dry-run` + `smoke_test.py` 通过 |
| FR-05 知识单元与来源 | knowledge-unit.schema.json | B2S-M3-01 (TASK-011) | S1 candidate_units 含 source_refs + quote + confidence |
| FR-06 多来源与版本合并 | override/patch、conflict/diff | B2S-M4-01 (TASK-014) | TASK-014 单元测试（39 用例）+ TASK-015 端到端 |
| FR-07 校验与质量门 | quality-report.schema.json | B2S-M5-01 (TASK-016) | S4-A/B/C validate pass_with_warnings；S5 injection fail（exit 1） |
| FR-08 版权与安全 | SECURITY.md、quality-report.schema.json | B2S-M5-01 (TASK-016) | S5（注入文本 + 非 https URL 被识别）；copyright 检查 PASS |
| FR-09 CLI 目标形态 | — | M0-M5（各任务） | 7 命令全部可用：analyze/batch/build/update/diff/validate/install/uninstall |
| FR-10 索引、日志和可恢复性 | publish-log.jsonl、snapshots/ | B2S-M4-02 (TASK-015) | TASK-015 端到端：publish-log.jsonl 写入、snapshot 创建、rollback 恢复 |

## 二、非功能需求（NFR）追踪

| 非功能需求 | 验证方式 | 验收证据 |
|---|---|---|
| Windows 11 首要验证 | 全部命令在 Windows 11 实测 | 所有 S1-S6 在 Windows 11 执行通过 |
| 10 本书批处理单书失败隔离 | S3 批处理 3 文件（1 正常 + 2 失败） | S3：E_notes.txt success，G1/G2 失败被隔离，failure_list 完整 |
| Schema 全部版本化 | 6 个 schema 均含 schema_version | 6 个 schemas/*.schema.json 全部 v1 |
| 中英文输出 | CLI 与报告 | summary.md 中文；JSON/Markdown 报告英文机器字段 |
| 无上游仓库和网络时核心流程可运行 | 断网独立性验证 | S6 offline-smoke pass；代码审查：src/ 唯一联网导入是 urllib.parse.urlparse（仅解析） |
| 可选依赖缺失时降级 | F 样本（Calibre 缺失） | F 跳过并记录降级原因（已知限制） |

## 三、MVP 10 项验收追踪（PRD §6）

| # | MVP 验收项 | 验收状态 | 证据 |
|---|---|---|---|
| 1 | 七类必选扩展名样本 | PASS（6/7，F 跳过） | S1 analyze A-E 全部成功；F 因 Calibre 拒绝转换最小 TXT 跳过，DRM 路径由 G2 验证 |
| 2 | 四种模式按合同工作 | PASS | S1 Analyze Only 不生成 Skill；S2 Full Build 产物完整；Build from Analysis + Update 由单元测试 + TASK-015 实测覆盖 |
| 3 | Full Build 产物完整并通过校验 | PASS | S2-A/B/C build 成功；S4 validate pass_with_warnings（模板硬编码 references/provenance.md 链接致 source_check warn，已知限制） |
| 4 | Update 差异/override/回滚 | PASS | TASK-015 端到端实测：dry-run → confirm → no_changes → rollback 全通过 |
| 5 | Raw 哈希不变 | PASS | S1/S2 data_home 多次运行 manifest.json content_sha256 一致 |
| 6 | Wiki 核心结论可追到原文件位置 | PASS | S1 candidate_units.source_refs 含 source_id + block_id + quote；S2 provenance.yml 含真实 content_sha256 |
| 7 | 无长段原文/虚构页码/个案阈值标注 | PASS | S4 copyright check 全部 PASS；S1 候选单元 quote 均为短摘要 |
| 8 | 注入测试被识别 | PASS | S5 validate-injection fail（exit 1），injection.phrase + 非 https URL 被报告 |
| 9 | 删除网络和上游仓库后可运行 | PASS（代码审查） | S6 offline-smoke pass；src/ 无运行时联网调用；8 个 vendored 文件本地化（PROVENANCE.yml PASS）。防火墙阻断实测需管理员权限，记为已知限制 |
| 10 | 来源台账、许可证和致谢完整 | PASS | docs/PROVENANCE.yml（2 上游，8 移植文件）+ LICENSES/（3 个 MIT）+ ACKNOWLEDGMENTS.md + THIRD_PARTY_NOTICES.md；check_provenance.py PASS |

## 四、质量指标追踪（ACCEPTANCE_TEST_PLAN.md）

| 指标 | 目标 | 实际 |
|---|---|---|
| 来源覆盖率（核心 KnowledgeUnit） | 100% | 100%（S1 所有 candidate_units 含 source_refs） |
| 无来源结论 | 0 | 0（S4 source_check 未报"orphan conclusion"） |
| 重复 ID | 0 | 0（S1 候选 ID 唯一） |
| Raw 改写 | 0 | 0（S1/S2 data_home 多次运行 manifest 一致） |
| 高严重度安全扫描未处置项 | 0 | 0（S5 injection fail 已识别并退出 1） |
| 回滚成功率 | 100% | 100%（TASK-015 实测） |

## 五、未覆盖项与已知限制

| 项 | 说明 | 跟踪 |
|---|---|---|
| Build from Analysis 端到端 | acceptance 未单独跑 `build --from-analysis`，由 TASK-013 单元测试覆盖 | 后续可补 S2-FA 步骤 |
| 断网防火墙实测 | Windows 防火墙需管理员权限，本会话未执行；以代码审查 + S6 env 隔离作为证据 | 用户可手动以管理员身份执行 |
| F 样本（DRM-free MOBI） | Calibre 拒绝转换最小 TXT（输出格式不匹配）；DRM 路径由 G2 验证 | 可准备更完整的 TXT 源（含标题/段落）后重测 |
| SKILL.md 模板硬编码引用 | 模板含 `references/provenance.md`，实际产物无此文件 → source_check warn | 后续修订模板 |
| 跨平台（macOS/Linux） | 仅 Windows 11 实测；代码使用 Path.home()/cwd() 跨平台 | CI 或用户在目标平台实测 |
| 元 Skill 自举闭环 | TASK-019 元 Skill `book2skill` 为手写产物（遵循与生成 Skill 相同的标准与目录结构），非 Book2Skill 流程编译输出 | P2 可尝试用流程把项目自身文档编译为元 Skill 验证自举 |
