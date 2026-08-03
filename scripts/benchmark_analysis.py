"""Record reproducible offline Analyze performance baselines.

Each 10/100/1000-block case runs in a fresh Python process.  That makes the
reported peak RSS case-specific instead of the cumulative high-water mark of
the whole benchmark suite.  The benchmark is deliberately offline and uses
the built-in deterministic mock adapter; it records no document body.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import subprocess
import sys
import tempfile
import time
import tracemalloc
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from book2skill.application.analyze import AnalyzeUseCase  # noqa: E402
from book2skill.application.gate import Gate  # noqa: E402
from book2skill.domain import SourceFormat  # noqa: E402
from book2skill.extractors.registry import ExtractorRegistry  # noqa: E402
from book2skill.extractors.text_extractor import TextExtractor  # noqa: E402
from book2skill.llm.chunking import chunk_blocks, estimate_tokens  # noqa: E402
from book2skill.llm.runtime import LLMRuntimeConfig  # noqa: E402
from book2skill.storage.file_storage import FileRawStorage  # noqa: E402


class CountingGate(Gate):
    """Count the one full-file hash pass performed by the real Gate."""

    def __init__(self) -> None:
        self.hash_passes = 0

    def _compute_sha256(self, path: Path) -> str:
        self.hash_passes += 1
        return Gate._compute_sha256(path)


class CountingTextExtractor(TextExtractor):
    """Count full extraction passes without changing extractor behaviour."""

    def __init__(self) -> None:
        self.extraction_passes = 0
        self.blocks = []

    def extract_text_blocks(self, path: Path):  # type: ignore[no-untyped-def]
        self.extraction_passes += 1
        self.blocks = super().extract_text_blocks(path)
        return self.blocks


class CountingRawStorage(FileRawStorage):
    """Count the stream-copy used to persist an immutable Raw original."""

    def __init__(self, data_home: Path) -> None:
        super().__init__(data_home)
        self.copy_passes = 0

    def save_ingest_from_path(self, *args: Any, **kwargs: Any) -> Path:
        self.copy_passes += 1
        return super().save_ingest_from_path(*args, **kwargs)


def run_case(block_count: int) -> dict[str, Any]:
    """Execute one actual Analyze run and return portable measurement data."""
    if block_count < 1:
        raise ValueError("block_count must be positive")

    with tempfile.TemporaryDirectory(prefix="book2skill-benchmark-") as temp:
        root = Path(temp)
        source = root / "benchmark.txt"
        source.write_text(_synthetic_source(block_count), encoding="utf-8")

        gate = CountingGate()
        extractor = CountingTextExtractor()
        registry = ExtractorRegistry()
        registry.register(SourceFormat.TXT, extractor)
        storage = CountingRawStorage(root / "data")
        use_case = AnalyzeUseCase(
            gate=gate,
            registry=registry,
            runtime_config=LLMRuntimeConfig.mock(),
            raw_storage=storage,
        )

        tracemalloc.start()
        started = time.perf_counter()
        result = use_case.execute([str(source)], rights_note="synthetic benchmark")
        wall_time_s = time.perf_counter() - started
        _, traced_peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        if result.bundle is None:
            raise RuntimeError("benchmark Analyze run produced no bundle")
        analysis_run = result.bundle.analysis_run
        if analysis_run is None:
            raise RuntimeError("benchmark Analyze run did not record LLM metadata")

        source_id = result.bundle.source_ids[0]
        chunks = chunk_blocks(
            source_id,
            [
                (block, f"{source_id}-p{index}")
                for index, block in enumerate(extractor.blocks, 1)
            ],
        )
        input_tokens = sum(
            estimate_tokens(item.text)
            + estimate_tokens(item.context_before)
            + estimate_tokens(item.context_after)
            for chunk in chunks
            for item in chunk.items
        )
        invocations = analysis_run.invocations
        operations = Counter(invocation.operation for invocation in invocations)
        candidate_metrics = _candidate_metrics(result.bundle.model_dump(mode="json"))
        reads = {
            "gate_hash_passes": gate.hash_passes,
            "extraction_passes": extractor.extraction_passes,
            "raw_copy_passes": storage.copy_passes,
        }
        reads["logical_full_reads"] = sum(reads.values())
        measurement: dict[str, Any] = {
            "schema_version": 1,
            "block_count": block_count,
            "runtime": "mock-rule-based-v1",
            "wall_time_s": round(wall_time_s, 6),
            "peak_rss_bytes": _peak_rss_bytes(),
            "tracemalloc_peak_bytes": traced_peak_bytes,
            "source_read_operations": reads,
            "llm": {
                "call_count": len(invocations),
                "calls_by_operation": dict(sorted(operations.items())),
                "input_tokens_estimate": input_tokens,
                "response_item_count": sum(
                    invocation.response_items or 0 for invocation in invocations
                ),
            },
            "output_quality": candidate_metrics,
        }
    assert_case_invariants(measurement)
    return measurement


def assert_case_invariants(measurement: dict[str, Any]) -> None:
    """Reject a baseline that no longer exercises the intended pipeline path."""
    blocks = int(measurement["block_count"])
    reads = measurement["source_read_operations"]
    quality = measurement["output_quality"]
    if quality["candidate_count"] != blocks:
        raise AssertionError("synthetic benchmark must yield one candidate per block")
    if quality["source_reference_coverage"] != 1.0:
        raise AssertionError("benchmark candidates must retain complete provenance")
    if reads["gate_hash_passes"] != 1 or reads["extraction_passes"] != 1:
        raise AssertionError(
            "benchmark source must be gated and extracted exactly once"
        )
    if reads["raw_copy_passes"] != 1:
        raise AssertionError("benchmark source must be persisted exactly once")
    if measurement["llm"]["call_count"] < 2:
        raise AssertionError(
            "benchmark must exercise chunk analysis and skill suggestion"
        )


def _candidate_metrics(bundle: dict[str, Any]) -> dict[str, int | float]:
    candidates = bundle["candidate_units"]
    source_ids = set(bundle["source_ids"])
    supported = sum(
        any(
            ref.get("source_id") in source_ids and ref.get("block_id")
            for ref in candidate["source_refs"]
        )
        for candidate in candidates
    )
    return {
        "candidate_count": len(candidates),
        "supported_candidate_count": supported,
        "source_reference_coverage": supported / len(candidates) if candidates else 1.0,
    }


def _synthetic_source(block_count: int) -> str:
    return "\n\n".join(
        "Principle: keep a durable constraint before committing capital. "
        f"Benchmark record {index} has a distinct evidence marker {index * 7919}."
        for index in range(1, block_count + 1)
    )


def _peak_rss_bytes() -> int:
    """Return this fresh child process's peak resident set size in bytes."""
    if sys.platform == "win32":
        from ctypes import wintypes

        class ProcessMemoryCountersEx(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCountersEx()
        counters.cb = ctypes.sizeof(counters)
        psapi = ctypes.WinDLL("Psapi.dll")
        get_current_process = ctypes.windll.kernel32.GetCurrentProcess
        get_current_process.restype = wintypes.HANDLE
        get_process_memory_info = psapi.GetProcessMemoryInfo
        get_process_memory_info.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCountersEx),
            wintypes.DWORD,
        ]
        get_process_memory_info.restype = wintypes.BOOL
        if not get_process_memory_info(
            get_current_process(), ctypes.byref(counters), counters.cb
        ):
            raise OSError("GetProcessMemoryInfo failed")
        return int(counters.PeakWorkingSetSize)

    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def _run_parent(cases: list[int], output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    measurements: list[dict[str, Any]] = []
    for count in cases:
        result_path = output_dir / f"{count}-blocks.json"
        proc = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--_case",
                str(count),
                "--_result",
                str(result_path),
            ],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"benchmark case {count} failed: {proc.stderr or proc.stdout}"
            )
        measurements.append(json.loads(result_path.read_text(encoding="utf-8")))

    summary = {"schema_version": 1, "cases": measurements}
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    (output_dir / "summary.md").write_text(_render_summary(summary), encoding="utf-8")
    return summary


