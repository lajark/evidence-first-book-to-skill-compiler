# Book2Skill 架构设计

## 1. 架构风格

采用 Ports & Adapters + 编译器管线：输入适配器把异构文档转换为统一 Raw/ExtractionMap；Schema/Skill IR 保存稳定语义；宿主 Adapter 只处理安装路径、frontmatter 扩展和调用方式。

```text
CLI / Meta Skill / Windows WebGUI
      ↓
Presentation Adapters (Typer / localhost API / pywebview)
      ↓
Application Use Cases
      ↓
Domain + SkillIR + Ports
      ↓
Extractors | Storage | LLM | Validators | Host Adapters
```

## 2. 目标目录

```text
book2skill/
├── src/book2skill/
│   ├── domain/
│   ├── application/
│   ├── extractors/
│   ├── storage/
│   ├── llm/
│   ├── compiler/
│   ├── validation/
│   ├── hosts/
│   ├── desktop/              # optional Windows WebGUI; no business rules
│   └── cli.py
├── skills/book2skill/
│   ├── SKILL.md
│   ├── scripts/
│   ├── references/
│   └── assets/
├── schemas/
├── templates/
├── tests/
├── docs/
└── workspace/  # 默认 Git ignore，可由 BOOK2SKILL_DATA_HOME 指向仓库外
```

## 3. 编译阶段

1. Discover：解析文件、目录、glob；
2. Gate：合法性确认、类型、大小、加密/DRM/安全检查；
3. Ingest：复制或引用原件，生成 manifest；
4. Extract：输出 normalized Markdown + extraction map；
5. Analyze：结构、方法、术语、冲突和 review queue；
6. Normalize：生成版本化 Schema/SkillIR；
7. Compile：生成标准 Skill 与宿主 overlay；
8. Validate：结构、来源、预算、注入、版权和链接；
9. Publish：临时目录原子替换、索引、日志、快照。

## 4. 关键设计决策

- `SkillIR` 是唯一跨宿主语义源；不为 Claude/TRAE/Codex 各维护一套知识正文。
- 人工修改保存为结构化 override/patch，与生成内容分离；更新时三方合并。
- 原始文件不修改；“删除”只标记来源不可用，不擦除历史。
- SQLite 不是 P0 必需；所有状态可由版本化文件重建。
- 模型仅产生候选结构，验证器和人工审核决定是否发布。
- Windows WebGUI 只能通过 Application Use Case 工作；其 localhost API 只负责
  会话、任务状态、进度和脱敏结果，不形成第二套领域模型。
- 桌面程序目录与用户数据目录分离；安装器不触碰 Raw 原件、工作区历史或凭据。

## 5. Generated Skill Product 目标架构

三层运行边界：

```text
Book2Skill Core（构建/治理）
  └─ Task discovery → candidate assets → review/merge/regression/publish
       └─ Generated Skill Product
            ├─ kernel/SKILL.md
            ├─ packs/<asset_pack_id>/<version>/
            ├─ schemas/
            ├─ scripts/                 # only bundled deterministic tools
            └─ runtime-closure.json
                 ↓
              Host/Harness（发现、文件、工具、调度、恢复、审计）
```

完整产品不以物理目录是否合并判断，而以 `runtime-closure.json` 是否固定并验证全部运行资源
判断。Standalone profile 的闭包不得运行时访问 Core、源码仓库、Raw、原书、中央 Schema/
Bundle 或绝对路径；Extension-backed profile 通过 manifest 明示 Core/SDK 兼容范围与权限。

SkillKernel 只保留稳定工作流和资源路由；详细方法进入 Pack references，输出模板进入 Pack
assets，确定性操作进入 scripts/Tool Contract。该分层遵循渐进式披露：Kernel 可发现每个
资源及其适用范围，承重资源不得成为隐形环境依赖。

## 6. 任务中心的编译与发布

目标阶段链为：

```text
Source/Raw/ExtractionMap
→ KnowledgeUnit
→ TaskCandidate(task_contract candidate)
→ CandidateAsset
→ review + semantic dedupe + scope/conflict preservation
→ Approved AssetPack
→ SkillKernel binding
→ Runtime Closure
→ closure/quality/capability validation
→ package/install/runtime evidence
```

- `task_contract_id` 是跨书稳定身份；collection 只是来源/处理集合，不再兼任 Skill 身份；
- 一个来源可路由到多个任务，一个任务可吸收多个来源；
- 同任务兼容内容只升级 Pack；Kernel 仅因任务合同、核心框架、I/O、安全、资源协议、失败恢复
  或工具边界改变而升级；
- candidate 与 production 使用分离的存储和 active pointer；发布沿用 staging、原子替换、快照
  和回滚不变量；
- 冲突资产保留双方来源和 scope。未裁决冲突可以留在 candidate；只有 Kernel 明确定义安全的
  并存/路由行为时才可进入 production。

