# 项目 TODO

## 1. 项目摘要
- 项目目标：将用户合法持有的 PDF、EPUB、MOBI/AZW、TXT、Markdown、DOCX 等文档编译为可追溯、可审核、可增量更新、跨宿主部署的 Agent Skill。
- 当前阶段：M0 + M1 完成（TASK-001~007 全部完成），选择性移植阶段 A+B 完成，M2 完成（TASK-008 边界测试 +22 用例、TASK-009 MOBI DRM 检测 +6 用例、TASK-010 批处理编排 +16 用例），M3 完成（TASK-011 KnowledgeUnit 与 Schema 层 +33 用例、TASK-012 SkillIR 编译器 +52 用例、TASK-013 Full Build 与 Build from Analysis +30 用例），M4 完成（TASK-014 差异引擎与冲突检测 +50 用例、TASK-015 Update 模式与原子发布 +25 用例完成，387 passed/1 skipped，覆盖率 89%），M5 完成（TASK-016 安全与质量校验器 +53 用例、TASK-017 多宿主安装器 +64 用例完成，504 passed/1 skipped，覆盖率 90%），M6 完成（TASK-018 端到端验收与断网独立性测试，PRD MVP 10 项全部通过，504 passed/1 skipped，覆盖率 90%，ruff/mypy 53 源文件/check_provenance 全部 PASS），TASK-019 完成（更新元 Skill `book2skill`，手写交付 SKILL.md + references/ + assets/ + scripts/ + provenance.yml，validate 5/5 pass，install --dry-run + smoke_test 通过）。PRD P0 任务与 P1 元 Skill 均已交付，项目进入收尾阶段。
- 主要交付物：
  - Python CLI 工具 `book2skill`（Typer + Rich）；
  - 元 Skill `book2skill`（可部署到 Claude Code / TRAE / Codex / ChatGPT / 通用项目）；
  - 版本化 JSON Schema（6 个）；
  - 格式适配器（7 类格式，P0 覆盖 6 类 + P1 的 HTML/RTF）；
  - 安全与质量校验管线；
  - 多宿主安装器。
- 规划依据：PRD.md v2.0-final、ARCHITECTURE.md、DATA_MODEL.md、TASKS.md、IMPLEMENTATION_PLAN.md、ACCEPTANCE_TEST_PLAN.md、OPEN_SOURCE_REUSE_POLICY.md、SKILL_DEPLOYMENT.md、6 个 JSON Schema、3 个模板。

## 2. 已确认的关键决策
- 决策：Ports & Adapters + 编译器管线架构｜依据：ARCHITECTURE.md｜实施影响：严格分层，Domain 不依赖文件系统/网络/具体模型
- 决策：SkillIR 是唯一跨宿主语义源｜依据：PRD FR-04｜实施影响：不为 Claude/TRAE/Codex 各维护一套知识正文
- 决策：人工修改保存为结构化 override/patch｜依据：ARCHITECTURE.md 关键设计决策｜实施影响：更新时三方合并，不依赖注释字符串判断
- 决策：Raw 不可变；更新创建新版本与日志｜依据：PRD 数据分层铁律｜实施影响：所有写入操作需版本化、幂等
- 决策：上游代码选择性移植（Selective Port），禁止 Fork/Submodule/Subtree｜依据：OPEN_SOURCE_REUSE_POLICY.md｜实施影响：每次移植需完整 provenance 登记、许可证核验、独立测试
- 决策：默认离线、无遥测、云模型显式 opt-in｜依据：PRD P0-7、SECURITY.md｜实施影响：LLM Adapter 需支持离线优先和显式网络开关
- 决策：主 Skill 目标 2,500–5,000 tokens，硬上限由配置和官方校验器决定｜依据：PRD FR-04、INTEGRATION_REVIEW.md｜实施影响：编译器需内置 token 预算检查
- 决策：MOBI/AZW 通过可选 Calibre Adapter 支持，不作为硬依赖｜依据：FORMAT_ADAPTERS.md｜实施影响：需提供降级和离线安装说明
- 决策：P0 不使用 SQLite；所有状态可由版本化文件重建｜依据：ARCHITECTURE.md 关键设计决策｜实施影响：存储层使用 JSON/JSONL/YAML/Markdown 文件

## 3. 待确认事项
### 阻塞项
- **[B-01]（已解决）** 上游仓库 `virgiliojr94/book-to-skill` 和 `apple-ouyang/book-to-skill` 已在 Commit `92b248fa`（MIT）核验并完成选择性移植（阶段 A+B，共 8 个文件）。PROVENANCE.yml 已登记。
- **[B-02]（已解决）** M0 问项目骨架已就绪：pyproject.toml、src/、tests/、Ruff/mypy/pytest 配置、AGENTS.md 真实命令均已回填。

### 非阻塞项
- **[N-01]（已解决）** Calibre CLI 9.11.0 已通过 winget 安装，`ebook-convert` 可用。MOBI 往返测试（合成 TXT → MOBI → 提取）真实通过。路径已写入 `~/.bashrc`。TASK-009 已实现 Calibre 缺失降级（`EXTRACT_UNSUPPORTED` + 安装指引）与 DRM 检测（PalmDoc encryption-type 预检 → `GATE_ENCRYPTED_FILE`），降级路径用 monkeypatch 在无 Calibre 环境亦可测试。
- **[N-02]（已缓解）** LLM Adapter 接口已搭建（`src/book2skill/llm/`），M1 使用 `MockLLMAdapter`（规则驱动关键词启发式）完成 Analyze Only 模式。真正 LLM 驱动分析在 M3 阶段完善，届时需升级为 OpenAI-compatible Adapter 或本地模型。
- **[N-03]（已解决）** 本机 `pdftotext` 经 Git for Windows 的 mingw64 已可用；PyMuPDF 1.28.0 / pypdf 4.3.1 / pdfminer.six 均已安装。PDF 所有后端均可真实抽取。ebooklib 0.20 / python-docx 1.2.0 / beautifulsoup4 4.15.0 均已安装，EPUB/DOCX/HTML 首选后端可用。
- **[N-04]** 测试书样本（A-G 测试集）尚未准备。M1 开始前需至少准备 A（文字型 PDF）和 E（TXT/Markdown）样本。

### 实施中验证项
- **[V-01]** Schema 版本号（当前均为 v1）在实际实现中可能需要调整字段，需在 M3 完成后做往返一致性验证。
- **[V-02]** Token 预算计算方式需与目标宿主校验器对齐，M5 阶段验证。
- **[V-03]** 中文分词与引文长度计算（"25 个英文词或等量中文字符"）的具体实现需在 FR-08 校验器中验证。

## 4. 里程碑

### M0：治理与仓库初始化
- 目标：建立可运行的 Python 项目骨架、依赖管理、许可证流程和 CI 基础
- 交付物：pyproject.toml、锁文件、src 目录骨架、pytest 配置、Ruff/mypy 配置、许可证文件、provenance 流程、AGENTS.md 命令回填
- 完成标准：`pytest`、`ruff check`、`mypy` 可真实运行并通过（即使只有骨架代码）
- 依赖：无（首批任务）
- 对应任务：B2S-M0-01、B2S-M0-02

### M1：Raw 层与 PDF/TXT 垂直切片（✓ 已完成）
- 目标：实现输入合法性检查、SourceManifest、不可变 Raw 存储、PDF/TXT 抽取、ExtractionMap、Analyze Only 模式
- 交付物：domain 模型、storage 端口与文件实现、PDF/TXT extractor、CLI `analyze` 命令、AnalysisBundle 输出
- 完成标准：Analyze Only 对 PDF/TXT 样本输出合法 AnalysisBundle，不生成 Skill；Raw 哈希可验证
- 依赖：M0
- 对应任务：B2S-M1-01、B2S-M1-02、B2S-M1-03

### M2：格式适配器扩展（✓ 已完成）
- 目标：支持 EPUB、DOCX、Markdown、MOBI/AZW 格式，批处理部分成功
- 交付物：EPUB/DOCX/MD extractor、MOBI/AZW 可选 Adapter、批处理编排、失败清单
- 完成标准：六类必选格式通过 Analyze Only；批处理单文件失败不影响其他文件
- 依赖：M1
- 对应任务：B2S-M2-01（TASK-008 ✓）、B2S-M2-02（TASK-009 ✓、TASK-010 ✓）

### M3：SkillIR 与 Skill 生成
- 目标：实现 KnowledgeUnit 模型、SkillIR、Full Build 和 Build from Analysis 模式
- 交付物：KnowledgeUnit Schema 实现、SkillIR 编译器、Wiki 生成器、`build` 和 `build --from-analysis` 命令
- 完成标准：Full Build 产物完整并通过校验；来源可追溯；标准目录结构正确
- 依赖：M1（M2 可并行）
- 对应任务：B2S-M3-01、B2S-M3-02

### M4：更新与增量（✓ 已完成）
- 目标：实现 Update/Fold-in 模式、差异引擎、override 三方合并、原子发布和回滚
- 交付物：diff 引擎、冲突检测、`update`/`diff` 命令、publisher、快照与回滚
- 完成标准：Update 输出新增/修改/冲突/废弃建议；人工 override 不丢失；回滚可恢复
- 依赖：M3
- 对应任务：B2S-M4-01（TASK-014 ✓）、B2S-M4-02（TASK-015 ✓）

