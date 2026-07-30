# 元 Skill `book2skill` 来源与自举说明

本文件说明元 Skill `book2skill` 的来源、自举状态与已知限制，供宿主 Agent 与审查者核对。

## 来源

元 Skill `book2skill` 当前为**手写产物**（非 Book2Skill 流程编译输出），来源为 Book2Skill 项目自身的设计文档：

- `PRD.md` v2.0-final：产品定位、P0 目标、FR-01~FR-10、NFR、MVP 验收；
- `ARCHITECTURE.md`：Ports & Adapters + 编译器管线、目录结构、关键设计决策；
- `SKILL_AUTHORING_STANDARD.md`：生成 Skill 编写标准 8 条；
- `SKILL_DEPLOYMENT.md`：Claude / TRAE / Codex / ChatGPT Project 部署步骤；
- CLI 命令合同（`src/book2skill/cli.py`）：实际命令签名、flags、退出码与输出合同。

`provenance.yml` 中 `review_status: hand-authored`，`content_sha256` 标 `hand-authored`（非真实哈希，因元 Skill 不经过 Raw 固化流程）。

## 自举状态

PRD 项目概述与 TODO TASK-019 备注指出：元 Skill 自身也是 Book2Skill 的产物，体现"自举"能力。当前状态：

- **当前**：手写元 Skill，遵循与生成 Skill 相同的目录结构（`SKILL.md` + `references/` + `assets/` + `scripts/` + `provenance.yml`）与编写标准，作为宿主调用 Book2Skill CLI 的入口；
- **未来 P2**：可尝试用 Book2Skill 流程把项目自身文档（PRD/ARCHITECTURE/SKILL_AUTHORING_STANDARD 等）编译为元 Skill，验证自举闭环。但元 Skill 的"主题"是 Book2Skill 自身的使用方法，并非某本书的内容，故手写更准确，强行走流程可能引入语义偏差。

## 校验状态

元 Skill 可通过本项目验证器自检：

```bash
book2skill validate skills/book2skill
```

预期结果：`pass` 或 `pass_with_warnings`（详见 `quality-report.md`）。已知可能触发的警告：
- `budget.below_target_min`：SKILL.md 低于 2500 token 目标下限（手写元 Skill 篇幅较生成 Skill 短）；
- `links.missing_reference`：若 SKILL.md 引用了 `references/<file>.md` 但文件不存在。

## 已知限制

- 元 Skill 不含真实书籍来源，`provenance.yml` 中的 source_id 为设计文档引用，非 Raw 哈希追溯；
- 元 Skill 自身不携带 Book2Skill CLI 可执行文件，部署前需确保目标环境已安装 `book2skill` CLI（通过 `pip install -e .` 或 `uv sync`）；
- 元 Skill 的 `scripts/smoke_test.py` 仅验证 CLI 可发现与 Analyze Only 可运行，不验证全模式；
- 跨平台仅 Windows 11 实测。

## Sources cited

- prd / requirements
- architecture / design
- skill-authoring-standard / rules
- skill-deployment / steps
- cli / contract
