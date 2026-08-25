# 生成 Skill 产品完整性复核报告

- 任务编号：B2S-M13-00
- 复核日期：2026-08-25
- 范围：产品定义、生成物闭包、多书/多任务增量编译、发布与宿主运行边界
- 结论性质：代码与现有产物的只读审计；本轮未修改业务代码、Schema、模板、构建脚本或已有 Skill

## 1. 结论

当前 Book2Skill 已是可用的**编译与治理 Core**，并具备 Raw 固化、来源哈希、
AnalysisBundle、NormalizedBundle、SkillIR、目录生成、质量门、原子发布、回滚和宿主
目录复制能力；但当前生成物还不能统一称为“完整可独立运行的 Generated Skill
Product”。

最准确的产品判断是：

1. 当前生成 Skill 的 Markdown/reference 文件**静态上不 import Book2Skill Core**；
   宿主安装器复制整个 Skill 目录，也不会在生成 Skill 中注入 Core import。
2. 当前没有正式的逐 Skill Standalone 发布 profile，也没有 Runtime Closure manifest；
   因而无法证明每个已安装 Skill 在隐藏 Core、项目源码、Raw、原书和中央 Schema 后
   仍能完成其声明任务。
3. 抽查的真实生成物 `m6-acceptance-c` 不含绝对路径或 Core import，方法正文位于包内
   `references/techniques.md`；但其输入合同仍要求“合法持有的源文档或已验证的
   AnalysisBundle”，且来源定位所需的 ExtractionMap 只存在中央 Raw 目录。它是可复制的
   Skill 目录，不是已证明的运行闭包。
4. Core ZIP 是 **Book2Skill Core/Extension 上游包**，不是逐生成 Skill 产品包。实际
   `book2skill-core-1.0.4.zip` 只包含 Wheel、Book2Skill 元 Skill、contracts、SDK 和样例
   Extension，没有任何业务 Generated Skill。
5. 当前增量机制是“同一 `collection_id` / `SkillSpec.name` 下合并 KnowledgeUnit，随后
   重编并替换整棵 Skill 树”。它能保留历史、override、冲突和回滚，但不是以稳定任务合同
   为身份、只升级 Asset Pack 的增量资产治理。
6. 一书多任务目前只停留在 `suggested_skills` advisory。Build 仍接收一个人工提供的
   `SkillSpec`，并把 Bundle 中全部可用 KnowledgeUnit 编成一个 Skill；没有候选任务到多个
   Kernel/Pack 的确定性 fan-out。

因此，目标产品应采用：

```text
CompleteUsableSkill
= SkillKernel
+ pinned AssetPacks
+ Schemas
+ ToolCapabilities
+ RuntimeClosureManifest
```

其中 Standalone Generated Skill 的 Core 仅是构建期依赖；Extension 可在 manifest 明示
Core/SDK 运行期依赖。二者不得混用。

## 2. 当前真实产品形态与证据

### 2.1 编译链

当前代码链为：

```text
Source → Raw/ExtractionMap → AnalysisBundle → NormalizedBundle
→ KnowledgeUnit → SkillIR → Skill directory → Validation/Publication
→ Host directory copy
```

关键证据：

- `src/book2skill/application/build.py` 的 `_compile_bundle` 对整个 Bundle 统一执行
  normalize、IR build、references/wiki render 和 Skill tree write；Build 接收单一
  `SkillSpec`。
- `src/book2skill/compiler/ir_builder.py` 的 `SkillSpec` 只有 name、description、usage、
  required inputs 和 outputs；`SkillIR` 没有 task contract、Kernel、Pack、Closure 或工具能力
  字段。
- `src/book2skill/compiler/skill_writer.py` 写入 `SKILL.md`、references、空 `assets/`、Wiki、
  provenance 和质量报告；没有逐资源角色/版本/兼容范围 manifest。