### M5：安全、质量门与多宿主部署（✓ 已完成）
- 目标：实现安全扫描、版权/引文校验、注入检测、质量报告和多宿主安装器
- 交付物：validators（注入/路径/版权/链接/预算）、quality-report 生成、`validate`/`install` 命令
- 完成标准：注入测试被识别；长引文被标记；校验失败不覆盖已发布版本；安装器 dry-run 通过
- 依赖：M3
- 对应任务：B2S-M5-01（TASK-016 ✓）、B2S-M5-02（TASK-017 ✓）

### M6：端到端验收
- 目标：使用 A-G 测试集完整验收，追踪矩阵全覆盖，断网独立性验证
- 交付物：验收证据报告、追踪矩阵更新、Gitee 私有仓库推送
- 完成标准：PRD MVP 10 项验收全部通过
- 依赖：M1-M5
- 对应任务：B2S-M6-01

## 5. 任务清单

### M0 任务

- [x] TASK-001 初始化 Python 项目骨架
  - 里程碑：M0
  - 优先级：P0
  - 状态：已完成
  - 需求依据：PRD P0-1~10、AGENTS.md Commands 节、IMPLEMENTATION_PLAN.md M0
  - 目标与产物：
    - `pyproject.toml`：Python 3.11+、Pydantic v2、Typer、Rich、pytest、Ruff、mypy
    - 锁文件（uv lock 或 pip-tools）
    - `src/book2skill/__init__.py`、`src/book2skill/cli.py` 骨架
    - `tests/__init__.py`、`tests/test_cli.py` 骨架
    - Ruff 配置、mypy 配置、pytest 配置
    - `.gitignore` 补充（如需要）
    - 回填 AGENTS.md 中所有 `<M0 后填写真实命令>` 占位符为真实可运行命令
  - 前置依赖：无
  - 涉及文件：`pyproject.toml`、`uv.lock`、`src/book2skill/__init__.py`、`src/book2skill/cli.py`、`tests/`、`AGENTS.md`
  - 验收标准：
    - `pytest` 可运行（即使 0 测试通过）
    - `ruff check src/` 可运行
    - `mypy src/` 可运行
    - `python -m book2skill` 或等效入口可调用
    - AGENTS.md 中所有命令已替换为真实命令
  - 验证方式：依次执行上述命令，确认退出码和输出
  - 风险或备注：
    - 需确认本机 Python 3.11+ 可用
    - 包管理器选择：推荐 uv（与 AGENTS.md 权限配置一致），备选 pip-tools
    - 可选依赖分组：pdf、epub、mobi、docx、ocr、llm 等

- [x] TASK-002 建立许可证与 Provenance 流程
  - 里程碑：M0
  - 优先级：P0
  - 状态：已完成
  - 需求依据：OPEN_SOURCE_REUSE_POLICY.md、ACKNOWLEDGMENTS.md、THIRD_PARTY_NOTICES.md、docs/PROVENANCE.yml、docs/UPSTREAM_REUSE_CHECKLIST.md
  - 目标与产物：
    - 核验两个上游仓库的当前 LICENSE 文件（MIT）
    - 确定选择性移植的具体文件/模块清单
    - 更新 `docs/PROVENANCE.yml`（填入真实 Commit SHA）
    - 更新 `ACKNOWLEDGMENTS.md`（填入真实移植信息）
    - 更新 `THIRD_PARTY_NOTICES.md`
    - 将上游 LICENSE 文件复制到 `LICENSES/`
    - 创建 `scripts/check_provenance.py`：验证来源一致性
  - 前置依赖：TASK-001（需要 Python 环境）
  - 涉及文件：`docs/PROVENANCE.yml`、`ACKNOWLEDGMENTS.md`、`THIRD_PARTY_NOTICES.md`、`LICENSES/`、`scripts/check_provenance.py`
  - 验收标准：
    - PROVENANCE.yml 中 Commit SHA 不再为占位符
    - 两个上游 LICENSE 已复制到 LICENSES/
    - `scripts/check_provenance.py` 可运行并报告当前状态
    - 未复制代码时状态准确（"no_code_imported" 阶段）
  - 验证方式：运行 `python scripts/check_provenance.py`，检查输出
  - 风险或备注：
    - **阻塞项 B-01**：必须在本任务中完成上游 Commit 级核验
    - 如果上游 LICENSE 已变化或不可访问，需记录并决策

### M1 任务

- [x] TASK-003 实现 Domain 层核心模型
  - 里程碑：M1
  - 优先级：P0
  - 状态：已完成
  - 需求依据：DATA_MODEL.md、schemas/source-manifest.schema.json、schemas/extraction-map-entry.schema.json
  - 目标与产物：
    - `src/book2skill/domain/__init__.py`
    - `src/book2skill/domain/models.py`：SourceManifest、ExtractionMapEntry、SourceId、ContentHash 等
    - `src/book2skill/domain/errors.py`：稳定错误码、DomainError 基类
    - `src/book2skill/domain/state.py`：状态枚举（抽取、知识、冲突、发布）
    - 单元测试：`tests/domain/`
  - 前置依赖：TASK-001
  - 涉及文件：`src/book2skill/domain/`、`tests/domain/`
  - 验收标准：
    - SourceManifest 可序列化/反序列化并通过 JSON Schema 校验
    - 错误码包含输入 ID、阶段和恢复建议
    - 单元测试覆盖正常路径、边界和主要失败路径
  - 验证方式：`pytest tests/domain/ -v`
  - 风险或备注：Pydantic v2 模型需与 JSON Schema 保持一致；注意 schema_version 字段

- [x] TASK-004 实现 Storage 端口与文件系统实现
  - 里程碑：M1
  - 优先级：P0
  - 状态：已完成
  - 需求依据：PRD 数据分层（workspace/raw/）、ARCHITECTURE.md 存储层
  - 目标与产物：
    - `src/book2skill/storage/__init__.py`
    - `src/book2skill/storage/ports.py`：RawStorage、SchemaStorage、WikiStorage 抽象接口
    - `src/book2skill/storage/file_storage.py`：文件系统实现
    - 原子写入工具（临时目录 + 原子替换）
    - 单元测试：`tests/storage/`
  - 前置依赖：TASK-003
  - 涉及文件：`src/book2skill/storage/`、`tests/storage/`
  - 验收标准：
    - Raw 写入后不可变（写入后再次读取哈希一致）
    - 写入失败不留下半成品文件
    - 幂等：相同输入重复写入安全
  - 验证方式：`pytest tests/storage/ -v`
  - 风险或备注：注意 Windows 上原子替换的文件锁行为；需处理路径穿越

- [x] TASK-005 实现输入发现与合法性检查
  - 里程碑：M1
  - 优先级：P0
  - 状态：已完成
  - 需求依据：PRD FR-01（输入与合法性）、FR-02（格式适配）
  - 目标与产物：
    - `src/book2skill/application/gate.py`：合法性检查用例
    - 文件发现：单文件、多文件、目录、glob
    - 扩展名与内容类型双重识别
    - SHA-256 计算、稳定 source_id 生成
    - 异常处理：不存在、空文件、损坏、加密、超大文件、压缩炸弹
    - 去重：相同哈希去重，同名异哈希创建新版本
    - 单元测试：`tests/application/test_gate.py`（54 个测试）
  - 前置依赖：TASK-003、TASK-004
  - 涉及文件：`src/book2skill/application/gate.py`、`tests/application/test_gate.py`
  - 验收标准：
    - 覆盖 PRD FR-01 所有文件场景
    - 相同哈希文件去重
    - 不存在的文件明确报错
    - 超大文件（>200MB）被拦截
  - 验证方式：`pytest tests/application/test_gate.py -v`（54 passed）
  - 风险或备注：无新依赖；使用标准库 mimetypes + 手工魔数检测；ZIP 炸弹检测基于压缩比（100:1）

- [x] TASK-006 实现 PDF/TXT Adapter
  - 里程碑：M1
  - 优先级：P0
  - 状态：已完成
  - 需求依据：PRD FR-02、FORMAT_ADAPTERS.md
  - 目标与产物：
    - `src/book2skill/extractors/__init__.py`
    - `src/book2skill/extractors/base.py`：Adapter 抽象基类（probe/extract/capabilities/diagnostics）
    - `src/book2skill/extractors/pdf_extractor.py`：PDF 适配器（PyMuPDF + pdftotext/pypdf/pdfminer 多后端）
    - `src/book2skill/extractors/text_extractor.py`：TXT/MD 适配器（BOM + UTF-8/GBK/GB2312 编码探测）
    - `src/book2skill/extractors/registry.py`：格式→适配器注册表
    - 单元测试：`tests/extractors/test_registry.py`、`tests/extractors/test_pdf_extractor.py`、`tests/extractors/test_text_extractor.py`
  - 前置依赖：TASK-003、TASK-005
  - 涉及文件：`src/book2skill/extractors/`、`tests/extractors/`、`pyproject.toml`（mypy overrides）
  - 验收标准：
    - PDF 适配器输出 normalized Markdown + extraction map ✓
    - 页码定位正确（可确定时）✓
    - TXT 编码探测覆盖 UTF-8/GBK/GB2312 ✓
    - 损坏/加密 PDF 明确报错 ✓（GATE_ENCRYPTED_FILE / GATE_DAMAGED_FILE）
    - 扫描无文本 PDF 提示 OCR 回退 ✓
  - 验证方式：`pytest tests/extractors/ -v`（45 passed, 2 skipped）
  - 风险或备注：
    - **非阻塞项 N-03**（已解决）：PyMuPDF 1.28.0 在 Windows 11 上可用
    - 加密 PDF 检测通过 PyMuPDF 的 `is_encrypted` 属性 + pypdf 后备
    - GBK/GB2312 编码通过本地扩展编码链实现（不修改 vendored 代码）
    - 不在此任务中实现 OCR

