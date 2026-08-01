# Book2Skill 数据模型

## 1. 核心实体

- `SourceManifest`：来源、哈希、格式、版权确认、抽取器、版本、保密等级。
- `ExtractionMapEntry`：normalized block 与页码/章节/段落/表格定位。
- `KnowledgeUnit`：framework/principle/technique/anti_pattern/term/case/checklist 等。
- `ConflictRecord`：冲突单元、双方来源、适用前提、状态。
- `ReviewItem`：审核对象、原因、严重度、处置和审核者。
- `SkillIR`：name、description、usage boundary、workflow、references、assets、knowledge refs。
- `BuildRun`：输入版本、模型/参数、工具版本、输出哈希、检查结果。

## 2. 状态

- 抽取：`pending | extracted | partial | failed`；
- 知识：`candidate | reviewed | approved | rejected | superseded`；
- 冲突：`open | resolved_by_scope | resolved_by_user | preserved`；
- 发布：`draft | validated | approved | published | rolled_back`。

## 3. ID 与版本

- `source_id = sha256(content)` 的稳定前缀 + 类型；
- 语义 ID 不依赖文件名；
- 每条记录包含 `schema_version`、`record_version`、`created_at`、`supersedes`；
- 任何更正创建新记录，不原地擦除历史。