- `src/book2skill/application/artifacts.py` 的 `CompilationArtifact` 能固定文件哈希、unit/source
  ID，但不能表达哪些资源是 exact-required、semantic-retrieval、optional，也不固定
  Kernel/Pack/Schema/Tool 版本。
- `src/book2skill/hosts/base.py` 的 `install` 只解析 SKILL.md name、复制目录并应用 overlay；
  不验证 `compilation-artifact.json`，也不检查 published/review 状态、Closure 或能力依赖。

### 2.2 一个真实生成 Skill 的闭包追踪

选取现有合成 EPUB 验收产物：

`workspace/acceptance/runs/20260803T105644/skills/skill-C/`

| 阶段 | 证据 | 观察 |
|---|---|---|
| 电子书 | `samples/C_book.epub` | 合成双章节 EPUB，无版权风险 |
| Source/Raw | `data/raw/727958daab37/1/manifest.json` | 固定 source ID、SHA-256、格式、抽取器和权利确认 |
| ExtractionMap | `data/raw/727958daab37/1/extraction-map.jsonl` | 两个 block 可定位到 chapter 1/2，但文件留在中央 Raw |
| Schema | `data/schema/col-712a8cf7fc2b/units.jsonl` | 两个 candidate technique，带 source/block ref |
| SkillIR/Compiler | `src/book2skill/compiler/ir_builder.py`、`skill_writer.py` | 单一 SkillSpec 将全部可用单元编成一个 Skill |
| Skill 目录 | `skills/skill-C/` | SKILL、references、wiki、provenance、质量报告和 artifact |
| 发布/安装 | `src/book2skill/application/publisher.py`、`hosts/base.py` | 发布会重编整树；安装会复制整树 |
| 运行资源 | `SKILL.md` + `references/techniques.md` | 方法文本在包内；没有脚本或 Core import |

该目录的 `compilation-artifact.json` 所列文件经只读 SHA-256 复核，未发现 hash mismatch。
静态扫描只发现 SKILL 输入合同提到 source document / AnalysisBundle，未发现绝对路径、
`import book2skill` 或 `from book2skill`。

但该闭包仍有以下限制：

- `references/provenance.md` 只保留 `source_id / block_id`，章定位存在中央
  `extraction-map.jsonl`，安装目录自身无法把 block ID 解析成 chapter/page/paragraph；
- `provenance.yml` 有源文件 hash，但没有每条知识到 locator 的完整闭包；
- `assets/` 为空，没有输出模板；没有 I/O Schema、工具能力清单或 Closure manifest；
- 产物是 2026-08-03 历史验收结果，早于当前 Build 写入 NormalizedBundle、设计评审和
  compatibility 报告的代码；不能拿它证明 1.0.4 的完整隔离运行；
- 未在隐藏项目源码、Core、原书、Raw 和 Schema 的环境中执行真实任务。

### 2.3 现有 1.0.4 Core 发布包

只读检查 `dist/release-v1.0.4-gitee/book2skill-core-1.0.4.zip`：

- SHA-256 为
  `7a346771021e2300989c989f8d75b590764a5a92b19139320e5945d6f8c08f41`，与同目录
  `checksums.sha256` 一致；
- 包含 `dist/book2skill-1.0.4-*.whl`、`skills/book2skill-skill.zip`、contracts、SDK、
  sample-extension 和安装脚本；
- 不包含 Generated Skill、Asset Pack、Runtime Closure 或逐 Skill release manifest；
- `release-manifest.json` 描述的是 `product=book2skill-core`，没有把 Core 包验收等价为任一
  Generated Skill 的运行验收。

## 3. 已满足能力与缺口

