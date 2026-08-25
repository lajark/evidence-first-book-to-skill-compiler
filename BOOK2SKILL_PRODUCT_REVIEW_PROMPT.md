# Book2Skill 需求与产品复核启动提示词

你是 `D:\AI\01_Book2Skill` 项目的续接开发 Agent。本轮任务是对 Book2Skill 的产品定位、生成物完整性和多书增量编译机制进行一次重大需求复核。

请使用中文沟通，先给结论和证据，再说明方案、风险和下一步。不要预设项目一定存在问题，必须从真实代码、构建链、发布包和隔离运行结果中获得证据。

## 一、本轮任务边界

本轮只授权：

1. 只读检查现有代码、测试、Schema、构建脚本、发布包和项目文档；
2. 复核 Book2Skill 的需求、产品定义和生成 Skill 的运行边界；
3. 更新项目1的 PRD、架构、ADR/决策、实施计划、验收计划、TODO、HANDOFF；
4. 编写下一轮正式开始代码优化的启动提示词。

本轮不授权：

- 修改业务代码、Schema、SkillCompiler、发布构建或已有 Skill；
- 重建、安装、发布或部署产物；
- 提交、推送、创建 Release；
- 修改项目2 `D:\AI\02_DDSkill`；
- 自动批准书籍方法、合并作者冲突或删除历史资产；
- 访问外部网络或生产系统。

## 二、开始前必须完成

先完整读取并遵循项目1适用范围内的：

- `AGENTS.md`
- PRD、README、架构和 ADR
- TASKS/TODO/HANDOFF
- 与 SkillCompiler、SkillIR、Bundle、发布构建、Standalone、Extension、Host Adapter 有关的规格
- 相关 Schema、测试和发布说明

随后执行：

```powershell
git status --short
git diff --name-only
```

保留所有用户和既有 Agent 的未提交修改，不得执行 reset、checkout、clean 或覆盖操作。

本任务涉及生成或升级 Skill 产品模型，必须完整读取并使用 `skill-creator` 的 `SKILL.md`。重点关注：

- `SKILL.md` 应保持精简并承载稳定工作流；
- 详细领域知识按需进入 references；
- 输出模板进入 assets；
- 重复且确定性要求高的操作进入 scripts 或明确的 Tool Contract；
- Skill 资源必须可发现、可定位、可验证，不能成为隐形环境依赖。

## 三、需要复核的核心产品问题

### 1. Book2Skill 与生成 Skill 的产品边界

明确区分以下三层：

1. `Book2Skill Core`：电子书发现、抽取、规范化、SkillIR、编译、验证、宿主适配和发布生命周期工具；
2. `Generated Skill Product`：由电子书编译产生、面向具体任务、可以部署和使用的 Skill；
3. `Host/Harness`：负责项目状态、文件访问、工具执行、调度、恢复和审计的运行环境。

重点判断：

- Core 应当是生成 Skill 的构建期依赖，还是运行期依赖？
- Standalone/逐 Skill 发布包在运行时是否仍读取项目1源码、Python 包、中央知识目录、原始电子书、临时 Bundle 或本机绝对路径？
- Extension 依赖 Core 是否被错误泛化成所有生成 Skill 都必须依赖 Core？
- 当宿主没有安装 Book2Skill 时，生成 Skill 是否仍能完成声明的任务？
- 当前所谓“Standalone”是否只是能够安装，而不是能够完成任务？

不能以“zip 中存在 SKILL.md”证明 Skill 已完整可用。

### 2. 完整可用 Skill 的产品模型

以以下公式作为待验证和细化的产品假设：

```text
CompleteUsableSkill
= SkillKernel
+ pinned AssetPacks
+ Schemas
+ ToolCapabilities
```

其中：

#### SkillKernel

应承载低频变化、成熟稳定的能力：

- 任务身份、触发条件和不适用边界；
- 输入输出合同；
- 核心分析框架和执行顺序；
- 证据纪律、来源要求和反证要求；
- Asset Pack 的发现、选择和适用范围判断；
- 冲突、资料缺口、不确定性、暂停和降级机制；
- 失败恢复和人工确认点；
- 宿主或工具能力不可用时的明确行为。

Kernel 不能退化成“请阅读知识库并完成任务”的空壳。

#### AssetPacks

应承载可独立审核和升级的内容资产：

- 书籍方法、原则、检查项和操作技术；
- 问题清单、报告模板和输出样例；
- 公式、参数、规则和行业/阶段扩展；
- 案例、例外、常见误用和反例；
- 来源定位、版本、适用范围、冲突关系和审核状态。

