# Book2Skill · Evidence-First Book-to-Skill Compiler

Book2Skill compiles legally usable PDF, EPUB, DOCX, MOBI/AZW, TXT, Markdown, HTML, and RTF documents into traceable, reviewable, incrementally updateable Agent Skills that can be deployed across hosts.

> 中文文档：[README.zh-CN.md](README.zh-CN.md) · Package/CLI name: `book2skill`

[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-999%20passed%20%2F%201%20skipped-brightgreen.svg)](#testing-and-quality)

## What it is

Book2Skill is a local-first document knowledge compiler. It turns reading material into structured knowledge, source ledgers, and deployable Skills instead of copying a book or producing an unverifiable summary.

```text
Rights check → immutable Raw + hashes → extraction + locators
→ AnalysisBundle → Skill IR/Wiki → security and quality gates → atomic publish/deploy
```

## What is different from upstream `book-to-skill`

This project references and selectively ports parts of [virgiliojr94/book-to-skill](https://github.com/virgiliojr94/book-to-skill). Licenses, ported files, and commit records are documented in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [docs/PROVENANCE.yml](docs/PROVENANCE.yml). The upstream project focuses on turning books or document collections into on-demand Agent Skills with deterministic extractors, chapter files, and multi-host usage.

Book2Skill extends that foundation with a stronger lifecycle and audit boundary:

| Area | Upstream `book-to-skill` | Book2Skill |
|---|---|---|
| Product goal | Quickly create a usable book/document Skill | Maintainable, reviewable, publishable knowledge compilation |
| Source traceability | Extracted content and generated chapter/Skill files | Immutable Raw, SHA-256, `source_id + block_id`, locators, provenance replay |
| Intermediate artifacts | Primarily generated Skill files | Versioned `AnalysisBundle`, schemas, IR, Wiki, and diffs |
| Updates | Update/fold-in workflows | Add-only by default, explicit source replacement, conflict detection, journals, rollback, active pointers |
| Quality and security | Skill validation rules | Source coverage, broken links, injection/path checks, quote limits, token budgets, claim and runtime-scaffold warnings |
| LLM runtime | Agent/extractor workflow driven | OpenAI-compatible adapters, profile contracts, data-send policy, routing, caching, streaming metrics, fail-closed behavior |
| Extensibility | Skill/scripts as the main extension surface | Public Extension SDK, manifests, dependency resolution, install/upgrade/rollback, typed registrars |
| Deployment | Primarily Copilot CLI, Amp, and Claude Code | Claude Code, TRAE, Codex/OpenAI, ChatGPT project directories, and generic project directories |
| Distribution | Repository Skill/CLI workflow | Python package, wheel, schemas, SDK, CLI, and auditable release trees |

The distinction is scope, not a quality judgment: upstream is strong at “extract and use”; Book2Skill adds “trace, review, update, publish, and extend.”

## Installation

### Requirements

- Python 3.11+ (3.13 recommended)
- Windows, macOS, or Linux
- `pip` or `uv`
- Optional: Calibre `ebook-convert` for MOBI/AZW; Tesseract for scanned-PDF OCR

### Install from source

PowerShell:

```powershell
cd D:\AI\01_Book2Skill
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e ".[pdf,epub,docx,html,ocr,llm,dev]"
book2skill hello
book2skill version
```

macOS/Linux/Git Bash:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[pdf,epub,docx,html,ocr,llm,dev]"
book2skill hello
```

With `uv`:

```bash
uv sync --all-extras
uv run book2skill hello
```

### Install a built wheel

```powershell
python -m pip install dist\book2skill-1.0.0-py3-none-any.whl
book2skill version
```

Install only the extras needed by the input format. Calibre is an external application, not a pip dependency.

## Quick start

The default local Mock LLM does not send document content over the network.

### Analyze

```bash
book2skill analyze input/my-book.pdf --json
```

This produces an `AnalysisBundle` under `output/bundles/` and persists Raw content, hashes, and locators under `output/workspace/`.

### Build a Skill

```bash
book2skill build input/my-book.pdf \
  --name my-book \
  --description "Turn document knowledge into a traceable executable Skill" \
  --use-when "When you need to reason or act from this document"
```

### Build from a reviewed bundle

```bash
book2skill build --from-analysis output/bundles/bundle_my-book.json \
  --name my-book \
  --description "Turn document knowledge into a traceable executable Skill" \
  --use-when "When you need to reason or act from this document"
```

### Validate

```bash
book2skill validate output/skills/my-book
book2skill validate output/skills/my-book --write
book2skill validate output/skills/my-book --json
```

Validation covers frontmatter, source coverage, copyright quotes, prompt injection and dangerous links, token budget, high-risk claim wording, and runtime-scaffold integrity.

### Deploy, update, and roll back

```bash
book2skill install output/skills/my-book --host claude
book2skill install output/skills/my-book --host codex --project-level
book2skill install output/skills/my-book --host project --project-root ./my-project
book2skill install output/skills/my-book --host claude --dry-run --json
book2skill uninstall my-book --host claude

book2skill update --data-home ./output/workspace \
  --collection-id col-xxx --name my-book --use-when "..."
book2skill update --data-home ./output/workspace \
  --collection-id col-xxx --confirm
book2skill update --data-home ./output/workspace --rollback
```

## Optional OpenAI-compatible LLMs

Real-model execution is fail-closed by default. API keys are read only from environment variables, system credentials, or `.env`; API keys are not accepted as command-line arguments. Start with:

```bash
cp .env.example .env
```

For multi-channel routing and audit manifests, use a profile:

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

Before a real run, confirm the document rights, provider data policy, profile roles, and request budget. See [docs/LLM_CONFIG.md](docs/LLM_CONFIG.md).

## Inputs, outputs, and artifacts

```text
input/                              # user-provided documents; do not commit books
output/bundles/                     # AnalysisBundle files
output/skills/<name>/               # final Skill directory
output/workspace/                   # Raw, Schema, cache, and publish state
dist/book2skill-1.0.0-*.{whl,tar.gz} # Python distribution artifacts
```

A generated Skill normally contains:

```text
SKILL.md                            # main Skill and workflow
references/                         # knowledge units, Wiki, and source routes
provenance.yml                      # source manifest and hashes
quality-report.{md,json}            # validation results
```

## Format support

| Format | Extensions | Dependency |
|---|---|---|
| PDF | `.pdf` | `pymupdf` / `pypdf` / `pdfminer.six` |
| EPUB | `.epub` | `ebooklib` / `beautifulsoup4` |
| DOCX | `.docx` | `python-docx` with ZIP/XML fallback |
| TXT / Markdown | `.txt` / `.md` | Standard library |
| HTML | `.html` / `.htm` | `beautifulsoup4` or standard-library fallback |
| RTF | `.rtf` | Built-in parser |
| MOBI / AZW | `.mobi` / `.azw` / `.azw3` | Calibre CLI |

The project does not bypass DRM, download pirated material, require a specific model or converter, or upload source documents by default.

## Testing and quality

```bash
.venv/Scripts/pytest tests -q
.venv/Scripts/ruff check src/ tests/ scripts/
.venv/Scripts/mypy src/
.venv/Scripts/python scripts/check_provenance.py
.venv/Scripts/python -m hatchling build
```

Latest local verification: **999 passed / 1 skipped**; Ruff, mypy, provenance, and wheel build passed. The skipped test covers an optional external tool.

## Documentation

- [README.zh-CN.md](README.zh-CN.md) — Chinese guide
- [docs/LLM_CONFIG.md](docs/LLM_CONFIG.md) — LLM profiles, data policy, routing
- [ARCHITECTURE.md](ARCHITECTURE.md) — architecture
- [SECURITY.md](SECURITY.md) — security policy
- [SKILL_DEPLOYMENT.md](SKILL_DEPLOYMENT.md) — host deployment
- [EXTENSION_SDK.md](EXTENSION_SDK.md) — Extension SDK
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) — third-party notices
- [docs/PROVENANCE.yml](docs/PROVENANCE.yml) — upstream provenance

## License and rights

The code is MIT-licensed; see [LICENSE](LICENSE). Use only documents you are legally allowed to process. Do not publicly redistribute a Skill derived from a third-party copyrighted book unless you have the required rights. The user is responsible for source-document rights and downstream distribution.