| 级别 | 判断 | 证据/影响 |
|---|---|---|
| 已满足 | Core 构建链可追溯且有确定性边界 | Raw/ExtractionMap、NormalizedBundle、CompilationArtifact |
| 已满足 | 候选、review queue、开放冲突、approved-only 发布门、原子回滚已存在 | Analyze/Update/Publisher |
| 已满足 | 生成目录主要使用相对路径，宿主复制整树 | SkillWriter、HostInstaller |
| P0 | 无稳定 `task_contract_id`，Skill 身份实际由 name + collection 驱动 | 同任务跨书无法可靠识别；书名/人工 slug 容易形成重复或误合并 |
| P0 | 无 Kernel/AssetPack 独立合同与版本 | 新知识会重编 SKILL.md；无法证明 Kernel 不变的 Pack 升级 |
| P0 | 无 Runtime Closure manifest 和运行前 hash/兼容检查 | 安装成功不等于任务成功；运行中无法冻结实际资源集合 |
| P0 | 无任务 fan-out 编译 | 一本书的多个任务建议不会自动形成多个隔离候选产品 |
| P1 | 无逐 Generated Skill Standalone 发布 profile | Core ZIP 与生成 Skill 目录被混为不同层级的“发布”概念 |
| P1 | 来源 locator 不在已安装闭包内 | 原书/Raw 不可见时，输出只能追到 block ID，不能继续追到书籍位置 |
| P1 | `assets/` 只是空目录；`SkillIR.assets` 未由 Builder 填充 | 模板、公式和固定结构可能只存在知识正文或中央数据中 |
| P1 | 无 ToolCapabilities 声明和安装前 preflight | 必需宿主工具缺失时不能统一 fail-closed 或声明降级 |
| P1 | Install 不检查 published/review/closure 状态 | 候选 draft 可被直接复制到宿主，绕过 production 发布门 |
| P1 | exact-required / semantic / optional 未分类 | 承重规则可能依赖模型模糊读取，缺失时没有稳定错误合同 |
| P2 | 冲突识别以内容启发式为主，scope 模型不足 | 尚不能按行业、阶段、法域、作者版本并存并路由 |

## 4. 产品定义

### Book2Skill Core

电子书发现、合法性门、抽取、Raw/Schema、任务候选、资产候选、SkillIR、编译、验证、
宿主适配和发布生命周期工具。对 Standalone Generated Skill 是构建期依赖；对 Extension
可按 manifest 成为显式运行期依赖。

### Generated Skill Product

围绕一个稳定任务合同交付、可安装、可执行、可追溯和可回滚的产品。它不是一个
SKILL.md 文件，也不是 Core ZIP。产品可物理拆分，但必须由 Closure manifest 固定所有
运行资源。

### Stable SkillKernel

承载低频变化的任务身份、触发/不适用边界、I/O 合同、核心分析顺序、证据纪律、Pack
选择规则、冲突/缺口/降级、失败恢复、人工确认和工具缺失行为。Kernel 必须保持精简且
可执行，不能退化为“阅读知识库后完成任务”。详细领域知识下沉 references/Asset Packs，
输出模板进入 assets，重复且确定性要求高的操作进入 scripts 或明确 Tool Contract。

### AssetPack

围绕同一 task contract 的可独立审核、版本化和回滚内容集合，包含方法、原则、检查项、
模板、案例、反例、参数、适用范围、冲突关系、来源定位和审核状态。普通新书知识优先形成
新 Pack 版本，不改 Kernel。

### Runtime Closure

一次运行允许使用的 Kernel、Pack、Schema、工具和宿主能力的不可变集合。Closure manifest
必须使用相对路径或稳定资源 ID，固定版本、兼容范围和 SHA-256；运行开始后不得因中央知识库
更新静默切换 Pack。

### Host/Harness

提供项目状态、文件访问、工具执行、调度、恢复、审计和用户确认的运行环境。宿主能力不是
Generated Skill 的隐形内容资产；必需、可选和不支持能力必须由 Closure/Capability 合同说明。

### Extension 与 Standalone

- **Standalone**：Closure 自带完成声明任务所需的 Kernel、Pack、Schema 和 bundled scripts；
  可使用 manifest 声明的通用宿主能力，但不得要求安装 Book2Skill Core、访问项目源码、Raw、
  原书或中央 Bundle。
