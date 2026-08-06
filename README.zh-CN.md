# Book2Skill · Evidence-First Book-to-Skill Compiler

Book2Skill 将用户合法持有的 PDF、EPUB、DOCX、MOBI/AZW、TXT、Markdown、HTML 或 RTF 文档，编译为可追溯、可审核、可增量更新、可跨宿主部署的 Agent Skill。

> English documentation: [README.md](README.md) · Package/CLI name: `book2skill`

[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-1026%20passed%20%2F%201%20skipped-brightgreen.svg)](#测试与质量)

## 这是什么

Book2Skill 是一个本地优先的文档知识编译器。它把一次性阅读材料转换成结构化知识、来源台账和可部署 Skill，而不是简单复制全文或生成一份不可核验的摘要。

核心链路：

```text
合法性确认 → Raw 固化与哈希 → 格式抽取与定位
→ AnalysisBundle → NormalizedBundle → Skill IR/Wiki
→ 安全、证据和兼容性质量门 → 原子发布/部署
```

## 相比上游 book-to-skill 的创新点

本项目参考并选择性移植了 [virgiliojr94/book-to-skill](https://github.com/virgiliojr94/book-to-skill) 的部分格式适配器；上游许可证、移植文件和 commit 记录见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 与 [docs/PROVENANCE.yml](docs/PROVENANCE.yml)。上游项目的核心体验是将书籍或文档提炼成可按需加载的 Agent Skill，并提供确定性抽取器、章节文件和多宿主使用方式。

在此基础上，Book2Skill 的工程化差异如下：

| 维度 | 上游 `book-to-skill` | Book2Skill |
|---|---|---|
| 目标 | 快速生成可使用的书籍/文档 Skill | 面向长期维护、审核和发布的知识编译管线 |
| 来源链 | 抽取内容并生成章节/Skill | 不可变 Raw、SHA-256、`source_id + block_id`、locator、provenance 回放 |
| 中间产物 | 以 Skill 文件为主 | 版本化 `AnalysisBundle`、Schema、IR、Wiki 和差异记录 |
| 更新能力 | 支持更新/fold-in 工作流 | add-only 默认策略、显式替换来源、冲突检测、事务日志、回滚和 active pointer |
| 发布质量 | Skill 规则校验 | 来源覆盖、断链、注入、路径、版权引文、Token 预算、强断言和运行时脚手架质量门 |
| 模型接入 | 由 Agent/抽取工作流使用模型 | OpenAI-compatible Adapter、profile 契约、数据发送策略、并发路由、缓存、流式指标和 fail-closed 运行清单 |
| 扩展能力 | 以 Skill/脚本扩展为主 | 公开 Extension SDK、Manifest、依赖解析、安装/升级/回滚和 typed registrar |
| 宿主部署 | 重点覆盖 Copilot CLI、Amp、Claude Code | Claude Code、TRAE、Codex/OpenAI、ChatGPT 项目目录和通用项目目录 |
| 交付方式 | 仓库中的 Agent Skill/CLI | Python 包、wheel、Schema、SDK、CLI 和可审计发布目录 |

这不是对上游项目质量的否定，而是不同的产品边界：上游擅长“提炼并使用”，Book2Skill 进一步解决“可追溯、可审核、可更新、可发布和可扩展”。

## 安装

### 系统要求

- Python 3.11 或更高版本（推荐 3.13）
- Windows、macOS 或 Linux
- `pip` 或 `uv`
- MOBI/AZW 需要可选的 Calibre `ebook-convert`；扫描 PDF 的 OCR 需要 Tesseract

### 从源码安装（推荐开发和本地使用）

PowerShell：

```powershell
cd D:\AI\01_Book2Skill
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e ".[pdf,epub,docx,html,ocr,llm,dev]"
book2skill hello
book2skill version
```

macOS/Linux/Git Bash：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[pdf,epub,docx,html,ocr,llm,dev]"
book2skill hello
```

如果已安装 `uv`，也可以执行：

```bash
uv sync --all-extras
uv run book2skill hello
```

### 安装已构建 wheel

```powershell
python -m pip install dist\book2skill-1.0.1-py3-none-any.whl
book2skill version
```

只安装核心运行时时不需要可选格式依赖；按实际输入格式安装对应 extras。MOBI/AZW 不通过 pip 安装 Calibre。

## 快速使用

以下示例默认使用本地 Mock LLM，不发送文档内容到网络。

### 1. 分析文档

```bash
book2skill analyze input/my-book.pdf --json
```

输出 AnalysisBundle，并保存到默认 `output/bundles/`。分析阶段也会在 `output/workspace/` 固化 Raw、哈希和定位映射。

### 2. 一步构建 Skill

```bash
book2skill build input/my-book.pdf \
  --name my-book \
  --description "将文档知识转为可执行、可追溯的 Skill" \
  --use-when "需要依据该文档进行判断或执行时"
```

### 3. 从审核后的 Bundle 构建

```bash
book2skill build --from-analysis output/bundles/bundle_my-book.json \
  --name my-book \
  --description "将文档知识转为可执行、可追溯的 Skill" \
  --use-when "需要依据该文档进行判断或执行时"
```

### 4. 校验 Skill

```bash
book2skill validate output/skills/my-book
book2skill validate output/skills/my-book --write
book2skill validate output/skills/my-book --json
book2skill compatibility output/skills/my-book --write
```

校验包括 frontmatter、来源覆盖、版权引文、Prompt Injection/危险链接、Token 预算、高风险断言、运行时脚手架和证据边界。兼容报告会分别记录内部检查、可选外部工具与宿主证据。

可重放规范化边界或比较新旧 Skill 正文：

```bash
book2skill normalize output/bundles/bundle_my-book.json -o normalized-bundle.json
book2skill compare-skill-artifacts path/to/v1.0 path/to/v1.0.1 --json
```

### 5. 部署到 Agent 宿主

```bash
book2skill install output/skills/my-book --host claude
book2skill install output/skills/my-book --host codex --project-level
book2skill install output/skills/my-book --host project --project-root ./my-project
book2skill install output/skills/my-book --host claude --dry-run --json
book2skill uninstall my-book --host claude
```

### 6. 增量更新和回滚

```bash
book2skill update --data-home ./output/workspace \
  --collection-id col-xxx --name my-book --use-when "..."
book2skill update --data-home ./output/workspace \
  --collection-id col-xxx --confirm
book2skill update --data-home ./output/workspace --rollback
```

## 接入 OpenAI-compatible 模型（可选）

真实模型默认 fail-closed；API Key 只从环境变量、系统凭据或 `.env` 读取，不接受命令行 API Key。推荐先复制模板：

```bash
cp .env.example .env
```

Profile 方式适合多通道、路由和审计：

```bash
book2skill build input/my-book.epub \
  --name my-book \
  --description "..." \
  --use-when "..." \
  --llm-profiles llm-profiles.example.yaml \
  --llm-strategy balanced \
  --output-dir output/skills/my-book \
  --bundle-dir output/bundles \
  --data-home output/workspace \
  --rights-note "user-licensed"
```

真实发送前确认：输入文档的使用权、provider 数据策略、profile 角色和 API 预算。完整配置见 [docs/LLM_CONFIG.md](docs/LLM_CONFIG.md)。

## 输入、输出和交付物

默认目录：

```text
input/                         # 用户提供的输入文档（不提交版权原文）
output/bundles/                # AnalysisBundle
output/skills/<name>/          # 最终 Skill 目录
output/workspace/              # Raw / Schema / 缓存 / 发布状态
dist/book2skill-1.0.1-*.{whl,tar.gz}  # Python 交付包
```

一个 Skill 通常包含：

```text
SKILL.md                       # 主 Skill 和 Workflow
references/                    # 知识单元、Wiki、来源引用
provenance.yml                 # 来源清单和哈希
quality-report.{md,json}       # 校验结果
normalized-bundle.json         # 可回放、内容寻址的确定性边界
skill-design-review.json       # 任务与执行边界建议
skill-fixtures.json            # 触发与执行回归夹具
compatibility-report.{md,json} # 规范、工具和宿主分层证据
```

## 格式支持

| 格式 | 扩展名 | 依赖 |
|---|---|---|
| PDF | `.pdf` | `pymupdf` / `pypdf` / `pdfminer.six` |
| EPUB | `.epub` | `ebooklib` / `beautifulsoup4` |
| DOCX | `.docx` | `python-docx`（可回退到 ZIP/XML） |
| TXT / Markdown | `.txt` / `.md` | 标准库 |
| HTML | `.html` / `.htm` | `beautifulsoup4` 或标准库回退 |
| RTF | `.rtf` | 内置解析器 |
| MOBI / AZW | `.mobi` / `.azw` / `.azw3` | Calibre CLI |

不绕过 DRM，不下载盗版，不默认联网，不把特定模型或转换器设为不可替代依赖。

## 测试与质量

```bash
.venv/Scripts/pytest tests -q
.venv/Scripts/ruff check src/ tests/ scripts/
.venv/Scripts/mypy src/
.venv/Scripts/python scripts/check_provenance.py
.venv/Scripts/python -m hatchling build
```

当前本地验证：**1026 passed / 1 skipped**；Ruff、mypy、来源一致性和 wheel 构建通过。测试中的一个 skipped 项为可选外部工具场景。

## 文档

- [README.md](README.md) — English guide
- [docs/LLM_CONFIG.md](docs/LLM_CONFIG.md) — LLM、profile、数据策略和路由
- [ARCHITECTURE.md](ARCHITECTURE.md) — 架构
- [SECURITY.md](SECURITY.md) — 安全策略
- [SKILL_DEPLOYMENT.md](SKILL_DEPLOYMENT.md) — 宿主部署
- [EXTENSION_SDK.md](EXTENSION_SDK.md) — Extension SDK
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) — 第三方通知
- [docs/PROVENANCE.yml](docs/PROVENANCE.yml) — 上游来源与移植记录

## 许可证与版权边界

代码采用 MIT，见 [LICENSE](LICENSE)。项目只处理用户合法持有或有权处理的资料；第三方版权书籍生成的 Skill 不应公开发布。生成文件的版权和再分发责任由使用者承担。
