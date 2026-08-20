"""Privacy-preserving LLM chunk timing history and ETA estimates.

The history deliberately records only runtime identity, estimated input tokens
and elapsed seconds. It never stores source text, prompts, endpoint URLs or
credentials. A corrupt or unavailable local cache is treated as an empty
history so progress reporting can never block analysis.
"""

from __future__ import annotations

import json
import os
import statistics
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

_SCHEMA_VERSION = 2
_MAX_SAMPLES = 200
TimingOperation = Literal["chunk", "skills", "synthesis"]


@dataclass(frozen=True)
class ChunkTimingSample:
    """One redacted, successful non-cached LLM chunk observation."""

    operation: TimingOperation
    provider: str
    model: str
    runtime_scope: str
    prompt_version: str
    response_schema_version: str
    input_tokens: int
    duration_seconds: float

    def is_valid(self) -> bool:
        return (
            self.operation in {"chunk", "skills", "synthesis"}
            and bool(self.provider)
            and bool(self.model)
            and len(self.runtime_scope) == 64
            and self.input_tokens > 0
            and self.duration_seconds > 0
        )


@dataclass(frozen=True)
class EtaEstimate:
    """A token-scaled ETA interval derived from robust observed rates."""

    seconds: float
    lower_seconds: float
    upper_seconds: float
    sample_count: int


class ChunkTimingHistory:
    """Bounded local sample history, resilient to cache read/write failures."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._samples = self._load(path)

    def matching(
        self,
        *,
        operation: TimingOperation,
        provider: str,
        model: str,
        runtime_scope: str,
        prompt_version: str,
        response_schema_version: str,
    ) -> list[ChunkTimingSample]:
        """Return samples from the same response-affecting runtime identity."""
        return [
            sample
            for sample in self._samples
            if sample.operation == operation
            and sample.provider == provider
            and sample.model == model
            and sample.runtime_scope == runtime_scope
            and sample.prompt_version == prompt_version
            and sample.response_schema_version == response_schema_version
        ]

    def append(self, sample: ChunkTimingSample) -> None:
        """Append a valid sample and atomically refresh the bounded cache."""
        if not sample.is_valid():
            return
        self._samples.append(sample)
        self._samples = self._samples[-_MAX_SAMPLES:]
        if self._path is None:
            return
        payload_text = json.dumps(
            {
                "schema_version": _SCHEMA_VERSION,
                "samples": [asdict(item) for item in self._samples],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        temporary: Path | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_name(
                f"{self._path.stem}.{uuid.uuid4().hex}.tmp"
            )
            temporary.write_text(payload_text, encoding="utf-8")
            os.replace(temporary, self._path)
        except OSError:
            # Some Windows runners can transiently reject replacing a freshly
            # created file (for example while an antivirus scanner has it
            # open).  The timing cache is best-effort, but a direct write keeps
            # the persistent ETA history available without weakening redaction.
            try:
                self._path.write_text(payload_text, encoding="utf-8")
            except OSError:
                return
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)

    @staticmethod
    def _load(path: Path | None) -> list[ChunkTimingSample]:
        if path is None:
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != _SCHEMA_VERSION
        ):
            return []
        raw_samples = payload.get("samples")
        if not isinstance(raw_samples, list):
            return []
        samples: list[ChunkTimingSample] = []
        for raw in raw_samples[-_MAX_SAMPLES:]:
            if not isinstance(raw, dict):
                continue
            try:
                sample = ChunkTimingSample(
                    operation=str(raw["operation"]),  # type: ignore[arg-type]
                    provider=str(raw["provider"]),
                    model=str(raw["model"]),
                    runtime_scope=str(raw["runtime_scope"]),
                    prompt_version=str(raw["prompt_version"]),
                    response_schema_version=str(raw["response_schema_version"]),
                    input_tokens=int(raw["input_tokens"]),
                    duration_seconds=float(raw["duration_seconds"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if sample.is_valid():
                samples.append(sample)
        return samples


def estimate_eta(
    input_tokens: int, samples: list[ChunkTimingSample]
) -> EtaEstimate | None:
    """Predict a chunk interval from median token rates, or return ``None``."""
    if input_tokens <= 0:
        return None
    rates = sorted(
        sample.duration_seconds / sample.input_tokens
        for sample in samples
        if sample.is_valid()
    )
    if not rates:
        return None
    median_rate = statistics.median(rates)
    if len(rates) == 1:
        lower_rate, upper_rate = median_rate * 0.5, median_rate * 1.5
    else:
        lower_rate = rates[(len(rates) - 1) // 4]
        upper_rate = rates[(len(rates) - 1) * 3 // 4]
    return EtaEstimate(
        seconds=median_rate * input_tokens,
        lower_seconds=lower_rate * input_tokens,
        upper_seconds=upper_rate * input_tokens,
        sample_count=len(rates),
    )


__all__ = [
    "ChunkTimingHistory",
    "ChunkTimingSample",
    "EtaEstimate",
    "TimingOperation",
    "estimate_eta",
]
