# Evidence-first Benchmark

Book2Skill measures whether generated knowledge remains traceable and reviewable. It does not turn provenance coverage into a semantic accuracy or hallucination claim.

## Public v1 benchmark

Run the repository-authored case twice with the offline Mock LLM:

```bash
python scripts/run_public_benchmark.py \
  --output-dir .workspace/tmp/public-benchmark \
  --json
```

The command writes `benchmark-report.json` and a Markdown view generated from that JSON. The report schema is [schemas/benchmark-report.schema.json](../schemas/benchmark-report.schema.json). The two runs compare only consumer files (`SKILL.md`, `references/`, `assets/`, and `scripts/`); timestamps and other diagnostics are intentionally excluded from the reproducibility hash.

## Metric contract

| Metric | Meaning | Evidence boundary |
|---|---|---|
| `source_reference_coverage` | Units with one or more source references divided by all units | Provenance coverage, not semantic correctness |
| `locator_resolution_rate` | Referenced block IDs found in the immutable extraction map | Structural locator check only |
| `content_integrity_blocked` | Whether the final content-integrity gate blocked publication | Fail-closed completeness evidence |
| `duplicate_unit_id_count` | Duplicate normalized unit IDs | Deterministic identity check |
| `rebuild_consumer_hash_equal` | Consumer-file hash equality across two builds | Replay stability for the fixture |
| `skill_fixture_pass_rate` | Host execution fixture result | `not_measured` until a host harness runs |
| `update_consistency` | Same-kernel Pack/update replay result | `not_measured` for the single-version demo |
| `semantic_hallucination_rate` | Independent human-labeled semantic evaluation | `not_measured` until a rubric and labels exist |

Human review must record the evaluator, rubric version, date, confidence, and source evidence. A full-source citation rate is never presented as a hallucination rate.

## Scope and limitations

- The public case is original repository-authored text and carries no third-party book content.
- Mock results establish repeatability and evidence plumbing, not production model quality, latency, or cost.
- Real LLM, host execution, multi-book update, and public-domain book cases require separate fixtures and rights records.
- Negative controls for missing locators, altered source hashes, duplicate IDs, truncation, and replacement must fail closed before a release is considered.
