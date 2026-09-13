# Changelog

## Unreleased

- docs: reposition the project around Traceable, Reviewable, Reproducible, and
  Updateable evidence, with a bilingual first-screen README and offline public demo.
- feat(benchmark): add a versioned public Benchmark v1 report contract and a
  deterministic two-run structural benchmark; semantic quality remains explicitly
  `not_measured` until independent labels exist.
- chore(maintenance): add contributor guidance, issue templates, pull-request
  checks, and a code of conduct without weakening security or rights boundaries.
- fix(acceptance): make the maintainer acceptance runner explicitly offline by
  default and add non-destructive `--help`, `--output-dir`, `--llm-mode`, and
  `--json` options.
- compliance: align the repository license and package metadata on MIT while
  keeping Windows installers internal until the dependency manifest and a
  trusted signature accompany an explicit `release_ready=true` gate.
- docs: add a bilingual launch-note draft with the evidence-first message and
  a gated publication sequence.

## 1.0.5

- feat(runtime): bind immutable Asset Pack releases to production Runtime
  Closures with upgrade, rollback, and compensating failure recovery.
- feat(update): add source-provenanced multi-book Pack-only incremental
  publication and rollback APIs without rebuilding the Skill/Kernel tree.
- feat(validation): add final generated-artifact content completeness and
  authoritative SourceManifest carrier checks.
- fix(compiler): preserve CRLF source content hashes on Windows and keep full
  source content in references while bounding workflow descriptions.
- validation: replay three authorized real books through Analyze, Pack release
  upgrades/rollback, production Closure binding, and installed-copy audits.

## 1.0.4

- 修复 PyInstaller 桌面包的 WebGUI 静态资源定位，避免窗口页面空白。
- 参考 StockAnalysis 增加“功能 / 配置 / 帮助”分区，补齐安装、配置、使用和宿主边界说明。
- 桌面版显式支持用户数据根目录中的 `.env` 与 `llm-profiles.local.yaml`，保持宿主无关。

## 1.0.3

- feat(desktop): add the Windows WebGUI, PyInstaller/Inno Setup installer, and
  isolated install/upgrade/uninstall smoke verification.
- build(release): include the desktop dependency manifest, 71 license evidence
  files, recursive checksums, and optional Authenticode/RFC 3161 signing.
- docs: document the Windows packaging boundary, unsigned personal-project
  distribution, and the current private-use license release gate.

## 1.0.2

- hardening: complete M9/M10 security, configuration, diagnostics, lifecycle,
  documentation, and offline-smoke improvements without changing Schema v1 or
  the public SDK compatibility contract.
- ci: add real cross-platform Python 3.11/3.13 validation, an 85% coverage
  gate, optional-extra checks, and failure annotations for GitHub Actions.
- validation: run pinned `skills-ref==0.1.0` and `skill-validator==1.5.6` in
  the portable-release profile with explicit flat-layout and Wiki-directory
  compatibility rules.
- runtime: preserve redacted ETA timing samples on coarse Windows clocks and
  fall back safely when an atomic cache replacement is transiently locked.
- host: record a real Codex CLI Skill-install-path smoke test; no claims are
  made for Claude, Trae, or ChatGPT runtime execution.

## 1.0.1

- feat(contracts): add a content-addressed `NormalizedBundle` boundary with a
  pinned Agent Skills specification revision, sanitized quote hashes, replay
  CLI, tamper checks, and backwards-compatible evidence levels.
- feat(validation): add layered compatibility reports, optional version-locked
  `skills-ref` / `skill-validator` subprocess adapters, conservative host
  evidence levels, and an evidence-boundary advisory check.
- feat(skill-design): add advisory task-boundary/overlap checks, typed execution
  contracts, and deterministic positive, negative, failure, and format fixtures.
- feat(regression): add content-only A/B artifact comparison so diagnostic files
  can be introduced without hiding changes to `SKILL.md` or supporting content.
- feat(sdk): expose normalization and compatibility contracts/services through
  additive SDK 0.2 APIs; no upstream source code was copied in this release.

## 1.0.0

- fix(cli): `analyze`/`batch` now catch `LLMRuntimeError`/`DomainError` and emit
  a structured `{"error": {...}}` document on stdout (exit 1) instead of leaking
  a traceback to stderr and leaving `--json` consumers with an empty file.
- fix(batch): per-file failure isolation now covers LLM/domain runtime errors;
  a failing file becomes a `failed` `FileOutcome` (`code: LLM_FAILURE`) and no
  longer aborts the whole batch.
- fix(progress): progress reporting moved to "after work completes", with a
  leading `0/N` ping before slow LLM bulk calls so the bar no longer jumps to
  100% before the long-running stage begins.

## 0.1.0

- Initial Core release package.