- [x] TASK-007 实现 Analyze Only 模式
  - 里程碑：M1
  - 优先级：P0
  - 状态：已完成
  - 需求依据：PRD FR-03-1、FR-05、schemas/analysis-bundle.schema.json
  - 目标与产物：
    - `src/book2skill/application/analyze.py`：Analyze Only 用例
    - `src/book2skill/application/models.py`：AnalysisBundle Pydantic 模型
    - `src/book2skill/llm/__init__.py`、`src/book2skill/llm/ports.py`：LLM 抽象接口
    - `src/book2skill/llm/mock_adapter.py`：Mock LLM（规则驱动，用于 M1）
    - `src/book2skill/cli.py` 中实现 `analyze` 命令（含 `--json` 输出）
    - `src/book2skill/domain/models.py` 新增 `TextBlock` 模型
    - `src/book2skill/extractors/base.py` 新增 `extract_text_blocks` 抽象方法
    - 6 个 extractor 重构实现 `extract_text_blocks`
    - 单元测试：`tests/application/test_analyze.py`（20 个测试）
  - 前置依赖：TASK-004、TASK-005、TASK-006
  - 涉及文件：`src/book2skill/application/`、`src/book2skill/llm/`、`src/book2skill/cli.py`、`src/book2skill/domain/models.py`、`src/book2skill/extractors/`、`tests/application/test_analyze.py`
  - 验收标准：
    - `book2skill analyze <pdf>` 输出 AnalysisBundle（JSON）✓
    - AnalysisBundle 包含 structure、candidate_units、review_queue ✓
    - 不生成最终 Skill ✓
    - 输出标记低置信项和人工确认项 ✓
  - 验证方式：`pytest tests/application/test_analyze.py -v`（20 passed）；CLI `book2skill analyze <file> --json` 实际运行通过
  - 风险或备注：
    - **非阻塞项 N-02**（已缓解）：M1 使用 MockLLMAdapter 规则驱动；真正 LLM 在 M3 完善
    - Mock 适配器使用关键词启发式检测 principle/technique/term/case
    - 冲突检测基于候选内容 SHA-256 去重

### M2 任务

- [x] TASK-008 实现 EPUB/DOCX/Markdown Adapter
  - 里程碑：M2
  - 优先级：P0
  - 状态：已完成（2026-07-29）
  - 需求依据：PRD FR-02、FORMAT_ADAPTERS.md
  - 目标与产物：
    - `src/book2skill/extractors/epub_extractor.py`：EPUB 适配器 ✓ 已存在
    - `src/book2skill/extractors/docx_extractor.py`：DOCX 适配器 ✓ 已存在
    - `src/book2skill/extractors/text_extractor.py`：TXT/MD 适配器 ✓ 已存在
    - **本任务实际需补**：验收标准级别的边界测试 fixtures（损坏 EPUB、表格 DOCX、代码块 MD）、部分成功报告、Markdown 路径穿越防护验证
    - 测试 fixtures：EPUB/DOCX/MD 边界样本
    - 单元测试：`tests/extractors/` 扩充
  - 前置依赖：TASK-006
  - 涉及文件：`tests/fixtures/`、`tests/extractors/test_epub_extractor.py`、`tests/extractors/test_docx_extractor.py`、`tests/extractors/test_text_extractor.py`
  - 验收标准：
    - EPUB 保留 spine、章节、元数据
    - DOCX 保留标题、段落、表格
    - Markdown 保留标题、列表、代码块
    - 损坏文件输出部分成功报告
    - Markdown 防路径穿越
  - 验证方式：`pytest tests/extractors/ -v`（74 passed, 2 skipped）；`pytest tests/ -v`（175 passed, 2 skipped，覆盖率 87%）；`ruff check`/`mypy src/`/`check_provenance.py` 全部通过
  - 风险或备注：EPUB 的 ebooklib 依赖已在 pyproject.toml 中列为可选依赖；本任务实际交付为 22 个验收级边界用例（EPUB spine 顺序/单章/非 zip/无 OPF/部分成功/probe/diagnostics；DOCX 表格/混合文档顺序/bad zip/空 body/probe/diagnostics；MD 标题/列表/代码块/路径穿越/空文件/扩展变体；HTML 标题列表/diagnostics/probe），沿用 tmp_path 动态构造 fixtures 模式（不建独立 fixtures 目录，避免版权/可移植性问题，与现有 extractor 测试风格一致）；spine 顺序与混合段落表格顺序测试用 monkeypatch 强制 zipfile 后端以确保断言确定性（ebooklib/python-docx 后端顺序不保证）

- [x] TASK-009 实现 MOBI/AZW 可选 Adapter
  - 里程碑：M2
  - 优先级：P1
  - 状态：已完成（2026-07-29）
  - 需求依据：PRD FR-02、FORMAT_ADAPTERS.md
  - 目标与产物：
    - `src/book2skill/extractors/mobi_extractor.py`：MOBI/AZW 适配器 ✓ 已存在，本任务补 DRM 检测与错误码分类
    - Calibre CLI 包装器 ✓ 已存在（vendored `calibre.py`）
    - 降级逻辑：未安装时提供离线安装说明 ✓（`EXTRACT_UNSUPPORTED` + recovery 含 Calibre 安装指引）
    - DRM 检测：疑似 DRM 时停止 ✓（PalmDOC encryption-type 字段预检，非 0 即拒绝，`GATE_ENCRYPTED_FILE`，不绕过 DRM）
    - 测试 fixtures：合成 PDB（DRM/非 DRM）+ monkeypatch 环境无关用例 + Calibre 真实往返
  - 前置依赖：TASK-006
  - 涉及文件：`src/book2skill/extractors/mobi_extractor.py`、`tests/extractors/test_mobi_extractor.py`
  - 验收标准：
    - Calibre 可用时成功转换 ✓（round-trip 真实通过，DRM 预检不误判 Calibre 生成的无 DRM MOBI）
    - Calibre 不可用时输出清晰安装说明 ✓
    - 疑似 DRM 文件明确拒绝 ✓（合成 DRM PDB → `GATE_ENCRYPTED_FILE`）
  - 验证方式：`pytest tests/extractors/test_mobi_extractor.py -v`（7 passed, 1 skipped）；`pytest`（197 passed, 1 skipped，覆盖率 88%）；`ruff check`/`mypy src/`（27 源文件）/`check_provenance.py` 全部通过；CLI `book2skill batch <drm.mobi> <norm.txt>` 端到端：DRM 文件被拒绝（failed, GATE_ENCRYPTED_FILE），正常文件成功，失败隔离工作
  - 风险或备注：
    - **非阻塞项 N-01**（已解决）：Calibre CLI 9.11.0 已装；降级路径用 monkeypatch 在无 Calibre 环境也可测试
    - 未修改 vendored `calibre.py`（DRM 检测与错误分类全部在 MobiExtractor 层），provenance 无需变更
    - DRM 检测基于 MOBI6 PalmDOC encryption-type 字段；AZW3/KF8 的 Amazon 现代 DRM 不在此字段，仅靠此启发式可能漏检——保守起见 Calibre 转换失败仍归为 `GATE_DAMAGED_FILE`
    - 已知瑕疵（任务外）：`AnalyzeUseCase` 将 `DomainError.__str__`（含 recovery）塞入 `GateError.message`，CLI 再追加 `(recovery: ...)` 导致 recovery 重复显示——影响 `analyze`/`batch` 失败清单可读性，非本任务引入，待后续统一修复

- [x] TASK-010 实现批处理编排
  - 里程碑：M2
  - 优先级：P0
  - 状态：已完成（2026-07-29）
  - 需求依据：PRD FR-02（批处理部分成功）、PRD NFR（10 本书批处理）
  - 目标与产物：
    - `src/book2skill/application/batch.py`：批处理编排器 ✓
    - 单文件失败不影响其他文件 ✓
    - 失败清单输出 ✓
    - 进度报告 ✓（可选 `on_progress` 回调）
    - 单元测试：`tests/application/test_batch.py`（16 个测试）✓
  - 前置依赖：TASK-005、TASK-006
  - 涉及文件：`src/book2skill/application/batch.py`、`src/book2skill/application/models.py`、`src/book2skill/cli.py`、`tests/application/test_batch.py`
  - 验收标准：
    - 批处理中单文件损坏不导致整体失败 ✓
    - 失败清单包含文件路径、错误码、恢复建议 ✓（FailureRecord：path/code/message/recovery）
    - 进度信息可读 ✓
  - 验证方式：`pytest tests/application/test_batch.py -v`（16 passed）；`pytest`（191 passed, 2 skipped，覆盖率 88%）；`ruff check`/`mypy src/`（27 源文件）/`check_provenance.py` 全部通过；CLI `book2skill batch <dir> --json` 与人可读输出实际运行通过，损坏 epub 被隔离跳过（skipped）而其余成功
  - 风险或备注：P1 并发不在本任务范围；逐文件调用 `AnalyzeUseCase.execute([path])` 复用现有 DomainError 隔离，接受单文件 discover 的 hash 冗余（10 本书量级可接受）；BatchResult 为 application 层模型未入 schemas/（非跨宿主契约）；新增 `FailureRecord` Pydantic 模型替代 frozen dataclass `GateError` 以保证 `model_dump(mode="json")` 可序列化

