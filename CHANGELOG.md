# Changelog

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
