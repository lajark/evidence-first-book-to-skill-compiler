# Contributing to Book2Skill

Thank you for helping improve an evidence-first document compiler. Contributions should make generated knowledge more traceable, reviewable, reproducible, or updateable without weakening the copyright and security boundaries.

## Before opening an issue or pull request

- Do not attach copyrighted books, extracted full text, API keys, private paths, prompts, or raw provider responses.
- Use the smallest synthetic or repository-authored fixture that reproduces the behavior.
- Check [SECURITY.md](SECURITY.md) for vulnerabilities and sensitive disclosures.
- Check [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [docs/PROVENANCE.yml](docs/PROVENANCE.yml) before porting code.

## Development workflow

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check src tests scripts
python -m mypy src
python scripts/check_provenance.py
python scripts/pre_push_scan.py --untracked
```

For the public evidence path, also run:

```bash
python scripts/build_public_demo.py --output-dir .workspace/tmp/public-demo --json
python scripts/run_public_benchmark.py --output-dir .workspace/tmp/public-benchmark --json
```

## Pull request expectations

Every PR should explain the user-visible behavior, scope, and rollback path. Include tests for normal, boundary, and fail-closed cases. Changes that affect generated content must update the relevant provenance, schema, integrity evidence, or deterministic fixture. Changes to third-party or vendored code must include the source, license, and provenance record.

Do not commit `input/` books, `output/`, `workspace/`, `dist/`, `.env`, local profile files, desktop installers, or process records. Generated artifacts must be produced by the project tooling; hand-editing a generated Skill to make a check pass is not acceptable.

## Review standards

Reviewers will look for:

- stable source and unit identities;
- explicit locators and evidence boundaries;
- no silent conflict resolution or source replacement;
- no new network, telemetry, or credential behavior by default;
- compatibility with Python 3.11+ and the existing CLI/SDK contracts;
- clear documentation of what was not measured.

The maintainer may request a focused follow-up instead of accepting unrelated refactors in the same PR.