### M3 任务

- [x] TASK-011 实现 KnowledgeUnit 与 Schema 层
  - 里程碑：M3
  - 优先级：P0
  - 状态：已完成（2026-07-29）
  - 需求依据：PRD FR-05、FR-06、DATA_MODEL.md、schemas/knowledge-unit.schema.json
  - 目标与产物：
    - `src/book2skill/domain/knowledge.py`：KnowledgeUnit、ConflictRecord、ReviewItem 模型
    - `src/book2skill/storage/schema_storage.py`：Schema 层读写（collection.json、units.jsonl、frameworks.jsonl 等）
    - 知识聚类、冲突检测、版本管理
    - 单元测试：`tests/domain/test_knowledge.py`、`tests/storage/test_schema_storage.py`
  - 前置依赖：TASK-003、TASK-004
  - 涉及文件：`src/book2skill/domain/knowledge.py`、`src/book2skill/storage/schema_storage.py`、`tests/`
  - 验收标准：
    - KnowledgeUnit 必须包含 unit_id、kind、content、source_refs、review_status
    - 同义方法可聚类但保留来源特有表达
    - 冲突观点并列，不自动裁决
    - 版本更正创建新记录，不原地擦除
  - 验证方式：`pytest tests/domain/test_knowledge.py tests/storage/test_schema_storage.py -v`（33 passed）；`pytest`（230 passed, 1 skipped，覆盖率 89%）；`ruff check`/`mypy src/`（29 源文件）/`check_provenance.py` 全部通过
  - 风险或备注：聚类/冲突检测为规则基础版（Jaccard token 重叠 + 否定标记启发式），P1 可换 LLM 辅助；`ConflictRecord`/`ReviewItem` 已从 application 统一到 domain（超集，新增可选 `conditions`/`disposition`/`reviewer`），analysis-bundle schema 宽松故无回归；`frameworks.jsonl` 由 `load_by_kind` 派生而非单独双写以避免分歧；版本管理 append-only，`supersede_unit` 不擦除历史行

- [x] TASK-012 实现 SkillIR 编译器
  - 里程碑：M3
  - 优先级：P0
  - 状态：已完成（2026-07-29）
  - 需求依据：PRD FR-04、schemas/skill-ir.schema.json、SKILL_AUTHORING_STANDARD.md
  - 目标与产物：
    - `src/book2skill/compiler/__init__.py`：公开导出
    - `src/book2skill/compiler/ir_builder.py`：SkillIR/SkillSpec/WorkflowStep Pydantic 模型 + IRBuilder（按 kind 分组 units → workflow 步骤 + references 下沉）+ validate_skill_ir_against_schema（测试用 jsonschema）
    - `src/book2skill/compiler/skill_writer.py`：SkillWriter（模板渲染 SKILL.md + 写 references/assets/provenance.yml/quality-report.md + token 预算门 + 路径穿越防护）
    - `src/book2skill/compiler/token_budget.py`：TokenBudget（2500–5000 默认）+ estimate_tokens（启发式 CJK ~1.5 字符/token、Latin ~4 字符/token）+ check_budget
    - 使用 `templates/generated-skill/` 模板
    - 单元测试：`tests/compiler/`（52 用例：token_budget 13 + ir_builder 22 + skill_writer 17）
  - 前置依赖：TASK-011
  - 涉及文件：`src/book2skill/compiler/`、`src/book2skill/domain/errors.py`（+2 错误码）、`tests/compiler/`、`pyproject.toml`（mypy overrides 加 jsonschema）
  - 验收标准：
    - SkillIR 通过 schema 校验 ✓（Pydantic 运行时 + jsonschema 测试一致性验证）
    - 生成 Skill 目录包含 SKILL.md、references/、assets/ ✓
    - name 使用小写字母、数字和连字符 ✓（Pydantic pattern 强制）
    - description 包含"做什么、何时用、何时不用" ✓（模板 Use when/Do not use when 段）
    - 主文件在 2,500–5,000 tokens 目标范围内 ✓（token_budget 门，超 hard max 抛 BUILD_BUDGET_EXCEEDED）
    - 细节按需下沉到 references/ ✓（framework/principle → workflow；technique/case/term/anti_pattern/checklist/decision_rule → references/<kind>.md）
  - 验证方式：`pytest tests/compiler/ -v`（52 passed）；`pytest`（282 passed, 1 skipped，覆盖率 89%）；`ruff check`/`mypy src/`（33 源文件）/`check_provenance.py` 全部通过
  - 风险或备注：
    - **实施中验证项 V-02**：token 估算为确定性启发式（CJK ~1.5 字符/token、Latin ~4 字符/token），与官方 tokenizer 存在偏差（约 ±15%），M5 阶段由官方校验器校准；估算器为纯函数，可替换为 tiktoken
    - provenance.yml 中 title/author/edition/content_sha256 标注 "unknown"，由 TASK-013 的 build 用例联接 SourceManifest 填充完整值
    - quality-report.md 为 stub（模板原样写出 + run-id 注入），由 TASK-016 填充实际校验结果
    - 无新增运行时依赖（jsonschema 仅 dev 依赖，运行时由 Pydantic 校验）；mypy overrides 增加 jsonschema 与现有 fitz/pypdf 模式一致
    - SkillWriter 通过 atomic_write 落盘，超 hard max 时不留半成品 SKILL.md（预算检查在写入前）
    - IRBuilder 过滤 rejected/superseded 状态的 unit；无可用 unit 时抛 BUILD_IR_FAILED

- [x] TASK-013 实现 Full Build 与 Build from Analysis
  - 里程碑：M3
  - 优先级：P0
  - 状态：已完成（2026-07-29）
  - 需求依据：PRD FR-03-2、FR-03-3
  - 目标与产物：
    - `src/book2skill/application/build.py`：BuildUseCase（Full Build `build_from_sources` + Build from Analysis `build_from_bundle`）+ BuildResult + `_candidate_to_unit` 转换
    - `src/book2skill/compiler/skill_writer.py`：SkillWriter.write 新增 `source_manifests` 参数，`_render_provenance` 优先使用 manifest 填充真实 `content_sha256`/`title`/`format`/`ingested_at`，回退 `source_ids` 标 unknown，向后兼容；新增 `_yaml_escape` 处理含冒号等特殊字符的 title
    - `src/book2skill/domain/errors.py`：新增 `BUILD_INPUT_INVALID` 错误码
    - `src/book2skill/cli.py`：新增 `build` 命令（`--from-analysis`/`--name`/`--description`/`--use-when`/`--no-use-when`/`--output-dir`/`--data-home`/`--rights-note`/`--json`）
    - 单元测试：`tests/application/test_build.py`（23 用例：Full Build 10 + Build from Analysis 6 + CLI 5 + BuildResult 2）+ `tests/compiler/test_skill_writer.py` 新增 `TestProvenanceWithManifests` 7 用例
    - 不含 Wiki 层 chapters/glossary/patterns/cheatsheet（PRD FR-04 仅要求"至少含 SKILL.md，可含 references/assets"；TODO.md 第 399 行的 Wiki 层扩展作为 backlog，由后续任务承接）
  - 前置依赖：TASK-007、TASK-012（均已满足）
  - 涉及文件：`src/book2skill/application/build.py`、`src/book2skill/compiler/skill_writer.py`、`src/book2skill/domain/errors.py`、`src/book2skill/cli.py`、`tests/application/test_build.py`、`tests/compiler/test_skill_writer.py`
  - 验收标准：
    - Full Build 从 Raw 到 Schema 再编译完整 Skill ✓
    - Build from Analysis 基于已有分析跳过重复抽取 ✓
    - 产物通过结构校验 ✓（SKILL.md/references/assets/provenance.yml/quality-report.md）
    - 来源可追溯 ✓（provenance.yml 含真实 content_sha256 当 data_home 可用）
  - 验证方式：`book2skill build <txt> --name x --description ... --use-when ... --data-home <dir>`；`book2skill build --from-analysis bundle.json ...`；`pytest tests/application/test_build.py -v`
  - 风险或备注：
    - LLM Adapter 仍为 Mock（N-02 已缓解）；用户可在 build 前编辑 AnalysisBundle.json 实现"人工审核"，真正 LLM 在 M3 后期升级
    - SourceManifest 不持有 title/author/edition 字段：provenance.yml 中 `content_sha256`/`title`（回退 original_name/source_id）/`format`/`ingested_at` 真实填充，`author`/`edition` 仍标 unknown（设计约束，需 ingester 上传时可选 metadata 增强）
    - Build from Analysis 模式无 RawStorage 句柄，provenance 回退 unknown stub（用户可后续手动编辑）
    - 312 passed/1 skipped（282 + 30 新增），覆盖率 89%，ruff/mypy 34 源文件/check_provenance 全部通过，CLI 端到端实测通过

