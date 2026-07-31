# Book2Skill

> 将合法持有的 PDF / EPUB / DOCX / MOBI / TXT / Markdown / HTML / RTF 文档编译为可追溯、可审核、可增量更新、跨宿主部署的 Agent Skill。

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-610%20passed-brightgreen.svg)](#测试)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

## 目录

- [功能概览](#功能概览)
- [安装](#安装)
- [快速上手](#快速上手)
- [CLI 命令参考](#cli-命令参考)
- [格式支持矩阵](#格式支持矩阵)
- [架构概览](#架构概览)
- [测试与质量](#测试与质量)
- [部署 Skill](#部署-skill)
- [项目文档](#项目文档)
- [许可证与致谢](#许可证与致谢)

---

## 功能概览

Book2Skill 是一个 Python CLI 工具，把书籍和长文档编译为符合 [Agent Skills 规范](https://docs.anthropic.com/en/docs/agents-and-tools/agent-skills) 的可调用 Skill。核心特点：

- **来源可追溯**：每个知识单元都记录原始来源（source_id + block_id + 定位器），可审核到原文。
- **版本化与增量更新**：Raw 不可变，更新创建新版本与日志；支持三方合并（人工 override vs 新版本差异）。
- **安全与质量校验**：注入检测、路径穿越拦截、版权引文长度校验、Token 预算门、来源覆盖率检查。
- **跨宿主部署**：一键安装到 Claude Code / TRAE / Codex / ChatGPT / 通用项目目录，含升级备份与卸载。
- **离线优先**：默认完全离线运行（Mock LLM），可选接入 OpenAI-compatible 端点。
- **断点恢复**：批处理支持 checkpoint，中断后可续跑。

## 安装

### 前置要求

- **Python 3.11+**（推荐 3.13）
- **pip** 或 **uv**（二选一）
- 可选外部工具：
  - [Calibre](https://calibre-ebook.com/) CLI（`ebook-convert`）— 处理 MOBI/AZW 格式
  - [Tesseract](https://github.com/tesseract-ocr/tesseract) OCR — 扫描型 PDF 的文本识别

### 安装方式

```bash
# 方式一：一键安装脚本（国内用户推荐，自动使用清华镜像）
bash scripts/install.sh                # 全部依赖（含可选分组 + dev）
bash scripts/install.sh --no-dev      # 运行时 + 可选，不含 dev
bash scripts/install.sh --core         # 仅核心运行时

# 方式二：手动 pip（国际源）
pip install -e ".[pdf,epub,docx,html,ocr,dev]"

# 方式三：手动 pip + 国内镜像（解决超时）
pip install -e ".[pdf,epub,docx,html,ocr,dev]" \
  -i https://pypi.tuna.tsinghua.edu.cn/simple \
  --trusted-host pypi.tuna.tsinghua.edu.cn

# 方式四：uv（如已安装）
uv sync --all-extras
```

> **国内用户**：如遇 `Connection timed out` 或 `Could not find a version` 报错，使用方式一（脚本自动配镜像）或方式三（手动指定镜像）。设 `USE_OFFICIAL=1` 可切换回官方 PyPI。

### 可选依赖分组

按需安装对应格式的提取后端：

```bash
pip install -e ".[pdf,epub,docx,html,ocr,llm,dev]"
```

| 分组 | 包 | 用途 |
|------|-----|------|
| `pdf` | pymupdf, pypdf, pdfminer.six | PDF 多后端文本提取 |
| `epub` | ebooklib, beautifulsoup4 | EPUB 章节提取 |
| `docx` | python-docx | DOCX 段落和表格 |
| `html` | beautifulsoup4 | HTML 提取 |
| `ocr` | pillow | 扫描型 PDF OCR 回退（需 Tesseract 二进制） |
| `llm` | openai | OpenAI-compatible LLM 适配器 |
| `dev` | pytest, ruff, mypy, jsonschema | 开发与测试 |

> **MOBI/AZW** 不需要 pip 依赖——通过 Calibre CLI 外部调用，未安装时自动降级并提供安装指引。

### 验证安装

```bash
book2skill version          # 显示版本
book2skill hello            # 冒烟测试
pytest                       # 运行测试套件（610 passed）
```

## 快速上手

### 1. Analyze Only — 分析不编译

分析文档，输出结构化 AnalysisBundle（不生成 Skill），适合人工审核：

```bash
book2skill analyze book.pdf --json > bundle.json
```

### 2. Full Build — 从源文档编译 Skill

一步从文档编译为完整 Skill 目录：

```bash
book2skill build book.pdf \
  --name my-skill \
  --description "What this skill does, when to use it." \
  --use-when "When you need X." \
  --data-home ./workspace \
  --output-dir ./skills
```

### 3. Build from Analysis — 从分析结果编译

先审核 `bundle.json`，再编译：

```bash
book2skill build --from-analysis bundle.json \
  --name my-skill \
  --description "..." \
  --use-when "..." \
  --output-dir ./skills
```

### 4. 验证 Skill 质量

```bash
book2skill validate skills/my-skill          # 只读校验
book2skill validate skills/my-skill --write   # 写入 quality-report
book2skill validate skills/my-skill --json     # JSON 输出
```

### 5. 部署 Skill 到宿主

```bash
# 安装到 Claude Code（个人级）
book2skill install skills/my-skill --host claude

# 安装到项目目录
book2skill install skills/my-skill --host project --project-root ./my-project

# 安装到 Codex（含 agents.md overlay）
book2skill install skills/my-skill --host codex --project-level

# dry-run 预览
book2skill install skills/my-skill --host claude --dry-run --json

# 卸载
book2skill uninstall my-skill --host claude
```

### 6. 增量更新

```bash
# 预览差异（dry-run）
book2skill update --data-home ./workspace --collection-id col-xxx --name my-skill --use-when "..." 

# 确认发布
book2skill update --data-home ./workspace --collection-id col-xxx --confirm

# 回滚
book2skill update --data-home ./workspace --rollback
```

## CLI 命令参考

| 命令 | 说明 | 关键选项 |
|------|------|---------|
| `analyze` | 分析文档，输出 AnalysisBundle | `--json`, `--data-home`, `--collection-id`, `--llm` |
| `batch` | 批处理多文件，故障隔离 | `--json`, `--data-home`, `--max-workers`, `--checkpoint`, `--resume`, `--llm` |
| `build` | Full Build 或 Build from Analysis | `--from-analysis`, `--name`, `--description`, `--use-when`, `--output-dir`, `--data-home` |
| `diff` | 比较两个集合的差异 | `--data-home`, `--collection-id`, `--merge`, `--json` |
| `update` | 增量更新已发布 Skill | `--data-home`, `--collection-id`, `--confirm`, `--rollback`, `--json` |
| `validate` | 校验 Skill 安全与质量 | `--json`, `--write`, `--max-quote-words` |
| `install` | 安装 Skill 到宿主 | `--host`, `--project-level`, `--project-root`, `--dry-run`, `--json` |
| `uninstall` | 卸载 Skill | `--host`, `--project-level`, `--project-root`, `--dry-run`, `--json` |
| `version` | 显示版本 | — |

详细命令签名见 [skills/book2skill/references/commands.md](skills/book2skill/references/commands.md)。

## 格式支持矩阵

| 格式 | 扩展名 | 依赖 | 状态 |
|------|--------|------|------|
| PDF | `.pdf` | pymupdf / pypdf / pdfminer（多后端） | ✅ |
| EPUB | `.epub` | ebooklib / bs4 | ✅ |
| DOCX | `.docx` | python-docx | ✅ |
| TXT | `.txt`, `.text` | 无（标准库） | ✅ |
| Markdown | `.md`, `.markdown` | 无（标准库） | ✅ |
| HTML | `.html`, `.htm` | bs4（可降级到标准库） | ✅ |
| RTF | `.rtf` | 无（标准库解析器） | ✅ |
| MOBI/AZW | `.mobi`, `.azw`, `.azw3` | Calibre CLI（外部） | ✅ 可选 |

所有格式支持：损坏文件检测、加密/DRM 拒绝（不绕过）、编码探测（含 GBK/GB2312）、页码/段落定位映射。

## 架构概览

```
CLI (Typer + Rich)
    │
    ▼
Application (用例编排)
  gate → analyze → build → diff → update → publisher
    │
    ▼
Domain (纯领域模型) + Ports (存储/LLM/宿主端口)
    │
    ▼
Adapters
  extractors (7 格式)  storage (文件/SQLite)  llm (Mock/OpenAI)  hosts (5 宿主)  compiler (IR+Writer+Wiki)
```

**分层依赖方向**：`CLI/SKILL → Application → Domain + Ports → Adapters`

**关键数据流**：合法性确认 → Raw 固化与哈希 → 格式抽取 → Schema/IR 结构化 → Analyze/Build → Wiki/Skill 编译 → 安全校验 → 原子发布/部署

详见 [docs/development/ARCHITECTURE.md](docs/development/ARCHITECTURE.md)。

## 测试与质量

```bash
# 全量测试
pytest                         # 610 passed, 1 skipped, 90% 覆盖率

# 指定模块
pytest tests/extractors/ -v
pytest tests/application/ -v
pytest tests/compiler/ -v

# Lint 与类型检查
ruff check src/ tests/
mypy src/

# 来源一致性
python scripts/check_provenance.py

# 端到端验收
python scripts/run_acceptance.py

# 自举验证
python scripts/bootstrap_self.py
```

## 部署 Skill

Book2Skill 生成的 Skill 可安装到 5 个宿主：

| 宿主 | `--host` 值 | 安装路径（项目级） | 说明 |
|------|-----------|-------------------|------|
| Claude Code | `claude` | `.claude/skills/<name>/` | 标准 Skill 目录 |
| TRAE | `trae` | `.trae/skills/<name>/` | 仅项目级 |
| Codex/OpenAI | `codex` | `.agents/skills/<name>/` | 含 `agents.md` overlay |
| ChatGPT | `chatgpt` | `.chatgpt/skills/<name>/` | Staging 目录，手动上传 |
| 通用项目 | `project` | `skills/<name>/` | 可配 `--target-dir` |

所有宿主：升级前自动备份旧版本到 `~/.book2skill/backups/`，卸载只删除 Skill 目录（不触碰 workspace/raw/backups）。

详见 [docs/development/SKILL_DEPLOYMENT.md](docs/development/SKILL_DEPLOYMENT.md)。

## 项目文档

| 文档 | 说明 |
|------|------|
| [README.md](README.md) | 本文件 — 安装与使用说明 |
| [SECURITY.md](SECURITY.md) | 安全策略 |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | 第三方许可证通知 |
| [AGENTS.md](AGENTS.md) | AI 开发工具项目规则 |
| **开发文档**（`docs/development/`） | |
| [PRD.md](docs/development/PRD.md) | 产品需求文档 |
| [ARCHITECTURE.md](docs/development/ARCHITECTURE.md) | 架构设计 |
| [DATA_MODEL.md](docs/development/DATA_MODEL.md) | 数据模型 |
| [FORMAT_ADAPTERS.md](docs/development/FORMAT_ADAPTERS.md) | 格式适配器规格 |
| [SKILL_AUTHORING_STANDARD.md](docs/development/SKILL_AUTHORING_STANDARD.md) | Skill 编写标准 |
| [SKILL_DEPLOYMENT.md](docs/development/SKILL_DEPLOYMENT.md) | 部署手册 |
| [OPEN_SOURCE_REUSE_POLICY.md](docs/development/OPEN_SOURCE_REUSE_POLICY.md) | 上游复用政策 |
| [TODO.md](docs/development/TODO.md) | 任务清单与里程碑 |
| [TASKS.md](docs/development/TASKS.md) | 任务定义 |
| [IMPLEMENTATION_PLAN.md](docs/development/IMPLEMENTATION_PLAN.md) | 实施计划 |
| [ACCEPTANCE_TEST_PLAN.md](docs/development/ACCEPTANCE_TEST_PLAN.md) | 验收测试计划 |
| [TRACEABILITY_MATRIX.md](docs/development/TRACEABILITY_MATRIX.md) | 追踪矩阵 |

## 许可证与致谢

- **许可证**：MIT — 见 [LICENSE](LICENSE)
- **上游致谢**：部分提取器代码选择性移植自 [virgiliojr94/book-to-skill](https://github.com/virgiliojr94/book-to-skill)（MIT），见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和 [docs/PROVENANCE.yml](docs/PROVENANCE.yml)
- **设计参考**：[apple-ouyang/book-to-skill](https://github.com/apple-ouyang/book-to-skill)

---

> **安全提醒**：Book2Skill 不绕过 DRM，不下载盗版，不发布第三方版权书籍的长段原文。只处理用户合法持有的文档。未确认版权/使用权时停止转换。