def _render_summary(summary: dict[str, Any]) -> str:
    lines = [
        "# Analyze performance baseline",
        "",
        "Synthetic offline Mock runtime; each case ran in a fresh process.",
        "Peak RSS is OS-level peak working set (or ru_maxrss on POSIX); "
        "tracemalloc is supplemental Python-allocation telemetry.",
        "",
        "| Blocks | Wall time (s) | Peak RSS (MiB) | Reads | LLM calls | "
        "Input tokens (est.) |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for case in summary["cases"]:
        reads = case["source_read_operations"]
        llm = case["llm"]
        lines.append(
            f"| {case['block_count']} | {case['wall_time_s']:.3f} | "
            f"{case['peak_rss_bytes'] / (1024 * 1024):.2f} | "
            f"{reads['logical_full_reads']} | {llm['call_count']} | "
            f"{llm['input_tokens_estimate']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--cases", default="10,100,1000")
    parser.add_argument("--_case", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--_result", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args._case is not None:
        if args._result is None:
            parser.error("--_result is required with --_case")
        args._result.parent.mkdir(parents=True, exist_ok=True)
        args._result.write_text(
            json.dumps(run_case(args._case), indent=2), encoding="utf-8"
        )
        return 0

    try:
        cases = [int(value) for value in args.cases.split(",") if value]
    except ValueError as exc:
        parser.error(f"--cases must be comma-separated integers: {exc}")
    if not cases or any(value < 1 for value in cases):
        parser.error("--cases must contain positive integers")
    output_dir = args.output_dir or (
        _REPO_ROOT / "workspace" / "benchmarks" / time.strftime("%Y%m%dT%H%M%S")
    )
    summary = _run_parent(cases, output_dir)
    print(
        json.dumps({"output_dir": str(output_dir), "case_count": len(summary["cases"])})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