- **Extension-backed**：可通过 extension manifest 显式依赖 Core/SDK 或其他 Extension；安装器
  必须验证版本和权限。此依赖不得外推到所有 Generated Skill。

## 5. 任务中心的多书增量编译方案

### 5.1 任务身份

引入不可由书名/source ID 推导的稳定 `task_contract_id`，例如
`task:book2skill:<semantic-slug>`，并独立维护 `task_contract_version`。语义相似度只产生
“可能同任务”的候选；是否绑定既有任务必须由 I/O、前置条件、不变量、安全和失败语义的
兼容检查加人工确认决定。

判定规则：

1. I/O、核心目标和安全语义兼容：路由到既有 task identity，产生 candidate assets；
2. 仅方法、模板、案例或适用范围增加：升级 Asset Pack，Kernel 不变；
3. 核心框架向后兼容扩展：评估 Kernel minor，但不得因单一内容项自动升级；
4. 输入输出语义、安全边界、资源解析协议或失败恢复不兼容：新 task identity 或 Kernel major；
5. 真正不同的任务不得为了减少 Skill 数量强行合并。

### 5.2 治理状态机

```text
source knowledge
  → candidate asset
  → semantic dedupe + scope/conflict analysis
  → human review
  → approved candidate set
  → merge into staged AssetPack
  → regression against pinned Kernel + prior Pack
  → publish Pack
  → build immutable Runtime Closure
```

- candidate 与 production 使用不同存储/指针，未审核资产不能进入 production Pack；
- 语义去重合并内容实体但保留所有 source refs；
- 作者冲突不投票、不覆盖，按来源、阶段、行业、法域和前置条件并存；无法判定时阻塞发布；
- Pack 发布使用并列版本、active pointer、快照和回滚；旧 Closure 始终可复现；
- Kernel 和 Pack 分别做回归。Pack 更新不得无意改变触发、I/O、安全和失败边界。

### 5.3 当前编译器分类

当前不是严格的“每本书固定一个 Skill”，因为 Full Build 可以接收多源；也不是已实现的
任务中心增量治理。准确分类是：

> 每次 Build 由调用者提供一个 Skill name/spec，将该 Bundle/collection 的全部可用内容编译
> 为一棵 Skill；Update 在同一 collection 上 fold-in 后重编并替换整棵 Skill。

因此它同时存在“一书一人工 Skill”“多书堆入一个 Skill”和“更新覆盖整棵派生树”的可能，
是否合理依赖调用者选择，尚无 task contract 与 Pack 治理硬约束。

## 6. 资源加载与能力合同

Closure 中每项资源必须属于以下一种：

1. `exact_required`：Schema、公式、固定模板、blocker、安全规则、承重脚本。按 resource ID、
   version、relative path 和 SHA-256 精确加载；缺失、损坏或不兼容时 fail-closed。
2. `semantic_retrieval`：方法解释、案例、参考问题、扩展知识。必须有完整索引、来源覆盖和
   检索边界；召回失败不得伪造。
3. `optional`：非承重扩展。缺失时输出明确降级标识和受影响能力。

Tool Capability 至少区分 `bundled_script`、`host_required`、`host_optional` 和
`unsupported`，并记录 capability ID/version、I/O Schema、权限、超时、稳定错误和降级策略。

## 7. 版本与升级

| 字段 | 语义 |
|---|---|
| `skill_kernel_version` | Kernel SemVer；patch 不改行为，minor 向后兼容增加，major 改任务/I-O/安全语义 |
| `task_contract_version` | 稳定任务合同版本；不兼容时 major 或新 task identity |
| `asset_pack_id` | 同一任务下独立资产集合身份 |
| `asset_pack_version` | Pack 内容/范围版本；普通新书更新优先只提升此版本 |
| `schema_version` | 各机器合同版本，带迁移与往返要求 |
| `tool_capability_version` | 工具 I/O、权限和错误语义版本 |
| `runtime_closure_version` | Closure manifest 结构/决策版本 |
| `runtime_closure_hash` | 规范 JSON 与全部 pinned 资源 hash 计算的不可变运行身份 |

