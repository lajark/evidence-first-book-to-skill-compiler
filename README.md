# Book2Skill

> 将合法持有的 PDF / EPUB / DOCX / MOBI / TXT / Markdown / HTML / RTF 文档编译为可追溯、可审核、可增量更新、跨宿主部署的 Agent Skill。

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-636%20passed-brightgreen.svg)](#测试与质量)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

## 目录

- [功能概览](#功能概览)
- [安装](#安装)
- [从零开始：完成第一次分析](#从零开始完成第一次分析)
- [输入与输出文件位置](#输入与输出文件位置)
- [快速上手](#快速上手)
- [接入大模型（可选）](#接入大模型可选)
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

### 创建并激活虚拟环境（从零开始）

强烈建议在独立虚拟环境（venv）中安装，避免污染全局 Python。以下以本项目根目录为例。

```bash
# 1. 进入项目目录
cd /d/AI/01_Book2Skill    # 替换为你的实际路径

# 2. 创建虚拟环境（仅需一次）
python -m venv .venv
```

每个**新开的终端窗口**需要先激活这个环境后，`book2skill` 命令才会进入 PATH。激活命令因所用 shell 而异：

| Shell | 激活命令 | 说明 |
|-------|---------|------|
| **bash / zsh / sh**（含 Git Bash） | `source .venv/Scripts/activate` | 用 `.` 也行，别漏 `source` |
| **PowerShell** | `.venv\Scripts\Activate.ps1` | 若提示禁止运行脚本，先执行一次 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| **cmd** | `.venv\Scripts\activate.bat` | Windows 命令提示符 |

> **排错**：激活前直接敲 `book2skill` 会报 `not recognized` / `command not found`，这是正常的——命令只存在于 venv 内。不想激活也可改用全路径调用 `.venv\Scripts\book2skill.exe`（bash 用 `.venv/Scripts/book2skill.exe`）。

### 安装方式

```bash
# 方式一：一键安装脚本（国内用户推荐，自动使用清华镜像）
bash scripts/install.sh                # 全部依赖（含可选分组 + dev）
bash scripts/install.sh --no-dev      # 运行时 + 可选，不含 dev
bash scripts/install.sh --core         # 仅核心运行时

# 方式二：手动 pip（国际源，可编辑安装推荐开发使用）
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

以下命令需在**已激活虚拟环境**的终端中运行（见上文激活方法）：

```bash
book2skill version          # 显示版本
book2skill hello            # 冒烟测试
pytest                       # 运行测试套件（636 passed）
```

若尚未激活，可用 venv 内完整路径调用验证（bash / PowerShell 通用）：

```bash
.venv/Scripts/book2skill.exe version
```

## 从零开始：完成第一次分析

本节从**下载项目源码压缩包**开始，一步步走到**完成第一个 PDF 的分析**，全程无需联网调用模型（默认 Mock LLM，离线可用）。

### 1. 下载并解压源码

- 从项目仓库（Gitee / GitHub 的发布页或 `Code → Download ZIP`）下载最新源码 `zip` 文件。
- 解压到目标目录。注意：zip 解压后通常会带一层父文件夹（例如 `Book2Skill-main/`），**请 `cd` 进这层目录**，后续命令都以它为根目录执行。

```bash
cd /d/AI/01_Book2Skill    # 根目录示例（替换为你的实际解压路径）
```

### 2. 创建并激活虚拟环境

```bash
python -m venv .venv        # 仅一次
```

每个新终端执行一次激活命令（按你的 shell 选择）：`source .venv/Scripts/activate`（bash）或 `.venv\Scripts\Activate.ps1`（PowerShell）。

### 3. 安装项目及 PDF 提取依赖

```bash
pip install -e ".[pdf,dev]"
```

- `.[pdf]` 装入 pymupdf / pypdf / pdfminer 等 PDF 提取后端；
- `.[dev]`（可选）装入 pytest / ruff / mypy，便于后续测试与开发。

> 也可以先只装运行时 `pip install -e .`，缺 PDF 后端时工具会给出安装提示。

### 4. 放置输入 PDF

输入文件**没有固定的强制目录**——analyze 直接接受你给出的任意文件路径。建议在项目根建一个 `input\` 目录存放待处理文档，方便管理。把你的 PDF（假设名为 `my-book.pdf`）放到该目录：

```bash
mkdir input
# 将 my-book.pdf 复制到 input\ 下
```

### 5. 运行首次分析

```bash
book2skill analyze input/my-book.pdf --json > bundle.json
```

- `--json` 把结构化结果 `AnalysisBundle` 输出到标准输出，`> bundle.json` 重定向保存为文件，用于人工审核或后续 `build --from-analysis`。
- **默认完全离线**（Mock LLM），无需任何 API 配置即可跑通首个示例。

看到类似输出即代表成功：

```text
Analysis complete — collection: col-abc123
  sources:          1
  candidate units:  12
```

至此你已完成从裸机到首个 PDF 分析的全流程。后续进阶（Build Skill、增量更新、接入 LLM）见「快速上手」与「接入大模型（可选）」。

## 输入与输出文件位置

Book2Skill 对文件位置采用「**显式路径优先 + 默认值兜底**」策略。所有路径均相对命令运行时的当前目录（下文以 cwd 表示）。

| 对象 | 位置约定 | 说明 |
|------|---------|------|
| **输入文档** | 无固定目录，由命令行直接指定（如 `input/my-book.pdf`） | analyze / build / batch 的 `sources` 参数，任意路径均可 |
| **Raw 固化目录** | `--data-home <dir>`，默认不传则**纯内存不落盘**；配置文件默认 `./workspace` | 保存原始文档、哈希、清单与抽取定位映射 |
| **Skill 输出** | `--output-dir`，默认 `workspace/skills/<name>` | build 编译生成的 Skill 目录 |

`--data-home` 内 Raw 的目录结构为：

```text
<data-home>/raw/
└── <source_id>/            # 按来源 ID
    └── <version>/          # 当前固定版本 1
        ├── original/       # 固化保存的原始文档（只读，禁止覆盖）
        ├── manifest.json   # 来源清单：哈希、版权声明、输入路径
        └── extraction-map.jsonl  # 原文分块 → 页面/段落定位映射
```

- **AnalysisBundle**：`analyze --json` 输出到**标准输出**，用 `>` 重定向到任意文件（如 `bundle.json`）。
- **随命令落盘的产物**：指定 `--data-home` 后，Raw / Schema 树会写入该目录；指令类数据（install 备份）默认写入 `~/.book2skill/backups/`。

> 生产使用建议显式传入 `--data-home ./workspace` 等持久目录；纯验证可用默认内存模式，不产生任何文件。

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

## 接入大模型（可选）

Book2Skill **默认完全离线运行（Mock LLM）**，无需任何密钥即可跑通从分析到部署的全流程；需要更深入的内容理解时，可接入 **OpenAI-compatible** 大模型——不绑定特定厂商，支持 OpenAI / Azure / **阿里云百炼（DashScope）** / Ollama / vLLM / LM Studio 等任何兼容端点（云端或本地部署）。

### 用与不用大模型的异同

| 维度 | Mock（默认，离线） | 接入 LLM（`--llm compatible`） |
|------|-------------------|----------------------------|
| 需要 API Key | ❌ 否 | ✅ 云端需要；本地部署可填任意值 |
| 是否联网 | 否 | 云端需要；本地部署不需要 |
| 内容理解 | 关键词启发式，识别 principle/technique/term/case | 模型动态理解、分类与命名 |
| `confidence` 取值 | 固定 0.3 / 0.5 / 0.7 / 0.8（按文本长度） | 模型动态评估 |
| 管线可用性 | 完整可用 | 完整可用；LLM 不可用时自动降级回 Mock |
| 适用场景 | 流程验证、CI/CD、无网络环境 | 真实内容抽取、生产质量 |

> **共同点**：两者产出的 `AnalysisBundle` 结构，以及后续 `build` / `validate` / `install` 流程**完全一致**——Mock 与 LLM 只影响“抽取质量”，不影响管线其余环节。因此可先用 Mock 跑通流程，再切换 LLM 提升质量，无需改任何下游命令。

### 一次性配置（推荐）：`.env` 文件

不希望每次命令都重复传 `--llm` / `--llm-base-url` / `--llm-model`？在项目根目录放一个 `.env` 文件，配置一次后所有 `analyze` / `batch` 命令自动生效。API Key 只能通过环境变量或 `.env` 提供，不接受命令行参数，以免泄漏到 shell 历史或进程列表。

```bash
# 从模板复制（.env 已被 .gitignore 排除，不会提交）
cp .env.example .env
# 然后编辑 .env，按需取消注释并填值
```

`.env` 支持的变量（通用 `LLM_*` 为规范名，优先级高于旧版 `OPENAI_*`，两者均生效）：

| 变量 | 作用 | 示例 |
|------|------|------|
| `BOOK2SKILL_LLM` | 默认适配器，免去每次 `--llm`；取 `mock` / `openai` / `compatible` | `compatible` |
| `BOOK2SKILL_LOCALE` | 人类可读 CLI/进度输出语言；`zh-CN`（默认）或 `en` | `en` |
| `LLM_API_KEY` | API Key（云端真实 key；本地填 `local`） | `sk-...` |
| `LLM_BASE_URL` | OpenAI 兼容端点（云端留空；其他供应商填地址） | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `LLM_MODEL` | 模型名 | `qwen-plus` |

> **查找规则**：从当前目录向上逐级查找 `.env`（在子目录运行命令也能找到项目根的 `.env`）。**优先级**：非敏感命令行参数（`--llm` / `--llm-model` / `--llm-base-url`）> 系统环境变量 > `.env` 文件 > 默认 Mock；其中 `LLM_*` 优先于 `OPENAI_*`。API Key 仅从环境变量或 `.env` 读取。命令行参数可临时覆盖非敏感配置（如 `--llm mock` 跑离线）。

人类可读的 CLI 和进度输出可用 `BOOK2SKILL_LOCALE=en` 或全局 `--locale en` 切换为英文；默认是 `zh-CN`。机器字段始终为英文，分析运行清单会记录有效 locale。

#### 阿里云百炼（DashScope）示例

在 [百炼控制台](https://bailian.console.aliyun.com/) 获取 API-KEY 后，`.env` 写：

```ini
BOOK2SKILL_LLM=compatible
LLM_API_KEY=sk-你的百炼key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus
```

配置好 `.env` 后，直接运行即可，无需任何 `--llm*` 参数：

```bash
book2skill analyze input/my-book.pdf --json > bundle.json
```

### 引入 API Key 的其他方式

若不使用 `.env`，也可用以下方式（优先级同上：命令行 > 环境变量 > Mock）：

```bash
# 方式 A：环境变量（不入命令历史）
# bash / Git Bash
export LLM_API_KEY="sk-你的密钥"
# PowerShell
$env:LLM_API_KEY = "sk-你的密钥"

# 方式 B：写入 shell 配置文件永久生效（~/.bashrc 等）
```

调用示例（`.env` 已配好时直接省略 `--llm*`；此处展示等价显式写法）：

```bash
# 云端 OpenAI，默认模型 gpt-4o
book2skill analyze input/my-book.pdf --llm compatible

# 阿里云百炼
book2skill analyze input/my-book.pdf \
  --llm compatible \
  --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --llm-model qwen-plus

# 本地模型（LM Studio / Ollama / vLLM，走 OpenAI 兼容接口）
book2skill analyze input/my-book.pdf \
  --llm compatible \
  --llm-base-url http://localhost:11434/v1 \
  --llm-model qwen2.5:7b
```

> `--llm openai` 与 `--llm compatible` 完全等价（`openai` 保留向后兼容），二者都走 OpenAI 兼容协议。

### 进度呈现

`analyze` / `build` 在各阶段（提取文本 → 结构分析 → 候选抽取 → Skill 建议 → 编译 Skill）会在 **stderr** 输出 Rich 进度条，便于在 LLM 长耗时等待时观察进展。`--json` 模式下进度仍走 stderr，与 stdout 的 JSON 互不污染，可安全管道处理。

### 安全与降级

- **Key 不入 git**：`.gitignore` 已排除 `.env`、`config.local.*` 等敏感文件，仅保留 `.env.example` 模板。
- **不污染环境**：`.env` 由内置解析器读取，不会写入进程环境变量或日志。
- **日志脱敏**：默认不记录文档正文、提示词、密钥与模型原始响应。
- **自动降级**：当 `openai` 包未安装、Key 为空、或调用失败时，自动回退 Mock 适配器，管线不中断。

> 完整配置（含 LM Studio / Ollama 启动步骤、验证 LLM 是否生效的方法）见 [docs/LLM_CONFIG.md](docs/LLM_CONFIG.md)。

## CLI 命令参考

> **如何在 bash 中调用 `book2skill`？** `book2skill` 不是全局命令，它只存在于虚拟环境内。下表及全文示例中的 `book2skill ...` 都需先满足下列**任一**条件——**不需要任何额外前缀**，要么先激活 venv，要么用全路径：
>
> 1. **激活 venv（推荐，每个新终端一次）**
>    - bash / Git Bash：`source .venv/Scripts/activate`
>    - PowerShell：`.venv\Scripts\Activate.ps1`
>    - cmd：`.venv\Scripts\activate.bat`
>
>    激活后提示符会出现 `(.venv)` 前缀，此时直接敲 `book2skill ...` 即可。
> 2. **用全路径调用（不想激活时）**
>    - bash / Git Bash：`.venv/Scripts/book2skill.exe analyze ...`
>    - PowerShell：`.venv\Scripts\book2skill.exe analyze ...`
> 3. **发布包安装位置**（如安装到 `D:\AI\MyApp\book2skill`）：venv 目录是 `venv/` 而非 `.venv/`，相应改为 `source venv/Scripts/activate` 或 `venv/Scripts/book2skill.exe ...`。
>
> 未激活直接敲 `book2skill` 会报 `command not found`（bash）或 `not recognized`（PowerShell）——这是正常的，命令只在 venv 内。安装与激活详解见上文「安装」一节。

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

> `analyze` / `batch` 的所有 `--llm*` 选项均可省略，改由 `.env` 文件或环境变量提供（见「接入大模型」）。详细命令签名见 [skills/book2skill/references/commands.md](skills/book2skill/references/commands.md)。

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

详见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 测试与质量

```bash
# 全量测试
pytest                         # 636 passed, 1 skipped, 90% 覆盖率

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

详见 [SKILL_DEPLOYMENT.md](SKILL_DEPLOYMENT.md)。

## 项目文档

| 文档 | 说明 |
|------|------|
| [README.md](README.md) | 本文件 — 安装与使用说明 |
| [SECURITY.md](SECURITY.md) | 安全策略 |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | 第三方许可证通知 |
| [AGENTS.md](AGENTS.md) | AI 开发工具项目规则 |
| [docs/LLM_CONFIG.md](docs/LLM_CONFIG.md) | 大模型接入与 API Key 配置指南 |
| [PRD.md](PRD.md) | 产品需求文档 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 架构设计 |
| [DATA_MODEL.md](DATA_MODEL.md) | 数据模型 |
| [FORMAT_ADAPTERS.md](FORMAT_ADAPTERS.md) | 格式适配器规格 |
| [SKILL_AUTHORING_STANDARD.md](SKILL_AUTHORING_STANDARD.md) | Skill 编写标准 |
| [SKILL_DEPLOYMENT.md](SKILL_DEPLOYMENT.md) | 部署手册 |
| [OPEN_SOURCE_REUSE_POLICY.md](OPEN_SOURCE_REUSE_POLICY.md) | 上游复用政策 |
| [TODO.md](TODO.md) | 任务清单与里程碑 |

## 许可证与致谢

- **许可证**：MIT — 见 [LICENSE](LICENSE)
- **上游致谢**：部分提取器代码选择性移植自 [virgiliojr94/book-to-skill](https://github.com/virgiliojr94/book-to-skill)（MIT），见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和 [docs/PROVENANCE.yml](docs/PROVENANCE.yml)
- **设计参考**：[apple-ouyang/book-to-skill](https://github.com/apple-ouyang/book-to-skill)

---

> **安全提醒**：Book2Skill 不绕过 DRM，不下载盗版，不发布第三方版权书籍的长段原文。只处理用户合法持有的文档。未确认版权/使用权时停止转换。