## 7. Closure、资源与版本

Closure manifest 至少固定：

- `task_contract_id` / `task_contract_version`；
- `skill_kernel_id` / `skill_kernel_version` / hash；
- 每个 `asset_pack_id` / `asset_pack_version` / hash；
- Schema ID/version/hash；
- Tool Capability ID/version/provider/requiredness；
- target host/profile、兼容范围、Closure version/hash。

资源加载类型：`exact_required` 精确读取并 fail-closed；`semantic_retrieval` 使用完整索引和来源
覆盖；`optional` 缺失时显式降级。运行启动时冻结 Closure；active pointer 的后续变化只影响
下一次运行。

## 8. 当前实现到目标架构的迁移边界

当前实现仍是 `SkillSpec.name + collection_id` 驱动的整树编译/更新，`suggested_skills` 仅为
advisory；`CompilationArtifact` 固定文件哈希但不是 Runtime Closure。现有 v1 Skill tree 作为
`legacy-unclosed` 保持可读，不自动宣称 Standalone。

迁移先用一个无版权 fixture 建立单 Kernel/单 Pack/单 Closure 纵向样板，再冻结合同；在隔离
闭包和 task identity 设计通过前，不批量迁移现有 Skill，也不修改已发布 Schema 主版本。

M13-06 首先补充了只读 Legacy 审计和临时宿主回归：缺 Closure 的 v1 产物保持 `legacy-unclosed`，
只有 production Closure 通过验证才可进入 `closure-ready`。该阶段审计不自动写入；随后经用户授权，
M13-17 已由 `LegacyMigrator` 执行真实批量迁移并保留独立快照回滚。

产品 descriptor 通过版本化 `runtime-product.json` 固定 GeneratedSkillProduct 的 profile、任务合同、
Core/SDK 依赖和 capability 声明，并以 `manifest_hash` 防止安装前被静默修改。Skill 根目录存在
descriptor 时，Host Installer/CLI 自动调用 `install_runtime_product()`；Extension-backed 产品的
`HostRuntime` 仍必须由显式 `--host-runtime` 快照提供，缺失依赖时 fail-closed。没有 descriptor 的
历史树继续走兼容 `install()`，不把产品合同强行套到 legacy 产物。

M13-08 增加了 Build/Publisher 的 descriptor 发出样板：`RuntimeProductEmitter` 在既有
staging 树完成 Skill 资源和质量产物后，按 `RuntimeClosureSpec` 将可运行文件投影为带版本、hash
和来源的 Closure 资源，并在同一目录写入 `runtime-product.json` 与 `runtime-closure.json`。
Build 只允许发出 `candidate` Closure，Publisher 在质量门通过后发出 `production` Closure；任一
发出参数缺失或 bundled capability 未绑定已发出资源都会 fail-closed。M13-16 已将默认合同接入
真实 Build/Publisher/Update：有来源台账时 Build 自动 candidate、Publisher/Update 自动 production；
无台账的内存便捷路径仍不写 descriptor。真实迁移和宿主证据见 M13-17～19。

M13-09 在 opt-in `install_runtime_product()` 中增加 Product/Closure 任务合同绑定校验：
`task_contract_id/version` 不一致时在原子安装前返回 `RUNTIME_CLOSURE_INVALID`，目标目录保持未变；
匹配时继续使用现有备份与目录换入路径。旧 `install()` 和默认 legacy 产物不受影响。

M13-10 在同一 opt-in 安装路径补充 capability 绑定校验：Product 与 Closure 的 capability 声明必须按
`capability_id/kind/version/resource_id` 无序匹配；错配时在原子安装前返回 `RUNTIME_CLOSURE_INVALID`，
目标目录保持未变。旧 `install()` 和默认 legacy 产物不受影响。

M13-11 让 `runtime-product.json` 顶层固定对应 `runtime-closure.json` 的 `closure_hash`。显式产品安装
在打开 Closure 后拒绝缺失或错配的 hash，再继续现有 task contract/capability 门；Build/Publisher
发出的 descriptor 自动携带该 pin。旧 `install()` 和默认 legacy 产物不受影响。

M13-12 在 RuntimeClosureManifest 解析阶段同时约束 `resource_id` 与资源 `path` 唯一；同一文件不得
被多个资源身份、版本或类别重复声明，避免运行时通过不同资源 ID 获得歧义映射。该约束只作用于
Closure 合同，不改变 legacy `install()`。

