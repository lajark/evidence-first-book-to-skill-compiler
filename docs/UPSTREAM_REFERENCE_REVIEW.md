# Book2Skill 上游参考复核与 v1.x 增量优化方案

- 复核日期：2026-08-06
- 输入：`项目1_Book2Skill_参考项目与增量优化建议.md`
- 适用基线：从 Book2Skill v1.0.0 增量升级到 v1.0.1；现有主链、Schema v1、SkillIR、内部质量门和扩展机制均保留
- 当前状态：M7/M8 已实施并通过发行级验证；未复制本轮参考上游源码

## 1. 决策摘要

不替换现有架构，不引入新的主干仓库。v1.x 只做四类最小增量：

1. 锁定 Agent Skills 规范并把内部、官方、第三方校验分层记录；
2. 将现有 `AnalysisBundle` 到 Schema 当前视图再到 `SkillIR` 的边界显式化，但不建立第二事实源；
3. 补充任务型 Skill 拆分建议、正负触发 fixture 和同基线版本比较；
4. 将“结构兼容、安装成功、真实宿主运行”分级，避免把安装测试写成运行验证。

优先顺序为“观察而不改产物 → 固化合同 → 增强设计建议 → 历史案例灰度”。研究型编译运行时、治理平台、AOT/JIT 和大规模平台适配暂缓。

## 2. 现有能力与建议差距

| 建议主题 | 当前项目事实 | 判断 | v1.x 动作 |
|---|---|---|---|
| Agent Skills 目录与 frontmatter | 已有 `SkillIR`、`SKILL.md` 模板、frontmatter/预算/链接检查和多宿主安装 | 部分具备；未锁定规范修订，也未保存官方校验器结果 | M7-01/02 |
| 外部 `skill-validator` | 当前为自有 Python Validator，无外部工具适配和结果来源字段 | 明确缺口 | M7-02，先观察模式 |
| `bundle.json → SkillIR` | 当前公开模型为 `AnalysisBundle`，审核后转 `KnowledgeUnit`/Schema 再构建 `SkillIR` | 语义边界存在，但阶段快照、哈希链和迁移说明不够显式 | M7-03；不直接重命名 v1 文件 |
| 历史回归与质量基线 | 已有 A–G acceptance、三书 benchmark、来源/性能指标和大量回归测试 | 已具备主干；缺规范兼容快照、反例材料和统一新旧模板比较合同 | M7-04/M8-04 |
| 一本书拆成任务型 Skill | 编写标准已有“不可做万能 Skill”和子 Skill/reference 原则；`SuggestedSkill` 字段较少 | 原则具备，数据合同和自动建议不足 | M8-01 |
| 正负触发与执行评测 | 已有质量基准、案例回归；未形成每个候选统一的正负触发 fixture | 明确缺口 | M8-02 |
| 证据分级与类型化边界 | 已有 source refs、review status、冲突、Critic/Arbiter 来源回放、输入输出合同 | 部分具备；尚无 `primary/secondary/inferred/user-added` 统一语义 | M8-03，需兼容迁移 |
| 多平台运行优化 | 已有 installer 与跨宿主内容一致性测试，但不是宿主内真实任务运行 | 不能宣称 runtime verified | M7-04；真实需求出现后再做单平台 profile |

## 3. 上游核验与采用方式

本表记录 2026-08-06 的实施核验。Agent Skills 规范已锁定到 Commit `217be548739f21d6008915c29aefe320ea1a90af`，`skills-ref` 锁定为 0.1.0，`skill-validator` 锁定为 1.5.6；均采用规范/设计参考或可选外部进程适配，没有复制其源码。

