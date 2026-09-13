# Evidence-first public demo

This is the smallest public case that shows the complete evidence chain. The source is an original repository-authored note; it contains no third-party book text.

Run from the repository root:

```bash
python scripts/build_public_demo.py \
  --output-dir .workspace/tmp/public-demo \
  --json
```

The script explicitly selects the offline Mock LLM, writes all generated material under the chosen directory, and runs the authoritative source-manifest integrity check. Open these files after the run:

- `skill/SKILL.md` — the deployable workflow;
- `skill/normalized-bundle.json` — source-linked knowledge units;
- `skill/provenance.yml` — source identity and hash;
- `skill/quality-report.md` — quality-gate decisions;
- `skill/content-integrity.json` — final completeness gate;
- `skill/compilation-artifact.json` — final carrier hash inventory.

Expected evidence path:

```text
input.md
  → immutable Raw + extraction-map.jsonl
  → normalized-bundle.json (source_id + block_id)
  → SKILL.md / references/
  → quality-report + content-integrity
  → deployable Skill directory
```

The demo is a structural and reproducibility example. A `pass_with_warnings` report is still honest when its warning is documented; it must never be described as a semantic accuracy score.

## Rights

`input.md` was authored for this repository and is distributed with the project under the MIT License. Do not replace it with a copyrighted book in a public checkout.