兼容的内容更新应优先发布新 Asset Pack，而不是频繁修改 SkillKernel。

#### Schemas

应覆盖：

- 输入输出；
- Asset、Pack、Kernel 和 Closure manifest；
- 来源与追踪关系；
- 兼容范围；
- 工具输入输出和稳定错误。

#### ToolCapabilities

应明确区分：

- Skill 包内可执行的确定性脚本；
- 由宿主提供的工具；
- 可选工具；
- 必需但当前宿主不支持的工具。

外部工具能力必须在 manifest 中声明并接受安装前检查，不能作为未记录的隐形依赖。

物理上允许 Kernel、Packs、Schema 和工具分开存放，但运行时必须由 manifest 固定 ID、版本、兼容范围和 hash。缺少必需资源时应 fail-closed 或显式降级，不能由模型常识补齐。

## 四、重点复核多书、多任务的编译模型

一本电子书不应默认只生成一个 Skill。请检查当前产品和实现是否能够表达：

```text
一本书
→ 多个知识单元
→ 多个任务候选
→ 一个或多个任务型 Skill/Asset Pack 更新
```

同时，多本书可能包含相同任务的方法：

```text
书籍 A ─┐
书籍 B ─┼→ 同一 task identity → 既有 SkillKernel + 新版 AssetPacks
书籍 C ─┘
```

需要设计或复核以下机制：

1. 使用稳定的 `task_contract_id`、任务语义或等价机制识别“同一任务”，不能用书名或 source ID 作为 Skill 身份；
2. 新书编译结果先形成 candidate assets，不得直接改写 production Skill；
3. 如果任务合同与既有 Skill 兼容，优先进入既有 Skill 的 Asset Pack 治理流程；
4. 合并流程至少包含：
   - 来源追踪；
   - 语义去重；
   - 适用范围识别；
   - 作者冲突保留；
   - 新旧关系；
   - 人工审核；
   - 回归测试；
   - Pack 发布和回滚；
5. 不得因新增一本书或新增一个方法就修改 SkillKernel；
6. 只有出现以下变化时才考虑升级 Kernel：
   - 新的任务合同；
   - 核心分析框架改变；
   - 输入输出语义改变；
   - 资源解析协议改变；
   - 安全、证据、失败恢复或工具边界改变；
7. 如果新知识属于真正不同的任务，不应强行合并到已有 Skill，应创建新的 task identity 和 Kernel；
8. 作者观点冲突不得自动投票或覆盖，应按来源、阶段、行业、法域等适用范围并存，无法判断时进入人工裁决。

请重点判断当前编译器究竟是：

- “每本书生成一套新的 Skill”；
- “每次编译覆盖旧 Skill”；
- “把全部书籍内容堆入一个大 Skill”；
- 还是已经具备以任务为中心的增量资产治理能力。

## 五、资源加载可靠性

复核是否需要将资产分为：

1. `exact_required`
   - Schema；
   - 公式；
   - 固定模板；
   - 模式结构；
   - blocker 规则；
   - 其他承重资产。

   必须按稳定 ID、版本和 hash 精确读取，不能依赖模糊语义召回。

2. `semantic_retrieval`
   - 方法解释；
   - 案例；
   - 参考问题；
   - 扩展知识。

   可以按需召回，但必须有完整索引、来源和覆盖记录。

3. `optional`
   - 非承重扩展。

   缺失时必须显式说明降级结果。

## 六、必须进行的证据审计

选择至少一个真实的生成 Skill，追踪完整调用链：

```text
电子书
→ Source/Extraction/Raw
→ KnowledgeUnit/Normalized Bundle
→ SkillIR
→ SkillCompiler
→ Skill目录
→ 发布包
→ 宿主安装
→ 运行时资源读取
```

检查：

- `SKILL.md` 实际包含什么；
- references/assets/scripts/schema 是否进入发布包；
- 方法和模板是否只是留在中央 Bundle 或项目源码目录；
- 是否存在绝对路径、源码 import、本项目包 import 或运行时 Core import；
- 删除或隐藏项目1源码、原书和构建目录后是否仍能运行；
- 必需工具能力不可用时是否诚实报告；
- provenance 是否能从输出追溯到书籍位置；
- 构建成功、验证成功、安装成功与任务成功是否被错误等同。

如果本轮不能执行完整隔离测试，必须列出未验证项，不能写成通过。

## 七、必须覆盖的产品场景

至少为以下场景定义预期行为和未来验收：

