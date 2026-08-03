# Changelog

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