每次任务输出必须记录实际 `runtime_closure_hash`。同一 Kernel 可绑定两个兼容 Pack 版本；
核心流程、输出合同和安全边界应保持一致。

## 8. 未验证项与残余风险

- 本轮授权禁止重建、安装和部署，因此未生成 1.0.4 新 Skill，也未执行完整隔离运行；
- 未隐藏项目源码/Core/原书/Raw 后调用真实宿主完成业务任务；
- 未验证 Claude、TRAE、ChatGPT 的真实任务执行；既有兼容矩阵仅 Codex 有一项历史宿主证据，
  且不等同于本次抽查 Skill 的隔离运行；
- 未验证必需工具缺失的运行时 fail-closed，因为当前没有 ToolCapabilities 合同；
- 未验证 Pack 独立升级、同 Kernel 双 Pack 或 candidate/production 隔离，因为当前模型不存在；
- 历史 `m6-acceptance-c` 是合成小样本，不能代表复杂方法书的内容充分性；
- 当前代码和现有文档存在版本叙述滞后，后续实现必须以新 ADR/PRD 合同为目标，不能把本报告
  写成“代码已完成”。

下一轮必须先做一个不触及全量迁移的最小纵向 Closure Spike；在 Closure 和 task contract
合同关闭关键设计问题前，不开始批量改写生成 Skill。

## 2026-08-25 后续生产检查点

上文记录的是 M13-00 只读基线，保留其历史未验证结论。随后用户已授权并完成 M13-16～20：Build/
Publisher 默认 Runtime Product 合同、带批准和快照回滚的四个真实 legacy Skill 迁移、默认 Host
Installer/CLI production preflight，以及四个迁移 Skill 在 Claude/Trae/Codex/Project/ChatGPT
项目目录的 20 项隔离安装与 Closure task harness。该 harness 复算 descriptor、Closure 和全部资源
哈希；它不等同于启动第三方客户端或评价模型对话质量。当前生产迁移快照位于授权部署备份的
`backup\migration-20260825`，可按每个 Skill 目录回滚。

同日使用项目 `.venv` 执行全量 pytest，结果为 `1150 passed, 5 skipped, 1 warning`；M13-20 Pack Store 定向
`18 passed`；Ruff、mypy（118 个
源文件）、来源一致性、Schema JSON 与 diff-check 均通过。桌面包已用
`scripts/build_windows.ps1` 重建，并完成直接启动 5 秒冒烟。该构建仅写入仓库 `dist/`，
随后用 Inno Setup 生成 `Book2Skill-Setup-1.0.4-windows-x64.exe`，在隔离临时根完成安装、升级、启动
5 秒冒烟和卸载（均返回 0）。构建与验证未覆盖授权部署实例；release-manifest 保持
`release_ready=false`（私用许可证、未签名），未执行发布。

生成物完整性闸门接入后重新构建桌面包；最新安装器在隔离临时根完成安装、升级、5 秒启动冒烟和卸载，
均返回 0。安装器 SHA-256 为 `9cb0031a164737d38dc3264585deffe7c9081d399a995dcc37fd318279e19fba`；
该证据只说明最终载体安装链通过，不改变私用许可证/未签名导致的 `release_ready=false`。

M13-20 进一步补齐 `PersistentPackStore`：candidate、不可变 release、active pointer 与 rollback 均可
跨进程回放，stale base 和 release 漂移在切换前 fail-closed；该持久化接缝尚未接入现有 Update/Publisher
的整树重编路径，因此不能把它写成完整的生产多书 Pack-only 增量发布。