| 上游 | 官方页面核验结论 | 采用方式 | 不采用内容 |
|---|---|---|---|
| [agentskills/agentskills](https://github.com/agentskills/agentskills) | 开放规范定义 `SKILL.md`、`scripts/`、`references/`、`assets/` 和渐进式加载；仓库提供 `skills-ref` 参考校验器；页面说明代码 Apache-2.0、文档 CC-BY-4.0 | 规范基线 + 官方参考校验；实施时锁定修订 | 不把目录结构当内部语义模型，不追随浮动 main |
| [anthropics/skills/skill-creator](https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md) | 强调真实测试提示、客观断言、定性/定量评估、触发描述和迭代比较 | 设计与评测方法参考 | 不复制平台专属假设；目录许可逐项核验 |
| [agent-ecosystem/skill-validator](https://github.com/agent-ecosystem/skill-validator) | MIT CLI；提供结构、内部/外部链接、Token、内容、污染、JSON/Markdown 输出及可选 LLM 评分；严格模式可把 warning 当失败 | 锁版本外部进程适配，先只观察；规则结果与 LLM 评分分离 | 不重写或复制整套实现；外链联网检查默认非阻断 |
| [virgiliojr94/book-to-skill](https://github.com/virgiliojr94/book-to-skill) | MIT；当前支持多源/多格式、按需章节、隐私与版权边界 | 继续按既有 provenance 流程做适配器差距审查 | 不替换解析主干，不重复移植已具备能力 |
| [apple-ouyang/book-to-skill](https://github.com/apple-ouyang/book-to-skill) | MIT；明确“一项 Skill 解决一个具体问题”、真实案例和可执行步骤 | Skill 任务边界、主/子路由和拆分建议参考 | 不复制书籍衍生 Skill 内容 |
| [qomob/SkillCompiler](https://github.com/qomob/SkillCompiler) | MIT Meta Skill；公开流程包含 Triage、多 Pass、Skill IR、压力测试和诚实边界 | IR、阶段产物和 advisory 设计参考 | 不作为代码底座，不引入其平台 profile 体系 |
| [generative-computing/mellea-skills-compiler](https://github.com/generative-computing/mellea-skills-compiler) | Apache-2.0；IBM Research 2026-05 research preview，页面明确 API/CLI/产物可能变化且当前编译后端有限 | 仅参考类型 Schema、fixture、矛盾显式化和审计思想 | 不作为 v1.x 运行依赖，不建设认证治理平台 |
| [SJTU-IPADS/SkVM](https://github.com/SJTU-IPADS/SkVM) | MIT；提供 Profiling、AOT、JIT 和跨模型/harness benchmark | 远期兼容矩阵和 runtime benchmark 观察项 | 不引入运行时、子模块、二进制下载或 AOT/JIT 依赖 |

### 本次本地参考项目：T-Stocks

本次 Windows 桌面增量参考了用户提供的 `T-Stocks` 本地项目：采用
`pywebview + localhost HTTP/SSE` 的本地 WebGUI 形态，并借鉴
`PyInstaller onedir + Inno Setup` 的 Windows 构建链。Book2Skill 只吸收交互与
打包思路，不复制其业务代码；任务仍由 Book2Skill 现有 Application Use Case
执行，并保留本地令牌、脱敏、许可证和可选依赖审查边界。macOS 桌面构建不在本次范围内。

## 4. 目标边界

### 4.1 校验分层

```text
Book2Skill internal checks       # 来源、安全、版权、IR、原子发布硬门
    ↓
Agent Skills skills-ref          # 锁定规范的一致性校验
    ↓
skill-validator (optional)       # 结构/链接/Token/内容/污染补充
    ↓
runtime fixtures (separate)      # 真实宿主任务运行证据
```

外部工具不能覆盖内部硬门。工具缺失、离线、超时或版本不匹配必须显式为 `not_run` 或失败；项目官方发布 profile 与普通用户草稿 profile 可采用不同严格度。

### 4.2 编译阶段

```text
AnalysisBundle (模型候选，可人工审核)
    ↓ deterministic normalize
NormalizedBundle (Schema 当前视图的可重建快照/清单)
    ↓ deterministic IR build
SkillIR (宿主无关)
    ↓ renderer + overlay
Skill package
    ↓ layered validation
QualityReport + CompatibilityReport
```

`NormalizedBundle` 不复制 Raw 正文，不替代 `workspace/schema/`，也不成为新的真源。落盘文件为 `normalized-bundle.json`，合同见 `schemas/normalized-bundle.schema.json` 与 ADR-002；现阶段未提升已发布 Schema 主版本。

## 5. 分阶段优化方案（v1.0.1 已完成）

### 阶段 A：规范差距与观察模式（M7-01/02）

- 锁定 Agent Skills 规范修订、`skills-ref` 和可选 `skill-validator` 版本；
- 建立 `portable-draft`、`portable-release` 两个 validation profile；
- 对现有 v1.0 产物生成兼容报告，不改变任何生成文件；
- 汇总工具版本、命令、退出码、JSON 结果、耗时和 `not_run` 原因；
- 退出条件：本地与 CI 对固定 fixture 产生一致离线结构结果，观察模式产物哈希不变。

### 阶段 B：确定性边界与迁移（M7-03）

- 先写 ADR，确认 `NormalizedBundle` 与 Schema 当前视图的关系；
- 定义输入哈希、上游 artifact ref、generator/template/schema version 和规范化规则；
- 为 v1 `AnalysisBundle` 提供迁移、往返、失败恢复和回滚测试；
- 退出条件：固定输入跨进程稳定、悬空来源/重复 ID/未审核必审项 fail-closed、无第二事实源。

### 阶段 C：回归基线与兼容矩阵（M7-04）

- 复用现有三书与 A–G 数据，补一个结构清晰短书和一个不适合转换的反例；
- 冻结 v1.0 产物、校验结果、Token、耗时和来源覆盖快照；
- 将宿主状态拆为 `structure_validated / install_smoke_passed / runtime_verified`；
- 退出条件：所有历史样本有可比较基线，未执行真实运行的宿主不被误标。

### 阶段 D：任务型设计与 fixture（M8-01/02/03）

- 扩充 Skill 建议合同：任务边界、重叠、路由词、正负触发例、案例引用和拆分理由；
- 拆分只生成 warning/advisory，不自动改写已审核内容；
- 建立正向触发、相邻负向、正常执行、缺输入、冲突、越界和输出格式 fixture；
- 评估证据分级字段的兼容迁移，不因追求覆盖率生成无来源内容；
- 退出条件：建议可审计、fixture 可机器运行、核心规则不由无来源推断单独支撑。

### 阶段 E：同基线 A/B 与灰度（M8-04）

- 新旧模板使用完全相同的输入、模型响应或冻结 Bundle、fixture 和校验器版本；
- 比较来源覆盖、结构校验、触发、执行断言、Token、耗时与错误恢复；
- 只有关键指标不回退时才提升默认模板；保留旧模板与迁移回滚；
- 退出条件：变更收益有证据、差异可解释、回滚演练通过。

## 6. 明确暂缓

- SkVM/Mellea 运行时或认证管线；
- 跨模型 AOT/JIT、通用知识图谱、自建向量数据库；
- 插件市场、远程扩展中心、大量平台自动发布；
- 自建 OCR、复杂 PDF 布局引擎；
- 无人工审核的全自动知识固化；
- 未经单独确认的 Schema 主版本升级或自动 Skill 拆分。

## 7. 文档交付顺序

本轮先建立复核方案，再按实施结果补齐合同、兼容矩阵、迁移回滚、质量门与回归文档：

1. M7-01：`docs/COMPATIBILITY_MATRIX.md`、`docs/QUALITY_GATES.md`；
2. M7-03：ADR、`docs/BUNDLE_SCHEMA_GUIDE.md`、`docs/SKILL_IR_CONTRACT.md`、`docs/MIGRATION_GUIDE.md`；
3. M7-04：`docs/REGRESSION_BENCHMARK.md`；
4. M8：fixture 合同和模板 A/B 报告。

## 8. 风险与回滚

- 规范和校验器持续变化：使用版本/Commit 锁定，报告携带工具身份，升级单独评估；
- 多校验器规则冲突：保存原始结果和映射，不把第三方 warning 自动升级为内部来源硬门；
- 新产物重复事实：`NormalizedBundle` 仅从 Schema 当前视图确定性重建，Schema 仍是权威；
- Schema 兼容风险：v1 只读基线 + v2 迁移/往返/回滚，未确认前不改主版本；
- 评测过拟合：保留反例和相邻负向任务，模板必须在冻结历史集上比较；
- 外部依赖破坏离线能力：外部 CLI 为可选适配，发布 CI 锁定并预装，核心内部校验不依赖网络。
