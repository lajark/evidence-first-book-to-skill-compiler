# 开源代码复用与独立性政策

- 文档版本：v2.2-final
- 原则：合理合法复用，不重复造轮子；Book2Skill Core 必须能够在第三方上游仓库不存在、断网的情况下独立构建、测试和运行，同时为用户自有的下游扩展提供稳定、版本化的公开接口。

## 1. 允许的复用方式

1. **普通第三方依赖**：通过 PyPI、系统包管理器等使用成熟通用库，锁定版本并保留锁文件与许可证清单。
2. **Selective Port（选择性移植）**：固定上游 Commit SHA 后，选择性复制少量、边界清晰、已有测试价值的实现。
3. **Design Reference（设计参考）**：借鉴流程、目录或交互原则，不复制可识别实现。
4. **Clean-room Reimplementation（洁净重写）**：先记录需求与行为测试，再独立实现，不以“AI 改写”规避来源义务。

## 2. 禁止方式

- 以 Fork 作为产品主仓库；
- Git Submodule、Git Subtree；
- 构建时 `git clone` 或运行时下载上游源码；
- 远程源码 import、直接依赖上游应用服务或数据目录；
- 复制许可证不明、许可证不兼容或无法确认权属的代码；
- 先复制、后补许可证；
- 删除版权头、隐去来源，或用模型改写代码来规避署名；
- 将上游仓库的测试结果当作本项目测试结果。

## 3. 每次移植的强制步骤

1. 在 `docs/PROVENANCE.yml` 登记上游仓库、精确 Commit SHA、访问日期和该 Commit 下的许可证。
2. 记录原始文件路径、本地文件路径、复用类型、复制比例、实质修改和保留的版权头。
3. 把适用的许可证原文放入 `LICENSES/`，并更新 `THIRD_PARTY_NOTICES.md` 与 `ACKNOWLEDGMENTS.md`。
4. 为移植行为补充本项目自己的单元/集成测试；测试不能只验证与上游相同，还要验证本项目边界。
5. 执行依赖许可证扫描、来源一致性检查和断网独立性测试。
6. 上游升级采用“重新评估 → 新 Commit 重新登记 → 小步移植”，禁止自动追随主分支。

## 4. 当前候选上游

- `virgiliojr94/book-to-skill`：重点评估格式抽取、四种运行模式、渐进披露、更新与校验。
- `apple-ouyang/book-to-skill`：重点评估单一任务 Skill、主/子 Skill 路由、真实案例与可执行步骤。
- `agentskills/agentskills`：作为开放规范与官方参考校验基线；代码与文档可能适用不同许可证，实施时分别锁定并核验。
- `anthropics/skills`：只参考 Skill 结构、触发与评测方法；不同目录可能存在不同许可，不按仓库整体推定可复制范围。
- `agent-ecosystem/skill-validator`：优先采用锁版本的外部 CLI 进程集成，不复制实现；其检查只补充项目内部来源、安全和版权质量门。
- `qomob/SkillCompiler`、`generative-computing/mellea-skills-compiler`、`SJTU-IPADS/SkVM`：仅作 IR、验证、Profiling 和跨模型评测的设计/研究参考，不作为 v1.x 稳定运行依赖。

截至 2026-08-06 的公开页面核验只用于规划，不能代替实施时的 Commit 级复核。任何外部调用、依赖或代码复制前仍须重新锁定 Commit/版本、核验适用文件许可证并完成来源登记；仅引用设计思想时记录 `design_reference`，不得虚构代码移植。

### v1.0.1 实施记录

- Agent Skills 规范锁定到 `217be548739f21d6008915c29aefe320ea1a90af`；
- `skills-ref==0.1.0` 与 `skill-validator==1.5.6` 只通过可选、锁版本的 CLI 适配器调用；
- 本轮新增实现均为项目内独立实现，没有复制这些参考项目的源码，因此不新增 selective-port provenance 条目；
- `scripts/check_provenance.py` 已对既有选择性移植记录重新验证通过。


## 5. 用户自有下游扩展

DD Methods 和 DD Workbench 是用户自有的正式下游产品，不属于本节的第三方上游。它们可以声明对 Book2Skill Core 的运行依赖，但只能依赖正式发布包、公开 Extension SDK 和版本化 contracts；不得通过复制 Core 私有源码、Git Submodule、源码符号链接或运行时拉取仓库集成。Core 的“第三方上游独立性”与“对下游提供稳定基础能力”必须同时满足。