M13-21 按参考规则 v3.1.1 新增生成物内容完整性闸门：`content-integrity.json` 对 normalized active
units、references 章节/内容、source refs、provenance source/hash 和必要文件逐项对账；Build/Publisher
在 Runtime Product 发出后再生成报告与 `compilation-artifact.json`，确保最终目录全部纳入文件清单。
删除、截断、替换、重排、重复及来源哈希篡改均有负向控制并 fail-closed。framework/principle 现在也
生成详细 references，避免 workflow 摘要掩盖全文缺失。最终目录/载体可用
`scripts/check_generated_skill_integrity.py` 独立重开验证；未提供权威 SourceManifest 时只记为
internal-only，不升级为完整来源通过。

M13-22 新增显式 Pack-only Update 生产桥接：`UpdateUseCase.publish_pack_incremental` 接收已带来源
证据的多书 `AssetCandidate`，固定执行 candidate→review→approved→多书治理→持久化 release/active
切换；该路径只改变 Pack 指针，不重建或覆盖 Skill/Kernel 树。新增/重复资产、冲突和 stale-base
回归均已覆盖，失败在 active 切换前保持旧版本可读。普通 `UpdateUseCase.execute` 仍是整树兼容路径，
真实电子书来源回放、Closure 资源重绑和默认路径切换仍未完成，因此本项不等同于完整真实多书宿主任务
证据。

M13-22 收口验证使用项目 `.venv` 完成相关回归 `63 passed`，全量 pytest `1155 passed, 5 skipped, 1 warning`，
Ruff、mypy（120 个源文件）、来源一致性和 `git diff --check` 均通过。应用层变更进入 Windows 桌面包后，
安装器在隔离临时根完成安装、升级、启动 5 秒冒烟和卸载（均返回 0）；该载体 SHA-256 为
`97f3bee0d26594084f23205e96d0b947e0098b645872c0a1243a7f2e6fe6d36e`。`release_ready=false` 仍由私用许可证
和未签名状态决定，不应被安装器冒烟结果覆盖。

## P0 真实来源与最终载体复核（2026-08-25）

用户确认 `input/` 中三本电子书已获授权，本轮据此执行真实回放：`孙子兵法.mobi`、
`番茄工作法图解.epub`、`微习惯.epub` 的 source IDs 为 `1751a97bee9a`、`3a1df6b9c2c5`、
`64a7b310958f`。Analysis 产出 40 个 candidate units，投影为 40 个带 block locator 和权威
source hash 的 Pack assets，按来源计数 8/16/16。

生产 Pack/Closure 路径实际执行 `1.0.0 → 1.1.0 → 1.2.0`：每次升级都写入不可变 release、
重绑 `assets/pack-release.json` 并重算 Closure/descriptor hash；回归函数验证 Kernel hash
保持 `bb8d004012aec6b45b18a5cbe15e29a1aedd7111838ce408775a6a81ea3ed137`。随后执行
`1.2.0 → 1.1.0` rollback，active pointer、Pack resource 和 Closure 的 `asset_pack_hash`
一致。Pack/Closure 绑定失败的补偿回滚由 `PackIncrementalPublisher` 覆盖。

真实 `微习惯.epub` 生成的最终 Skill 包含 16 个 unit，production 目录和复制后的
`installed-copy` 均用 `scripts/check_generated_skill_integrity.py --source-manifests` 独立重开：
artifact、内容报告、权威 source hash 全部通过，`blocked=false`。Pack resource 作为独立载体
保留其实际来源范围；Skill 自身的完整性报告只核对 Skill 所编译的基础来源，不把 Pack-only
新增书籍错误宣称为 Skill 正文来源。

本轮修复了两个真实回放中发现的生成器问题：CRLF 嵌入在 Windows 写入时导致正文 hash 漂移，
现统一为 LF；workflow description 不再复制整本书，而使用有界摘要，全文仍在 references，
从而同时满足默认预算和内容完整性门。

最终验证为定向 pytest `102 passed`、全量 pytest `1163 passed, 5 skipped, 1 warning`；Ruff、
mypy（121 个源文件）、来源一致性、`git diff --check` 与 `python -m build --no-isolation` 均通过。
用户明确排除的公开桌面发布、第三方客户端模型质量、大文档/真实 LLM/语义索引等可选项未执行。