### M4 任务

- [x] TASK-014 实现差异引擎与冲突检测
  - 里程碑：M4
  - 优先级：P0
  - 状态：已完成（2026-07-29）
  - 需求依据：PRD FR-03-4、FR-06、ARCHITECTURE.md 关键设计决策
  - 目标与产物：
    - `src/book2skill/application/diff.py`：DiffEngine（diff + merge_with_overrides 纯函数引擎）+ Override/OverrideField/UnitChange/DiffResult/MergeConflict/MergeResult 模型 + bundle_to_units + load_diff_input（双输入解析）
    - `src/book2skill/storage/override_storage.py`：OverrideStorage（append-only overrides.jsonl，save_override/load_overrides/load_active_overrides/load_for_unit/supersede_override，按 (unit_id, field) 去重 + override_id 链式 supersede 追踪）
    - `src/book2skill/domain/errors.py`：新增 DIFF_INPUT_INVALID、MERGE_CONFLICT_UNRESOLVABLE 错误码
    - `src/book2skill/cli.py`：新增 `diff` 命令（支持 collection_id 和 bundle.json 双输入、--data-home、--merge、--json）
    - 单元测试：`tests/application/test_diff.py`（39 用例：DiffEngine.diff 11 + merge_with_overrides 13 + Override 校验 5 + bundle 转换 6 + CLI 4）+ `tests/storage/test_override_storage.py`（11 用例）
  - 前置依赖：TASK-011、TASK-013（均已满足）
  - 涉及文件：`src/book2skill/application/diff.py`、`src/book2skill/storage/override_storage.py`、`src/book2skill/domain/errors.py`、`src/book2skill/cli.py`、`tests/application/test_diff.py`、`tests/storage/test_override_storage.py`
  - 验收标准：
    - 同书不同版本不静默合并 ✓（字段级 3-way merge，同字段双修标为 unresolvable）
    - 冲突观点并列，记录适用前提 ✓（MergeConflict 记录 human_value/generator_value，不自动裁决）
    - 人工 override 在更新时保留 ✓（ours 优先；removed/added 单元的 override 进 preserved_overrides）
    - diff 输出可读 ✓（CLI 可读报告 + JSON 输出）
  - 验证方式：`book2skill diff <old.json> <new.json> [--json] [--merge --data-home <dir>]`；`pytest tests/application/test_diff.py tests/storage/test_override_storage.py -v`
  - 风险或备注：
    - 三方合并不做文本级 patch，按整字段值比较（content 作为原子陈述，非代码文本）
    - MergeConflict 为单单元字段冲突模型，与 ConflictRecord（两单元间冲突）语义分离
    - override 的 value 类型多态由 Override.validate_value 校验
    - 不实现 override 的 CLI 编辑命令（由 TASK-015 Update 用例或后续 review CLI 承接）
    - 362 passed/1 skipped（312 + 50 新增），覆盖率 90%，ruff/mypy 36 源文件/check_provenance 全部通过，CLI 端到端实测通过

- [x] TASK-015 实现 Update 模式与原子发布
  - 里程碑：M4
  - 优先级：P0
  - 状态：已完成（2026-07-29）
  - 需求依据：PRD FR-03-4、FR-10、ARCHITECTURE.md 编译阶段 9
  - 目标与产物：
    - `src/book2skill/application/update.py`：Update/Fold-in 用例（plan 纯读 diff+merge；execute 确认后发布；rollback 委托 publisher）
    - `src/book2skill/application/publisher.py`：Publisher（原子发布、快照、回滚、index.md、publish-log.jsonl）+ SkillMeta/PublishLogEntry/PublishRecord + load_skill_meta
    - `src/book2skill/cli.py`：新增 `update` 命令（`--data-home`、`--collection-id`、`--name/--description/--use-when`、`--confirm`、`--rollback`、`--json`）
    - `src/book2skill/application/build.py`：`_compile_bundle` 追加写 `skill.meta.json`（供 update 回读 collection_id + spec）
    - `src/book2skill/domain/errors.py`：新增 `PUBLISH_FAILED`、`PUBLISH_ROLLBACK_FAILED`
    - `src/book2skill/storage/__init__.py`：导出 `OverrideStorage`
    - 单元测试：`tests/application/test_update.py`（18 用例）、`tests/application/test_publisher.py`（13 用例）
  - 前置依赖：TASK-014
  - 涉及文件：`src/book2skill/application/update.py`、`src/book2skill/application/publisher.py`、`src/book2skill/cli.py`、`src/book2skill/application/build.py`、`src/book2skill/domain/errors.py`、`src/book2skill/storage/__init__.py`、`tests/application/test_update.py`、`tests/application/test_publisher.py`
  - 验收标准：
    - Update 输出新增/修改/冲突/废弃建议 ✓（复用 DiffEngine.diff + merge_with_overrides）
    - 确认后原子更新 ✓（staging 编译 → 快照旧树 → os.replace 替换）
    - 保留回滚快照 ✓（snapshots/<name>/<ts>/）
    - 失败不破坏已发布版本 ✓（编译失败不触碰已发布；快照后失败自动回滚）
    - 操作幂等 ✓（旧侧 active view 排除 superseded/rejected；无变更时 no_changes，不产快照/日志）
  - 验证方式：`pytest tests/application/test_update.py tests/application/test_publisher.py -v`（31 passed）；`pytest`（387 passed, 1 skipped，覆盖率 89%）；`ruff check`/`mypy src/`（38 源文件）/`check_provenance.py` 全部通过；CLI 端到端实测：build → update dry-run（输出差异）→ update --confirm（published=true，生成 snapshot）→ 相同输入二次 --confirm（no_changes，无新快照）→ update --rollback（恢复旧版本）全部通过
  - 风险或备注：
    - Windows 目录原子替换：依赖"替换时目标已移走"不变量；staging/snapshot/skill 同卷（`data_home` 下）避免跨设备 rename；Windows 11 实测通过
    - provenance.yml 不含 collection_id（Explore 确认），故新增 `skill.meta.json`（per-skill 元文件，非 schemas/ 跨宿主契约，与 BatchResult 同属 application 产物）携带 collection_id + spec；本任务前用旧 build 产出的 skill 无此文件，update 要求显式 `--collection-id` + 形状参数兼容回退
    - 默认 dry-run，`--confirm` 才发布；`--rollback` 走快照恢复，不做 diff
    - `wiki/index.md` 为 publish-log.jsonl 的派生视图（latest per skill），避免双写分歧；build-only skill 不入 index（update --confirm 才入），属 P0 已知范围
    - removed 单元标记为 superseded（append-only，不擦除历史）；modified 单元 supersede_unit bump 版本；unchanged 跳过避免重复版本记录
    - 时间戳 `datetime.now(UTC)`；测试用注入 clock 保证快照目录名唯一可确定

### M5 任务

- [x] TASK-016 实现安全与质量校验器
  - 里程碑：M5
  - 优先级：P0
  - 状态：已完成（2026-07-29）
  - 需求依据：PRD FR-07、FR-08、SECURITY.md、schemas/quality-report.schema.json
  - 目标与产物：
    - `src/book2skill/validation/__init__.py`：公开导出
    - `src/book2skill/validation/models.py`：CheckStatus/ReportStatus/Finding/CheckResult/BaseCheck/QualityReport（符合 schemas/quality-report.schema.json）
    - `src/book2skill/validation/frontmatter_check.py`：frontmatter 校验（name 模式 + description 长度）
    - `src/book2skill/validation/source_check.py`：来源覆盖率、孤立引用、重复 ID、缺失 reference 链接
    - `src/book2skill/validation/copyright_check.py`：长引文检测（25 软上限 warn/40 硬上限 fail，中英混合等量词数计算）
    - `src/book2skill/validation/injection_check.py`：注入文本（中英文短语）、隐藏字符（ZWSP/BOM/RTL/C0-C1）、可疑 URL（非 https/内网 IP/data:/javascript:）、路径穿越（../ 与绝对路径 + resolve_within）
    - `src/book2skill/validation/budget_check.py`：复用 token_budget.check_budget
    - `src/book2skill/validation/quality_report.py`：Validator 编排 + QualityReportWriter（Markdown + JSON）
    - `src/book2skill/cli.py`：新增 `validate` 命令（`--json`/`--write`/`--max-quote-words`，退出码 fail→1）
    - 单元测试：`tests/validation/`（53 用例：frontmatter 6 + source 7 + copyright 9 + injection 13 + budget 4 + quality_report 8 + validator_cli 6）
  - 前置依赖：TASK-013（已满足）
  - 涉及文件：`src/book2skill/validation/`（8 个新文件）、`src/book2skill/domain/errors.py`（+2 错误码 VALIDATE_INPUT_INVALID/VALIDATE_SKILL_DIR_INVALID）、`src/book2skill/cli.py`、`tests/validation/`（8 个新文件）
  - 验收标准：
    - 注入文本被识别 ✓（中英文短语命中 → fail）
    - 隐藏字符被报告 ✓（ZWSP/BOM/RTL/C0-C1 → fail）
    - 可疑 URL 被标记 ✓（非 https → warn；data:/javascript: → fail；内网 IP → warn）
    - 路径穿越被拦截 ✓（../ 与绝对路径 → fail；resolve_within 终检）
    - 长引文（>25 词或等量中文）被标记 ✓（25 软上限 warn，40 硬上限 fail，1.5 字符/词折算中文）
    - 校验失败不覆盖已发布版本 ✓（validate 默认只读，--write 仅写 quality-report.md/json；SKILL.md/references/provenance.yml 不动；CLI 端到端实测确认 mtime 未变）
    - 输出 machine-readable 和 Markdown quality-report ✓（--json 输出符合 schema 的 JSON；--write 写 quality-report.md/json；Markdown 表格匹配模板结构）
  - 验证方式：`pytest tests/validation/ -v`（53 passed）；`pytest`（440 passed, 1 skipped，覆盖率 90%）；`ruff check`/`mypy src/`（46 源文件）/`check_provenance.py` 全部通过；CLI 端到端实测：`book2skill build <txt>` → `book2skill validate <skill-dir>`（pass_with_warnings）→ `validate --json --write`（生成 quality-report.md/json，核心文件 mtime 未变）→ `validate <injection-skill>`（fail，退出码 1，未写任何文件）全部通过
  - 风险或备注：
    - **实施中验证项 V-03**（已验证）：中文引文长度采用 1.5 字符/词折算（与 token_budget 启发式一致），25 英文词等量 = 25 词或约 38 中文字符；阈值通过 `--max-quote-words` 可配置；M6 端到端验收时校准
    - 注入检测为基础版（中英文规则匹配），需持续更新（TODO L504 已标注）
    - validate 默认只读，不嵌入 build 流程（P1 可考虑 `--validate` 集成）；--write 仅覆写 quality-report.md/json，不触碰 skill 核心文件
    - 复用现有组件：pyyaml（已是依赖）、token_budget.check_budget、resolve_within、atomic_write、ErrorCode 体系、_is_cjk；无新增运行时依赖
    - links 检查合并：无效 reference 文件链接归 SourceCheck（links.missing_reference），路径穿越归 InjectionCheck，避免超出 TODO 文件清单
    - QualityReport 不使用 use_enum_values=True（保持 status 为枚举成员，.value 可用；model_dump_json 仍序列化为 schema 字符串）