1. 一本书产生三个不同任务的 Skill/Pack；
2. 第二本书补充一个已有任务，Kernel 不变，只升级 Pack；
3. 两本书对同一方法存在冲突；
4. 新书提供兼容方法和新模板；
5. 新书导致任务合同发生不兼容变化；
6. Pack 缺失、hash 损坏或版本不兼容；
7. 宿主没有安装 Book2Skill Core；
8. 宿主缺少声明的确定性工具；
9. 原书和项目1源码完全不可见；
10. 同一 Kernel 分别绑定两个兼容 Pack 版本，核心流程、输出合同和安全边界保持稳定；
11. candidate 资产未审核，不能污染 production Pack；
12. 同一资产被多本书重复描述，合并后仍保留完整来源。

## 八、版本与升级模型

请提出并评估至少以下独立版本：

- `skill_kernel_version`
- `task_contract_version`
- `asset_pack_id`
- `asset_pack_version`
- `schema_version`
- `tool_capability_version`
- `runtime_closure_version/hash`

每次运行必须冻结实际使用的 Closure。运行中不能因知识库更新而静默切换 Pack。

普通内容更新优先只升级 Asset Pack；Kernel 应采用保守升级：

- Patch：不改变行为的修正；
- Minor：向后兼容的框架或能力增加；
- Major：任务合同、输入输出或安全语义不兼容。

## 九、本轮应形成的交付物

完成复核后，先给出证据支持的结论，再更新项目文档。

至少交付：

1. 《生成 Skill 产品完整性复核报告》
   - 当前真实产品形态；
   - 构建期与运行期依赖；
   - 已满足能力；
   - 缺口；
   - 严重级别；
   - 证据位置；
   - 未验证项。

2. 产品定义
   - Book2Skill Core；
   - Generated Skill；
   - Stable SkillKernel；
   - AssetPack；
   - Runtime Closure；
   - Host/Harness；
   - Extension 与 Standalone 的边界。

3. 多书增量编译方案
   - task identity；
   - existing/new Skill 判定；
   - candidate → review → approved → merge → regression → publish；
   - 去重、冲突、适用范围和来源追踪；
   - Kernel/Pack 分别升级的规则。

4. 文档更新
   - PRD；
   - ARCHITECTURE/ADR；
   - IMPLEMENTATION_PLAN；
   - ACCEPTANCE_TEST_PLAN；
   - TASKS 或静态路线图；
   - `.workspace/TODO.md`；
   - `.workspace/HANDOFF.md`；
   - 必要的决策记录。

5. 下一轮代码优化启动提示词
   - 从最小纵向样板开始；
   - 不直接批量迁移所有生成 Skill；
   - 明确测试优先、回滚和发布授权边界。

## 十、建议的阶段顺序

建议将后续工作拆为：

1. 产品和依赖现状审计；
2. 选择一个真实 Skill 做隔离闭包 Spike；
3. 冻结 Kernel/AssetPack/Closure/Task Identity 合同；
4. 实现同任务 Asset Pack 增量合并样板；
5. 验证多书冲突、去重和来源追踪；
6. 验证 Kernel 不变的 Pack 独立升级；
7. 扩展到多任务和全部发布 profile；
8. 全量回归、迁移和发布。

如果第2、3步没有关闭关键设计问题，不要开始大规模代码修改。

## 十一、完成标准

复核阶段完成必须满足：

- 明确回答生成 Skill 是否依赖项目1代码或 Core 才能运行；
- 区分 Extension 依赖和 Standalone 依赖；
- 给出至少一个真实生成 Skill 的资源闭包证据；
- 建立以任务而不是书籍为中心的 Skill 身份模型；
- 明确同任务新书优先升级 Asset Pack，而不是修改 Kernel；
- 明确何时必须创建新 Skill 或升级 Kernel；
- 建立多书去重、冲突、审核、追踪和回滚机制；
- 给出可测试的验收条件；
- PRD、架构、TODO、HANDOFF 和下一轮启动提示词完成同步；
- 清楚列出未验证项和残余风险。

## 十二、最终报告格式

最终报告请包含：

- 任务名称/编号；
- 结论；
- 当前产品真实状态；
- 发现的问题及证据；
- 推荐产品模型；
- 修改文件；
- 执行的检查及真实结果；
- 未执行检查；
- 已知限制和风险；
- 下一任务。

不要因为文档中写有“Standalone”“完整 Skill”或“验证通过”就直接采信；必须由发布包内容、运行时调用链和隔离验证证明。

同任务内容应优先合并到既有 Skill，但只有在任务合同兼容时才合并，不能为了减少 Skill 数量而把不同任务强行塞进同一个 Kernel。