M13-13 增加 Build-output 的纵向 Closure 回放证据：无版权 fixture 先由 Build 生成 Skill 目录，
再显式发出 production Closure 和 bundled deterministic tool；回放进程隐藏 Core、source、
workspace 与 output，仅通过 Closure 内相对路径执行，并验证输出中的 Closure hash 与 Skill hash。
M13-14 收紧 Closure 来源证据：每个 `ClosureResource` 必须携带至少一个不重复的
`source_id + locator (+ source_sha256)` 引用；Runtime Product 发出在扫描资源前拒绝空或重复
`SourceManifest`。该门已接入真实 Build/Publish；无来源台账的内存便捷路径和已发布 Schema 保持不变。
M13-15 进一步将显式 Build/Publish 的审核 `KnowledgeUnit.source_refs` 投影为
`block:<block_id>` locator，并要求其 `source_id` 存在于同批 `SourceManifest`；因此生成闭包可在
不依赖中央 Raw 的前提下保留最小 block 级来源路由。直接实验 API 未提供 block refs 时仍只作为
fixture-level generated locator；真实 Build/Publish 默认传入 block refs，迁移器也要求历史 provenance
ledger 有可解析 block 路由。

### M13 生产接入（2026-08-25 授权检查点）

M13-16 将 Runtime Product 合同接入真实 Build/Publisher 尾部：只要有可回放的来源台账，Build
自动发出 `candidate`，Publisher/Update 自动发出 `production`；默认合同由已校验的 Skill slug
派生，禁止从书名、collection 或模型响应推断任务身份。无 `data_home` 的纯内存测试路径仍保持
不可发布的 legacy 便利模式。

M13-17 提供带人工确认的 `migrate` CLI 和 `LegacyMigrator`。迁移先校验历史 `provenance.yml`、
`references/provenance.md` 的 source/block 路由，再在 sibling staging 发出 production Closure，
并把原树原子移动至显式快照根；批次激活失败会逆序恢复。2026-08-25 已将授权部署备份中的四个
真实 Skill 迁移并复核为 `closure-ready`。

M13-18 将 Host Installer 的默认 `install()` 接到产品门：检测到 `runtime-product.json` 时，在任何
备份、复制或 swap 前自动打开 production Closure，校验 product/profile、task contract、capability
和 pinned hash；CLI 同样自动发现 descriptor，仍无 descriptor 的历史 Skill 走兼容路径。

M13-19 在五类项目宿主（Claude、TRAE、Codex、Project、ChatGPT）分别安装四个迁移 Skill，并用无
`PYTHONPATH` 的隔离子进程逐项读取安装树、复算 manifest/descriptor hash 和全部 Closure 资源 hash，
共 20 个安装任务通过。该证据是宿主目录适配与 Closure 任务 harness，不冒充未安装的第三方宿主
客户端对话/模型质量结论。

M13-20 新增 `PersistentPackStore` 作为 Pack-only 增量发布的持久化接缝：candidate、不可变
production release 和 `active.json` 指针分开存储，release 内容以 `pack_hash` 固定，active 指针用
同目录原子写切换。发布前通过期望 base version 做乐观并发校验，失败时旧 active 保持可读；rollback
只选择同 task/kernel 且 hash 有效的历史 release。该接缝尚未替换现有 Update/Publisher 的整树重编，
因此当前仍需后续真实多书回放才能宣称生产级增量编译。

M13-22 新增显式 `UpdateUseCase.publish_pack_incremental` 接口及
`PackIncrementalPublisher`：调用方提交已带 source provenance 的多书 `AssetCandidate`，流程固定为
candidate→review→approved→`MultiBookGovernance`→`PersistentPackStore.publish_incremental`。
该路径不接触 Skill 目录、Kernel 文件或现有整树 Update；冲突、stale base、版本错误和 regression
失败均在 active pointer 切换前 fail-closed。当前实现同时支持传入 production Closure 根：Pack
release 会以 `assets/pack-release.json` 固定写入 Closure，升级/回滚更新同一资源并重算 descriptor
与 Closure hash；绑定失败触发 Pack/Closure 补偿回滚。普通 `UpdateUseCase.execute` 仍保持兼容的
整树路径，不因 Pack-only 接口而默认切换。

P0 真实回放已在用户授权的三本电子书上完成：40 个候选资产按 source 为 8/16/16，release 链
`1.0.0 → 1.1.0 → 1.2.0` 后回滚到 `1.1.0`，Kernel hash 全程不变。最终 Skill 生产目录和
installed-copy 重新运行带权威 SourceManifest 的 `check_generated_skill_integrity.py` 均通过；
Pack provenance 仍独立覆盖其实际纳入的来源。

生成物完整性闸门：Build/Publisher 在暴露或发布前写入
`content-integrity.json`，按 active normalized unit、references 章节、source ref、
`provenance.yml` 来源哈希和必要文件逐项对账；`compilation-artifact.json` 随后覆盖该
报告及最终目录中的全部文件。最终目录或载体必须重新打开运行
`scripts/check_generated_skill_integrity.py`，不能以 staging 或同一进程对象替代；未提供
权威 SourceManifest 时只能记录 internal-only。
