"""A/B/C multi-model benchmark harness (OPT-P1-11).

Runs each configured book through each requested strategy (``single``,
``balanced``, ``quality``) and records a redacted measurement of wall time,
estimated tokens, estimated cost, cache/failure/fallback counts, profile ids
used, source-reference coverage and benchmark-slot coverage.

The harness is offline-first: ``--mock`` builds a deterministic two-channel
mock profile set so the measurement pipeline and report are fully testable
without any cloud endpoint. A real run passes ``--profiles <yaml>`` with the
user's actual provider profiles (≥2 distinct channels for ``balanced`` /
``quality``).

Redaction contract: no API key, endpoint URL, prompt, or source text is ever
recorded. ``profile_id`` values are recorded; the profile's ``api_key_env``
value and ``base_url`` are never. ``benchmark_score`` is left ``null`` for a
manual or separate evaluator step; this harness never auto-scores the rubric.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "src"))

from scripts.acceptance_metrics import analyze_bundle_metrics  # noqa: E402
from scripts.benchmark_analysis import CountingGate, CountingRawStorage  # noqa: E402
from scripts.benchmark_evaluation import (  # noqa: E402
    attach_evaluation,
    load_evaluations,
)

from book2skill.application.analyze import AnalyzeUseCase  # noqa: E402
from book2skill.config import load_env_file  # noqa: E402
from book2skill.extractors.base import Extractor  # noqa: E402
from book2skill.extractors.registry import (  # noqa: E402
    ExtractorRegistry,
    default_registry,
)
from book2skill.llm.benchmark_slots import (  # noqa: E402
    BenchmarkSlot,
    compute_slot_coverage,
    load_benchmark_slots,
    missing_slots,
)
from book2skill.llm.chunking import estimate_tokens  # noqa: E402
from book2skill.llm.profiles import (  # noqa: E402
    ProviderProfile,
    ProviderProfileSet,
    load_profile_set,
    profile_to_runtime_config,
)
from book2skill.llm.quality import (  # noqa: E402
    LLMReviewer,
    QualityBudget,
    QualityService,
)
from book2skill.llm.router import RouterLLMAdapter  # noqa: E402
from book2skill.llm.runtime import (  # noqa: E402
    LLMInvocation,
    LLMRuntimeError,
    RuntimeLLMAdapter,
    build_llm_adapter,
)

STRATEGIES = ("single", "balanced", "quality")

# Maps a book_id to its input file and benchmark-slot YAML.
BOOKS: dict[str, dict[str, str]] = {
    "sunzi": {"file": "孙子兵法.pdf", "slots": "sunzi.yaml"},
    "pomodoro": {"file": "番茄工作法图解.epub", "slots": "pomodoro.yaml"},
    "mini_habits": {"file": "微习惯.epub", "slots": "mini_habits.yaml"},
}

# Books that run by default. ``sunzi`` is a scan-only PDF with no text layer
# (fails Gate with GATE_DAMAGED_FILE and makes no LLM calls), so it is excluded
# from the default run; run it explicitly with ``--book sunzi`` (after OCR).
DEFAULT_BOOKS: tuple[str, ...] = ("pomodoro", "mini_habits")

_SLOTS_DIR = _REPO_ROOT / "benchmark_abc" / "slots"


@dataclass(frozen=True)
class BookSpec:
    book_id: str
    source_path: Path
    slots: list[BenchmarkSlot]


class CountingExtractor(Extractor):
    """Count extraction passes and keep the last block list for any extractor."""

    def __init__(self, inner: Extractor) -> None:
        self._inner = inner
        self.passes = 0
        self.blocks: list[Any] = []

    def extract_text_blocks(self, path: Path) -> list[Any]:
        self.passes += 1
        self.blocks = self._inner.extract_text_blocks(path)
        return self.blocks

    def extract(
        self,
        path: Path,
        *,
        source_id: str,
        version: int = 1,
        original_name: str | None = None,
        rights_note: str | None = None,
    ) -> tuple[Any, list[Any]]:
        return self._inner.extract(
            path,
            source_id=source_id,
            version=version,
            original_name=original_name,
            rights_note=rights_note,
        )

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def version(self) -> str:
        return self._inner.version

    def probe(self, path: Path) -> bool:
        return self._inner.probe(path)

    @property
    def capabilities(self) -> Any:
        return self._inner.capabilities

    def diagnostics(self) -> dict[str, bool]:
        return self._inner.diagnostics()


def _counting_registry() -> ExtractorRegistry:
    """Return a registry wrapping every real extractor with a counter.

    Formats that share one underlying extractor (e.g. TXT/MD share
    ``TextExtractor``) must share one wrapper so the instance the pipeline
    resolves via ``require`` is the same one this harness reads for
    ``block_count``.
    """
    registry = ExtractorRegistry()
    base = default_registry()
    wrappers: dict[int, CountingExtractor] = {}
    for fmt in base.formats():
        inner = base.get(fmt)
        if inner is None:
            continue
        wrapper = wrappers.get(id(inner))
        if wrapper is None:
            wrapper = CountingExtractor(inner)
            wrappers[id(inner)] = wrapper
        registry.register(fmt, wrapper)
    return registry


def _mock_profile_set() -> ProviderProfileSet:
    """Build a deterministic two-channel mock profile set for offline runs."""
    return ProviderProfileSet(
        default_profile="mock-strong",
        profiles=[
            ProviderProfile(
                profile_id="mock-strong",
                provider="mock",
                model="mock-rule-based-v1",
                api_key_env="MOCK_PLACEHOLDER",
                roles=[
                    "map",
                    "section_reduce",
                    "book_reduce",
                    "synthesis",
                    "skill",
                    "arbiter",
                ],
                cost_per_million_input_tokens=0.0,
            ),
            ProviderProfile(
                profile_id="mock-critic",
                provider="mock",
                model="mock-rule-based-v1",
                api_key_env="MOCK_PLACEHOLDER",
                roles=["critic"],
                cost_per_million_input_tokens=0.0,
            ),
        ],
    )


def _build_adapter(
    strategy: str,
    profile_set: ProviderProfileSet,
    *,
    data_home: Path,
    fallback: bool,
    env_file: dict[str, str] | None = None,
) -> tuple[Any, QualityService | None]:
    """Return ``(llm_adapter, quality_service)`` for a strategy."""
    locale = "zh-CN"
    if strategy == "balanced":
        return (
            RouterLLMAdapter(
                profile_set,
                allow_fallback=fallback,
                locale=locale,
                data_home=data_home,
                env_file=env_file,
            ),
            None,
        )
    if strategy == "quality":
        router = RouterLLMAdapter(
            profile_set,
            allow_fallback=fallback,
            locale=locale,
            data_home=data_home,
            env_file=env_file,
        )
        critic_id = _first_critic_profile(profile_set)
        arbiter_id = profile_set.default_profile
        cache_root = data_home / ".cache" / "llm"

        def build_reviewer(profile_id: str, role: str) -> LLMReviewer:
            config = profile_to_runtime_config(
                profile_set.profile(profile_id),
                allow_fallback=fallback,
                locale=locale,
                env_file=env_file,
            )
            adapter = RuntimeLLMAdapter(config, cache_root=cache_root)
            return LLMReviewer(adapter, role=role, model_id=profile_id)  # type: ignore[arg-type]

        service = QualityService(
            critic_reviewer=build_reviewer(critic_id, "critic"),
            arbiter_reviewer=build_reviewer(arbiter_id, "arbiter"),
            budget=QualityBudget(),
        )
        return router, service
    # single
    config = profile_to_runtime_config(
        profile_set.profile(profile_set.default_profile),
        allow_fallback=fallback,
        locale=locale,
        env_file=env_file,
    )
    cache_root = data_home / ".cache" / "llm"
    return build_llm_adapter(config, cache_root=cache_root), None


def _first_critic_profile(profile_set: ProviderProfileSet) -> str:
    for profile in profile_set.profiles:
        if profile.can_serve("critic"):
            return profile.profile_id
    return profile_set.default_profile


def run_strategy(
    spec: BookSpec,
    strategy: str,
    profile_set: ProviderProfileSet,
    *,
    data_home: Path,
    fallback: bool = False,
    env_file: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run one (book, strategy) Analyze and return a redacted measurement."""
    gate = CountingGate()
    registry = _counting_registry()
    extractor = registry.probe(spec.source_path)
    if extractor is None:
        raise RuntimeError(f"no extractor matched {spec.source_path}")
    storage = CountingRawStorage(data_home / "raw")

    adapter, quality = _build_adapter(
        strategy,
        profile_set,
        data_home=data_home,
        fallback=fallback,
        env_file=env_file,
    )
    use_case = AnalyzeUseCase(
        gate=gate,
        registry=registry,
        llm=adapter,
        raw_storage=storage,
    )

    started = time.perf_counter()
    try:
        result = use_case.execute(
            [str(spec.source_path)],
            rights_note="synthetic benchmark",
            quality=quality,
        )
    except LLMRuntimeError as exc:
        # One (book, strategy) run failing must not abort the whole A/B/C.
        # Record a redacted failure measurement and continue the others.
        wall_time_s = time.perf_counter() - started
        return {
            "schema_version": 1,
            "book_id": spec.book_id,
            "strategy": strategy,
            "wall_time_s": round(wall_time_s, 6),
            "block_count": len(extractor.blocks),
            "output_quality": {
                "candidate_count": 0,
                "supported_candidate_count": 0,
                "source_reference_coverage": 1.0,
                "duplicate_candidate_id_count": 0,
                "benchmark_slot_coverage": {s.slot_id: 0 for s in spec.slots},
                "benchmark_slot_gaps": [s.slot_id for s in spec.slots],
            },
            "llm": {
                "call_count": 0,
                "cache_hit_count": 0,
                "retry_count": 0,
                "failure_count": 1,
                "fallback_count": 0,
                "input_tokens_estimate": 0,
                "cost_estimate": 0.0,
                "calls_by_operation": {},
                "calls_by_profile_id": {},
            },
            "quality": {"flags_count": 0, "review_patch_count": 0},
            "profile_ids_used": [],
            "benchmark_score": None,
            "source_error": f"{type(exc).__name__}: {exc}",
            "notes": "run aborted by an LLM runtime error; recorded as a failure "
            "so the remaining strategies/books still execute.",
        }
    wall_time_s = time.perf_counter() - started

    if result.bundle is None:
        error = "; ".join(str(e) for e in result.errors) or "no bundle produced"
        return {
            "schema_version": 1,
            "book_id": spec.book_id,
            "strategy": strategy,
            "wall_time_s": round(wall_time_s, 6),
            "block_count": len(extractor.blocks),
            "output_quality": {
                "candidate_count": 0,
                "supported_candidate_count": 0,
                "source_reference_coverage": 1.0,
                "duplicate_candidate_id_count": 0,
                "benchmark_slot_coverage": {slot.slot_id: 0 for slot in spec.slots},
                "benchmark_slot_gaps": [slot.slot_id for slot in spec.slots],
            },
            "llm": {
                "call_count": 0,
                "cache_hit_count": 0,
                "retry_count": 0,
                "failure_count": 0,
                "fallback_count": 0,
                "input_tokens_estimate": 0,
                "cost_estimate": 0.0,
                "calls_by_operation": {},
                "calls_by_profile_id": {},
            },
            "quality": {"flags_count": 0, "review_patch_count": 0},
            "profile_ids_used": [],
            "benchmark_score": None,
            "source_error": error,
            "notes": "source could not be analyzed (e.g. scanned PDF without OCR); "
            "no LLM calls were made.",
        }
    bundle = result.bundle
    analysis_run = bundle.analysis_run
    if analysis_run is None:
        raise RuntimeError(
            f"strategy {strategy} did not record LLM metadata for {spec.book_id}"
        )

    invocations: list[LLMInvocation] = analysis_run.invocations
    operations = Counter(inv.operation for inv in invocations)
    by_profile = Counter(
        inv.profile_id or profile_set.default_profile for inv in invocations
    )
    cache_hits = sum(1 for inv in invocations if inv.reason == "cache_hit")
    fallbacks = sum(1 for inv in invocations if inv.outcome == "fallback")
    failures = sum(1 for inv in invocations if inv.outcome == "error")

    input_tokens = _estimate_input_tokens(extractor.blocks)
    cost = _estimate_cost(profile_set, input_tokens, by_profile)

    bundle_json = bundle.model_dump(mode="json")
    quality_metrics = analyze_bundle_metrics(bundle_json)
    coverage = compute_slot_coverage(
        bundle.candidate_units,
        spec.slots,
        structure=bundle.structure,
    )
    gaps = missing_slots(coverage, spec.slots)

    measurement: dict[str, Any] = {
        "schema_version": 1,
        "book_id": spec.book_id,
        "strategy": strategy,
        "wall_time_s": round(wall_time_s, 6),
        "block_count": len(extractor.blocks),
        "output_quality": {
            "candidate_count": quality_metrics["candidate_count"],
            "supported_candidate_count": quality_metrics["supported_candidate_count"],
            "source_reference_coverage": quality_metrics["source_reference_coverage"],
            "duplicate_candidate_id_count": quality_metrics[
                "duplicate_candidate_id_count"
            ],
            "benchmark_slot_coverage": coverage,
            "benchmark_slot_gaps": gaps,
        },
        "llm": {
            "call_count": len(invocations),
            "cache_hit_count": cache_hits,
            "retry_count": 0,
            "failure_count": failures,
            "fallback_count": fallbacks,
            "input_tokens_estimate": input_tokens,
            "cost_estimate": round(cost, 6),
            "calls_by_operation": dict(sorted(operations.items())),
            "calls_by_profile_id": dict(sorted(by_profile.items())),
        },
        "quality": {
            "flags_count": len(bundle.quality_review or []),
            "review_patch_count": len(bundle.quality_review or []),
        },
        "profile_ids_used": sorted(by_profile),
        "benchmark_score": None,
        "notes": (
            "benchmark_score is null; rubric scoring requires a separate "
            "evaluator step. retry_count is not tracked by the manifest."
        ),
    }
    return measurement