- [x] TASK-017 实现多宿主安装器
  - 里程碑：M5
  - 优先级：P0
  - 状态：已完成（2026-07-29）
  - 需求依据：PRD FR-04、SKILL_DEPLOYMENT.md
  - 目标与产物：
    - `src/book2skill/hosts/__init__.py`：公开导出
    - `src/book2skill/hosts/base.py`：HostInstaller 抽象基类 + InstallRecord + parse_skill_name
    - `src/book2skill/hosts/claude.py`：Claude Code 安装器（个人级 `~/.claude/skills/` + 项目级 `.claude/skills/`）
    - `src/book2skill/hosts/trae.py`：TRAE 安装器（项目级 `.trae/skills/`，忽略 project_level）
    - `src/book2skill/hosts/codex.py`：Codex/OpenAI 安装器（个人级 `~/.agents/skills/` + 项目级 `.agents/skills/` + `agents.md` overlay）
    - `src/book2skill/hosts/project.py`：通用项目级安装器（默认 `skills/` 子目录，可配 `--target-dir`）
    - `src/book2skill/hosts/registry.py`：`get_installer(host)` 工厂 + `HOST_KINDS` 常量
    - `src/book2skill/cli.py`：新增 `install` 和 `uninstall` 命令（含 `--host`/`--project-level`/`--project-root`/`--backup-root`/`--target-dir`/`--dry-run`/`--no-backup`/`--json`）
    - 单元测试：`tests/hosts/`（64 用例：base 22 + installers 13 + registry 8 + cli 21）
  - 前置依赖：TASK-013（已满足）
  - 涉及文件：`src/book2skill/hosts/`（7 个新文件）、`src/book2skill/domain/errors.py`（+3 错误码 INSTALL_FAILED/INSTALL_SKILL_DIR_INVALID/UNINSTALL_FAILED）、`src/book2skill/cli.py`（+2 命令）、`tests/hosts/`（4 个新文件）
  - 验收标准：
    - `install --host claude` 复制到 `~/.claude/skills/` 或 `.claude/skills/` ✓
    - `install --host trae` 复制到 `.trae/skills/` ✓
    - `install --host codex` 复制到 `~/.agents/skills/` ✓
    - `install --host project` 复制到项目目录 ✓
    - 脚本路径不依赖仓库绝对路径 ✓（使用 Path.home() / Path.cwd() / --project-root）
    - 升级前备份旧版本 ✓（时间戳备份目录 `<backup_root>/<host>/<skill>/<ts>/`，默认 `~/.book2skill/backups/`）
    - 卸载只删除 Skill 安装目录 ✓（uninstall 不触碰 backups/workspace/raw）
  - 验证方式：`pytest tests/hosts/ -v`（64 passed）；`pytest`（504 passed/1 skipped，覆盖率 90%）；`ruff check`/`mypy src/`（53 源文件）/`check_provenance.py` 全部通过；CLI 端到端实测：`book2skill install <skill> --host project`（installed 1 file）→ `install --host codex --project-level --dry-run`（no changes）→ `uninstall <name> --host project`（uninstalled）全部通过
  - 风险或备注：
    - 跨平台路径处理：使用 `Path.home()` 与 `Path.cwd()` 而非手工 `~` 展开；os.replace 目录原子替换依赖"目标不存在"不变量（与 Publisher 一致），Windows 11 实测通过
    - 安装原子性：新树先复制到 `<target>/.<name>.staging-<ts>/` 暂存目录，再 `os.replace` 替换；overlay 失败时自动恢复备份（best-effort，不抛二错），与 Publisher 回滚模式一致
    - 备份目录默认放 `~/.book2skill/backups/` 而非 workspace 下，避免 workspace 清理时丢失回滚入口
    - Skill 名称解析从 SKILL.md frontmatter `name` 字段读取（与 frontmatter_check 同模式），保证目录名与 slug 一致，而非依赖源目录名
    - Codex overlay 每次重写 `agents.md`，避免上一版本残留陈旧索引；其他 host 无 overlay（_apply_overlay 默认 no-op，B027 已 noqa）
    - ProjectInstaller 拒绝绝对 `target_dir`（POSIX `/` 前缀 + Windows 驱动器号），并通过 `_resolve_project_target`/`resolve_within` 终检路径穿越
    - 无新增运行时依赖；复用 pyyaml、resolve_within、atomic_write（间接）、shutil.copy2（保 mtime 可审计）、ErrorCode 体系
    - InstallRecord 为 application 层 frozen dataclass（与 PublishRecord 同模式，非 schemas/ 跨宿主契约）
    - 卸载幂等：uninstall 不存在的 skill 返回成功 no-op，不抛异常（与 update --rollback 的 StorageNotFoundError 行为不同，因为安装目录可能已被人工删除）

### M6 任务

- [x] TASK-018 端到端验收与断网独立性测试
  - 里程碑：M6
  - 优先级：P0
  - 状态：已完成（2026-07-30）
  - 需求依据：ACCEPTANCE_TEST_PLAN.md、PRD MVP 验收、PRD NFR
  - 目标与产物：
    - 测试集 A-F 完整验收（Analyze Only + Full Build）✓
    - 测试集 G（损坏/加密/注入样本）验收 ✓
    - 断网独立性验证 ✓（代码审查 + S6 env 隔离实测）
    - 全量追踪矩阵更新 ✓
    - 验收证据报告 ✓（`docs/M6_ACCEPTANCE_REPORT.md`）
    - Gitee 私有仓库首次推送 ✓（提供命令清单 + `scripts/pre_push_scan.py`，用户亲自执行）
  - 前置依赖：TASK-001 ~ TASK-017
  - 涉及文件：`tests/fixtures/acceptance/__init__.py`（A-G 样本生成器）、`scripts/run_acceptance.py`（自动化验收脚本）、`scripts/pre_push_scan.py`（推送前敏感扫描）、`docs/M6_ACCEPTANCE_REPORT.md`、`TRACEABILITY_MATRIX.md`、`TODO.md`、`.gitignore`
  - 验收标准：
    - PRD MVP 10 项验收全部通过 ✓
    - 断网后核心流程可运行 ✓
    - 来源台账、许可证和致谢完整 ✓
    - 追踪矩阵全覆盖 ✓
  - 验证方式：`python scripts/run_acceptance.py`（10 步：9 PASS + 1 预期 FAIL=injection）；`.venv\Scripts\pytest`（504 passed/1 skipped，90% 覆盖率）；`.venv\Scripts\ruff check src/ tests/ scripts/`（All checks passed）；`.venv\Scripts\mypy src/`（53 源文件 Success）；`python scripts\check_provenance.py`（8 文件 PASS）
  - 风险或备注：
    - **非阻塞项 N-04**（已解决）：用合成样本（无版权风险），存于 `tests/fixtures/acceptance/`；A/B 用字节模板 PDF，C 用 stdlib zipfile EPUB，D 用 stdlib zipfile DOCX，E 直接写文本，F 用 Calibre 转换（Calibre 拒绝最小 TXT 时降级，DRM 路径由 G2 验证）
    - 断网独立性：src/ 唯一联网导入是 `urllib.parse.urlparse`（仅 URL 解析，不发起请求）；LLM 包纯 Protocol + 规则驱动 Mock，无 openai import；8 个 vendored 文件本地化（PROVENANCE.yml PASS）；S6 offline-smoke（清除 proxy env）通过；Windows 防火墙阻断实测需管理员权限未执行，记为已知限制
    - 验收脚本通过 subprocess 调用真实 CLI（优先用 `.venv/Scripts/book2skill.exe`），证据含每步 stdout/stderr/exit_code/duration，归档于 `workspace/acceptance/runs/<ts>/`（已 gitignore）
    - 已知限制：SKILL.md 模板硬编码 `references/provenance.md` 链接导致 source_check warn（非安全风险）；跨平台仅 Windows 11 实测
    - Gitee 推送：用户亲自执行 `git init` → `pre_push_scan.py` → `git add` → `git commit` → `git remote add` → `git push`；pre_push_scan.py 检查路径模式（.env/workspace/*.pdf 等）+ 内容标记（PRIVATE KEY/sk-/ghp_ 等）


### 持续/横切任务

- [x] TASK-019 更新元 Skill `book2skill`
  - 里程碑：M3（首次）、M6（最终）
  - 优先级：P1
  - 状态：已完成（2026-07-30）
  - 需求依据：PRD 项目概述、SKILL_AUTHORING_STANDARD.md、SKILL_DEPLOYMENT.md、ARCHITECTURE.md §2
  - 目标与产物：
    - `skills/book2skill/SKILL.md`：元 Skill 主文件（frontmatter + Use when / Do not use when / Required inputs / Workflow / Output contract / Routing decision tree / Examples / Evidence and limitations）✓
    - `skills/book2skill/references/commands.md`：详细 CLI 命令参考（8 命令完整签名、flags、失败码、恢复建议）✓
    - `skills/book2skill/references/workflow.md`：四种模式工作流与决策树、数据分层铁律、override/三方合并、原子发布与回滚、离线降级 ✓
    - `skills/book2skill/references/deployment.md`：多宿主部署步骤（Claude/TRAE/Codex/Project/ChatGPT 回退）、升级备份、卸载、冒烟测试、跨平台 ✓
    - `skills/book2skill/references/provenance.md`：元 Skill 来源与自举说明（手写产物、自举状态、校验状态、已知限制、Sources cited）✓
    - `skills/book2skill/assets/example-config.yaml`：用户起步配置模板 ✓
    - `skills/book2skill/scripts/smoke_test.py`：部署后冒烟测试（stdlib 实现，.venv 优先解析 CLI，Analyze Only 验证）✓
    - `skills/book2skill/provenance.yml`：元 Skill 来源台账（5 设计文档 source_id，review_status: hand-authored）✓
    - `skills/book2skill/quality-report.md` + `quality-report.json`：validate --write 产物 ✓
  - 前置依赖：TASK-013（已满足）
  - 涉及文件：`skills/book2skill/`（10 个新文件）
  - 验收标准：
    - 元 Skill 可被宿主发现和调用 ✓
    - 遵循 SKILL_AUTHORING_STANDARD.md 8 条标准（目录名与 name 一致、description 前置触发词与不适用范围、主文件只放工作流/边界/路由/合同、详细方法下沉 references/、模板放 assets/、确定性动作放 scripts/、跨宿主核心不写死路径、生成前后跑校验器）✓
    - 元 Skill 自身通过本项目验证器 ✓
  - 验证方式：
    - `book2skill validate skills/book2skill --write`：status=pass，5/5 checks pass（frontmatter/source-coverage/copyright/injection/budget 全 PASS，无警告）；
    - `book2skill install skills/book2skill --host project --dry-run --json`：skill_name=book2skill 解析正确，target_dir 解析正确，dry_run=true；
    - `python skills/book2skill/scripts/smoke_test.py`：PASS（CLI 经 `python -m book2skill.cli` 发现，analyze 成功，collection_id=col-xxx，candidate_units=1）；
    - `.venv\Scripts\ruff check skills/book2skill/scripts/smoke_test.py`：All checks passed；
    - `.venv\Scripts\pytest`：504 passed, 1 skipped（与 TASK-018 一致，无回归），覆盖率 90%。
  - 风险或备注：
    - 元 Skill 为**手写产物**（非 Book2Skill 流程编译输出），遵循与生成 Skill 相同的目录结构与编写标准。provenance.yml 中 review_status=hand-authored、content_sha256=hand-authored（非真实哈希，因元 Skill 不经过 Raw 固化流程）。
    - 自举说明：PRD/TODO 备注指出"元 Skill 自身也是 Book2Skill 的产物，体现自举能力"。当前手写实现作为宿主调用 Book2Skill CLI 的入口；P2 可尝试用流程把项目自身文档（PRD/ARCHITECTURE/SKILL_AUTHORING_STANDARD 等）编译为元 Skill 验证自举闭环，但元 Skill 主题是 Book2Skill 自身使用方法而非某本书内容，手写更准确（详见 references/provenance.md）。
    - 编写规避注入检测陷阱：初版 SKILL.md 的 `- ` 列表项含 `/`（如 `框架/原则`、`references/commands.md`）误触 SourceCheck 的 source citation 正则（`- <source_id> / <block_id>`）导致 6 个 undeclared 警告；已通过改用中文顿号、把 Evidence 段落化、为路径项加描述前缀修复，现 validate 全 PASS。
    - 元 Skill 不携带 Book2Skill CLI 可执行文件，部署前需确保目标环境已安装 `book2skill` CLI（`pip install -e .` 或 `uv sync`）；smoke_test.py 仅验证 CLI 可发现与 Analyze Only 可运行，不验证全模式。
    - 跨平台仅 Windows 11 实测；scripts/smoke_test.py 为纯 stdlib 实现，POSIX 路径分支已包含。

## 6. 推荐首批任务
建议首先执行以下 3 项任务：

1. **TASK-001 初始化 Python 项目骨架**（P0）
   - 理由：所有后续任务依赖可运行的 Python 环境和命令。M0 是项目从规格进入实现的唯一入口。
2. **TASK-002 建立许可证与 Provenance 流程**（P0）
   - 理由：阻塞项 B-01——上游代码移植决策必须在编码前完成，否则后续任务无法合法引用上游实现。
3. **TASK-003 实现 Domain 层核心模型**（P0）
   - 理由：Domain 层是架构基础，所有上层模块依赖其数据模型。先建立稳定的数据契约可降低后续返工风险。

## 7. 测试与质量计划
| 测试类型 | 覆盖范围 | 工具 | 执行阶段 |
|---|---|---|---|
| 单元测试 | 所有 domain/application/extractor/storage/compiler/validation/hosts 模块 | pytest | 每个任务内 |
| 集成测试 | 端到端管线：输入→Raw→Schema→Skill→校验 | pytest | M1/M3/M4/M5 末尾 |
| 格式兼容性 | 7 类格式 × 正常/损坏/边界样本 | pytest + fixtures | M1/M2 |
| 安全测试 | 注入、路径穿越、隐藏字符、长引文 | pytest + 恶意样本 | M5 |
| 断网独立性 | 删除上游仓库和网络后核心流程 | 手动 | M6 |
| 跨平台 | Windows 11（首要）、macOS、Linux | 手动/CI | M6 |
| 回滚验证 | 更新→回滚→一致性检查 | pytest | M4 |
| 宿主部署 | Claude Code / TRAE / Codex / ChatGPT dry-run | 手动 | M5 |
| 许可证合规 | 依赖扫描、来源一致性 | scripts/check_provenance.py | M0/M6 |

## 8. 风险登记
| 风险 | 影响 | 缓解措施 | 触发条件 | 对应任务 |
|---|---|---|---|---|
| 上游 LICENSE 不可用或不兼容 | 无法移植代码，需全部自研 | 提前 Commit 级核验；备选自研方案 | 上游仓库删除或改 License | TASK-002 |
| LLM 不可用（离线/无 API Key） | Analyze/Full Build 核心流程受阻 | Mock 适配器 + 规则引擎兜底；本地模型集成 | M1 阶段 LLM 调用失败 | TASK-007、TASK-013 |
| Windows 文件锁/原子操作差异 | 原子写入、回滚可能失败 | 使用临时目录 + 重命名方案；充分测试 Windows 路径 | M1 阶段存储测试失败 | TASK-004、TASK-015 |
| PDF 提取工具在 Windows 不可用 | PDF 适配器无法工作 | 提供多种 PDF 后端选择（PyMuPDF/pdftotext） | M1 阶段 PDF 测试失败 | TASK-006 |
| Calibre CLI 未安装 | MOBI/AZW 格式无法处理 | 降级策略：输出安装说明，不阻塞其他格式 | M2 阶段 MOBI 测试 | TASK-009 |
| 中文引文长度计算不准确 | 版权校验误报/漏报 | 参考行业标准实现中文分词计数；可配置阈值 | M5 阶段校验测试 | TASK-016 |
| Token 计数与宿主不一致 | Skill 预算超标或未充分利用 | 选择与主流宿主一致的 tokenizer；可配置目标值 | M3 阶段编译测试 | TASK-012 |

## 9. 暂不实施事项
- OCR 回退（P1）
- 图片/图表/公式增强（P1）
- 并发处理（P1）
- 断点恢复（P1）
- 本地 OpenAI-compatible 模型自动化（P1）
- 跨宿主回归测试（P1）
- 可视化审核界面（P1）
- HTML/RTF 格式支持（P1）
- GUI（非目标）
- 全文搜索/RAG 替代（非目标）
- 模型训练/微调（非目标）
- 自动执行文档中命令/URL/提示词（非目标）
- P0 阶段引入 SQLite（非目标）

## 10. TODO 维护规则
- 状态更新：任务完成后在 `- [ ]` 改为 `- [x]`，并更新状态字段
- 每次只保留一个"进行中"任务
- 任务完成后补充验证命令和结果到备注
- 新增任务需标注需求依据和里程碑
- 阻塞项解决后更新第 3 节
- 里程碑完成后在对应 `- [ ]` 改为 `- [x]`
- 每周或每里程碑结束时审查一次 TODO 完整性

## 11. 选择性移植进度（virgiliojr94/book-to-skill，Commit 92b248fa）

按 `OPEN_SOURCE_REUSE_POLICY.md` 选择性移植，存放于 `src/book2skill/extractors/_vendor/book_to_skill/`，每次移植均含 provenance 版权头并登记于 `docs/PROVENANCE.yml`。

### 阶段 A（已完成，2026-07-27）
- `sanitize.py` — 不可见码点移除
- `parsers/text.py` → `text.py` — BOM 感知纯文本读取
- `TextExtractor` 包装器 + `tests/extractors/test_text_extractor.py`

### 阶段 B（已完成，2026-07-28）
- `parsers/html.py` → `html.py` + `HtmlExtractor` + 测试（bs4 缺失时走 stdlib fallback）
- `parsers/epub.py` → `epub.py` + `EpubExtractor` + 测试（ebooklib 缺失时走 stdlib zip fallback）
- `exceptions.py` + `parsers/docx.py` → `docx.py` + `DocxExtractor` + 测试（含 XXE/Billion Laughs 校验）
- `parsers/pdf.py` → `pdf.py` + `PdfExtractor` + 测试（pdftotext/pypdf/pdfminer/docling 多后端 + 本地新增 PyMuPDF 后端）
- `parsers/calibre.py` → `calibre.py` + `MobiExtractor` + 测试（Calibre CLI 外部，未安装时清晰降级）

### 验证结果（阶段 B 结束，2026-07-28）
- `pytest`：**48 passed, 2 skipped**（MOBI 含合成往返测试真实通过；1 个需 B2S_MOBI_FIXTURE 的占位测试跳过）
- `ruff check src/ tests/ scripts/`：All checks passed
- `mypy src/`：Success, no issues（vendored 目录已排除）
- `python scripts/check_provenance.py`：PASS，8 个 ported 文件一致
- 覆盖率：85%

### 依赖安装状态（2026-07-28）
- `pdf`：pymupdf 1.28.0 / pypdf 4.3.1 / pdfminer.six ✓
- `epub`：ebooklib 0.20 / beautifulsoup4 4.15.0 ✓
- `docx`：python-docx 1.2.0 ✓
- `html`：beautifulsoup4 4.15.0 ✓
- `mobi`：Calibre CLI 9.11.0（winget 安装，路径已写入 `~/.bashrc`）✓
- `docling`：未安装（重型可选布局后端，暂不需要）

### 衔接
阶段 B 后所有 P0 格式（PDF/EPUB/DOCX/TXT/MD/HTML）及 P1（MOBI/AZW）均具备文本抽取与 `ExtractionMapEntry` 生成能力，直接支撑 TASK-005（输入发现）、TASK-006（PDF/TXT Adapter）、TASK-008（EPUB/DOCX/MD Adapter）、TASK-009（MOBI/AZW Adapter）。

**TASK-006 已完成（2026-07-28）**：`registry.py` 注册表、`base.py` 扩展（probe/capabilities/diagnostics）、TextExtractor GBK/GB2312 编码支持、PdfExtractor 加密/扫描 PDF 错误区分均已实现。后续需补 CLI 命令、批处理与安全校验（不在 TASK-006 范围）。

### 阶段 C / M1 完成（2026-07-28）
- **TASK-005（输入发现与合法性检查）**：`application/gate.py` — Gate 类（文件发现/格式检测/SHA-256/去重/压缩炸弹检测），54 个测试
- **TASK-006（PDF/TXT Adapter + registry）**：`extractors/registry.py` + `base.py` 扩展 + 6 个 extractor 重构 + GBK 编码 + 加密 PDF 检测
- **TASK-007（Analyze Only 模式）**：`llm/` 包（端口 + MockLLMAdapter）、`application/models.py`（AnalysisBundle 模型族）、`application/analyze.py`（AnalyzeUseCase）、CLI `analyze` 命令（含 `--json`）

#### 验证结果（M1 结束，2026-07-28）
- `pytest`：**153 passed, 2 skipped**（覆盖率 85%）
- `ruff check src/ tests/ scripts/`：All checks passed
- `mypy src/`：Success, no issues found in 26 source files
- `python scripts/check_provenance.py`：PASS，8 个 ported 文件一致
- CLI `book2skill analyze <file> --json`：实际运行通过，输出合法 AnalysisBundle JSON

#### M1 已完成架构层次
- `domain/`：SourceManifest、ExtractionMapEntry、TextBlock、Locator、SourceFormat、DomainError/ErrorCode、状态枚举
- `storage/`：RawStorage/SchemaStorage/WikiStorage 端口 + FileRawStorage 等实现 + 原子写入 + 路径穿越防护
- `extractors/`：Extractor 基类（extract/extract_text_blocks/probe/capabilities/diagnostics）+ 6 个 extractor + ExtractorRegistry + default_registry
- `application/`：Gate（输入发现）、AnalyzeUseCase（Analyze Only 编排）、AnalysisBundle 模型族
- `llm/`：LLMAdapter 端口 + MockLLMAdapter（规则驱动）
- `cli.py`：`hello`/`version`/`analyze` 命令

#### 下一步衔接
M1 完成后可直接进入：
- **M2（TASK-008/009/010）**：EPUB/DOCX/MD Adapter 标准化（extractor 已存在，需补 fixtures 和边界测试）、MOBI/AZW DRM 检测、批处理编排
- **M3（TASK-011/012/013）**：KnowledgeUnit 与 Schema 层、SkillIR 编译器、Full Build 与 Build from Analysis（可并行于 M2）
- 注意：TASK-008 的 extractor（epub/docx/html/text）在选择性移植阶段 B 已创建并通过测试，TASK-008 实际只需补 fixtures 和验收标准级别的边界测试

### TASK-008 完成（2026-07-29）
- **交付**：22 个验收级边界测试用例，覆盖 EPUB/DOCX/MD/HTML 四类格式的损坏/边界/部分成功/路径穿越场景
- **改动文件**（仅测试文件，无生产代码变更）：
  - `tests/extractors/test_epub_extractor.py`：重构 `_build_epub` 支持 spine/chapters/omit_files 参数；新增 7 个测试（spine 顺序、单章、非 zip、无 OPF、部分成功跳过缺失 chapter、capabilities/diagnostics、probe）
  - `tests/extractors/test_docx_extractor.py`：提取 `_write_docx` 辅助函数，`_build_docx` 支持 tables/empty_body；新增 6 个测试（表格行、混合段落表格文档顺序、bad zip、空 body、capabilities/diagnostics、probe）
  - `tests/extractors/test_text_extractor.py`：新增 6 个测试（MD 标题/列表/代码块保留、路径穿越不解析相对链接、空文件、.markdown 扩展变体）
  - `tests/extractors/test_html_extractor.py`：新增 3 个测试（标题列表保留、capabilities/diagnostics、probe）
- **关键设计决策**：
  - 沿用 tmp_path 动态构造 fixtures 模式（不建独立 `tests/fixtures/` 目录），与现有 extractor 测试风格一致，避免版权/可移植性问题
  - EPUB spine 顺序与 DOCX 混合段落表格顺序测试用 `monkeypatch` 强制 zipfile 后端，因为 ebooklib/python-docx 后端的元素顺序不保证按 spine/文档顺序
  - Markdown 路径穿越防护测试验证：extractor 只读取传入文件，不解析 Markdown 相对链接（`[x](../secret.txt)` 被当作字面文本保留，secret.txt 内容绝不出现）
- **验证结果**：
  - `pytest tests/ -v`：175 passed, 2 skipped（从 153 → 175，+22）
  - `ruff check src/ tests/ scripts/`：All checks passed
  - `mypy src/`：Success, no issues found in 26 source files
  - `python scripts/check_provenance.py`：PASS，8 个 ported 文件一致
  - 覆盖率：87%（从 85% 提升 2 个百分点）
- **未执行检查**：无
- **已知限制**：
  - EPUB 元数据保留（title/author/dc:metadata）未单独测试——当前 extractor 不提取元数据字段，仅按 spine 抽取章节文本；元数据保留需在 M3 KnowledgeUnit 层或 extractor 扩展时补
  - DOCX 标题层级（Heading 1/2/3 样式）未单独测试——当前 extractor 将所有段落按文本抽取，不区分样式；标题层级保留需在 extractor 扩展时补
- **下一任务**：TASK-016（安全与质量校验器，M5 P0，前置依赖 TASK-013 已满足）。M4（TASK-014/015）已全部完成，当前 387 passed/1 skipped。