def _estimate_input_tokens(blocks: list[Any]) -> int:
    """Estimate input tokens for the extracted blocks (best-effort)."""
    return sum(estimate_tokens(block.text) for block in blocks)


def _estimate_cost(
    profile_set: ProviderProfileSet,
    input_tokens: int,
    by_profile: Counter[str],
) -> float:
    """Estimate cost from per-profile input-token pricing."""
    total = 0.0
    for profile_id, count in by_profile.items():
        try:
            profile = profile_set.profile(profile_id)
        except KeyError:
            continue
        rate = profile.cost_per_million_input_tokens
        if rate is None:
            continue
        # Distribute estimated input tokens across calls proportionally.
        share = input_tokens * (count / max(sum(by_profile.values()), 1))
        total += share * rate / 1_000_000
    return total


def _render_measurement(measurement: dict[str, Any]) -> str:
    q = measurement["output_quality"]
    llm = measurement["llm"]
    if measurement.get("source_error"):
        return (
            f"## {measurement['book_id']} / {measurement['strategy']}\n\n"
            f"- Source error: {measurement['source_error']}\n\n"
        )
    lines = [
        f"## {measurement['book_id']} / {measurement['strategy']}",
        "",
        f"- Wall time: {measurement['wall_time_s']:.3f} s",
        f"- Blocks: {measurement['block_count']} | Candidates: {q['candidate_count']} "
        f"| Source coverage: {q['source_reference_coverage']:.2f}",
        f"- LLM calls: {llm['call_count']} "
        f"(cache {llm['cache_hit_count']}, fallback {llm['fallback_count']}, "
        f"failure {llm['failure_count']})",
        f"- Input tokens (est.): {llm['input_tokens_estimate']} | "
        f"Cost (est.): ${llm['cost_estimate']:.4f}",
        f"- Profiles used: {', '.join(measurement['profile_ids_used']) or '—'}",
        "- Benchmark slot coverage: "
        f"{len(q['benchmark_slot_coverage']) - len(q['benchmark_slot_gaps'])}/"
        f"{len(q['benchmark_slot_coverage'])}",
        f"- Benchmark slot gaps: {', '.join(q['benchmark_slot_gaps']) or 'none'}",
        "",
    ]
    return "\n".join(lines)


def _render_summary(measurements: list[dict[str, Any]]) -> str:
    lines = [
        "# A/B/C benchmark summary",
        "",
        "Offline Mock or configured provider run; redacted (no keys, endpoints, "
        "prompts or source text). `benchmark_score` requires a separate evaluator.",
        "",
        "| Book | Strategy | Wall (s) | Calls | Cache | Fallback | Failure | "
        "Coverage | Slot cov | Gaps |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for m in measurements:
        q = m["output_quality"]
        llm = m["llm"]
        slot_total = len(q["benchmark_slot_coverage"])
        slot_cov = slot_total - len(q["benchmark_slot_gaps"])
        lines.append(
            f"| {m['book_id']} | {m['strategy']} | {m['wall_time_s']:.3f} | "
            f"{llm['call_count']} | {llm['cache_hit_count']} | "
            f"{llm['fallback_count']} | {llm['failure_count']} | "
            f"{q['source_reference_coverage']:.2f} | {slot_cov}/{slot_total} | "
            f"{len(q['benchmark_slot_gaps'])} |"
        )
    return "\n".join(lines) + "\n"


def _load_slot_specs(input_dir: Path) -> list[BookSpec]:
    specs: list[BookSpec] = []
    for book_id, mapping in BOOKS.items():
        source = input_dir / mapping["file"]
        if not source.exists():
            raise FileNotFoundError(f"book source not found: {source}")
        slots = load_benchmark_slots(_SLOTS_DIR / mapping["slots"])
        specs.append(BookSpec(book_id=book_id, source_path=source, slots=slots))
    return specs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=_REPO_ROOT / "input")
    parser.add_argument("--profiles", type=Path, help="provider profile YAML")
    parser.add_argument(
        "--mock", action="store_true", help="use deterministic mock profiles"
    )
    parser.add_argument(
        "--skip-missing",
        action="store_true",
        help=(
            "drop reserved profile slots whose api_key_env has no credential "
            "(dynamic channel count); default is fail-closed"
        ),
    )
    parser.add_argument(
        "--strategy",
        default="all",
        choices=["all", *STRATEGIES],
        help="which strategy to run (default: all three)",
    )
    parser.add_argument(
        "--book",
        default="all",
        choices=["all", *BOOKS],
        help=(
            "which book to run (default: all text-extractable books; "
            "scan-only 'sunzi' runs only when named explicitly)"
        ),
    )
    parser.add_argument(
        "--evaluations",
        type=Path,
        help=(
            "validated evaluator JSON file or directory; when supplied, "
            "backfill benchmark_score for matching book/strategy records"
        ),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.mock and args.profiles:
        parser.error("pass either --mock or --profiles, not both")
    if not args.mock and not args.profiles:
        parser.error("pass --mock (offline) or --profiles <yaml> (real cloud)")
    profile_set = _mock_profile_set() if args.mock else load_profile_set(args.profiles)
    env_file = load_env_file()
    evaluations = load_evaluations(args.evaluations) if args.evaluations else {}
    if args.skip_missing:
        profile_set = profile_set.active(env_file=env_file)

    specs = _load_slot_specs(args.input_dir)
    if args.book == "all":
        # Exclude scan-only books (no text layer) from the default run.
        specs = [s for s in specs if s.book_id in DEFAULT_BOOKS]
    else:
        specs = [s for s in specs if s.book_id == args.book]
    strategies = list(STRATEGIES) if args.strategy == "all" else [args.strategy]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    measurements: list[dict[str, Any]] = []
    per_strategy: dict[str, list[dict[str, Any]]] = {s: [] for s in strategies}
    with tempfile.TemporaryDirectory(prefix="book2skill-abc-") as temp:
        data_home = Path(temp)
        for spec in specs:
            for strategy in strategies:
                measurement = run_strategy(
                    spec,
                    strategy,
                    profile_set,
                    data_home=data_home,
                    env_file=env_file,
                )
                measurement = attach_evaluation(
                    measurement, evaluations.get((spec.book_id, strategy))
                )
                measurements.append(measurement)
                per_strategy[strategy].append(measurement)

    for strategy, items in per_strategy.items():
        (args.output_dir / f"{strategy}.json").write_text(
            json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (args.output_dir / f"{strategy}.md").write_text(
            "\n".join(_render_measurement(m) for m in items), encoding="utf-8"
        )
    (args.output_dir / "summary.json").write_text(
        json.dumps(measurements, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (args.output_dir / "summary.md").write_text(
        _render_summary(measurements), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir),
                "run_count": len(measurements),
                "strategies": strategies,
                "books": [s.book_id for s in specs],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
