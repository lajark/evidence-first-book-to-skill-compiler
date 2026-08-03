"""Tests for the Analyze Only use case."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from book2skill.application.analyze import AnalyzeUseCase
from book2skill.application.models import AnalysisBundle, CandidateUnit
from book2skill.domain import SourceFormat, TextBlock
from book2skill.extractors.registry import ExtractorRegistry
from book2skill.extractors.text_extractor import TextExtractor
from book2skill.llm.mock_adapter import MockLLMAdapter
from book2skill.storage import FileRawStorage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_txt(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Mock LLM adapter
# ---------------------------------------------------------------------------


class TestMockLLMAdapter:
    """Tests for the rule-based mock LLM."""

    def test_analyze_structure_detects_markdown_headings(self) -> None:
        from book2skill.domain import Locator, LocatorKind, TextBlock

        block = TextBlock(
            text="# Chapter Title\nSome body text here.",
            locator=Locator(kind=LocatorKind.PARAGRAPH, paragraph=1),
        )
        adapter = MockLLMAdapter()
        structure = adapter.analyze_structure("src1", [block])
        assert len(structure) == 1
        assert structure[0]["heading"] == "Chapter Title"
        assert structure[0]["level"] == 1

    def test_analyze_structure_skips_long_lines(self) -> None:
        from book2skill.domain import Locator, LocatorKind, TextBlock

        block = TextBlock(
            text="This is a very long paragraph that should not be treated as a "
            "heading because it exceeds the eighty character threshold limit.",
            locator=Locator(kind=LocatorKind.PARAGRAPH, paragraph=1),
        )
        adapter = MockLLMAdapter()
        structure = adapter.analyze_structure("src1", [block])
        assert len(structure) == 0

    def test_extract_candidates_produces_units(self) -> None:
        from book2skill.domain import Locator, LocatorKind, TextBlock

        block = TextBlock(
            text="You should always validate input before processing.",
            locator=Locator(kind=LocatorKind.PARAGRAPH, paragraph=1),
        )
        adapter = MockLLMAdapter()
        candidates = adapter.extract_candidates("src1", [block])
        assert len(candidates) == 1
        assert candidates[0]["kind"] == "principle"
        assert candidates[0]["review_status"] == "candidate"
        assert candidates[0]["confidence"] >= 0.5

    def test_extract_candidates_flags_thin_content(self) -> None:
        from book2skill.domain import Locator, LocatorKind, TextBlock

        block = TextBlock(
            text="Short.",
            locator=Locator(kind=LocatorKind.PARAGRAPH, paragraph=1),
        )
        adapter = MockLLMAdapter()
        candidates = adapter.extract_candidates("src1", [block])
        assert candidates[0]["confidence"] < 0.5

    def test_suggest_skills_returns_one(self) -> None:
        adapter = MockLLMAdapter()
        candidates = [{"kind": "principle"}, {"kind": "technique"}]
        skills = adapter.suggest_skills("src1", candidates)
        assert len(skills) == 1
        assert skills[0]["name"].startswith("skill-")

    def test_suggest_skills_empty_candidates(self) -> None:
        adapter = MockLLMAdapter()
        assert adapter.suggest_skills("src1", []) == []


# ---------------------------------------------------------------------------
# AnalyzeUseCase end-to-end
# ---------------------------------------------------------------------------


class TestAnalyzeUseCase:
    """End-to-end tests for the Analyze pipeline."""

    def test_analyze_single_txt_file(self, tmp_path: Path) -> None:
        f = _write_txt(
            tmp_path / "book.txt",
            "# Introduction\n\nThis is the first paragraph with enough content.\n\n"
            "# Method\n\nYou should apply this technique carefully in all cases.",
        )
        use_case = AnalyzeUseCase()
        result = use_case.execute([str(f)])

        assert result.errors == []
        assert result.bundle is not None
        bundle = result.bundle
        assert len(bundle.source_ids) == 1
        assert bundle.collection_id.startswith("col-")
        assert len(bundle.candidate_units) >= 2
        assert len(bundle.structure) >= 2
        assert bundle.suggested_skills
        assert bundle.analysis_run is not None
        assert bundle.analysis_run.provider == "mock"
        assert {event.operation for event in bundle.analysis_run.invocations} == {
            "chunk",
            "skills",
        }
        # Two extracted blocks are analysed in one bounded merged chunk instead
        # of issuing separate structure and candidate calls for every block.
        assert [event.operation for event in bundle.analysis_run.invocations].count(
            "chunk"
        ) == 1

    def test_analyze_returns_empty_on_no_inputs(self) -> None:
        use_case = AnalyzeUseCase()
        result = use_case.execute([])
        assert result.bundle is None
        assert result.errors == []

    def test_analyze_reports_missing_file(self, tmp_path: Path) -> None:
        use_case = AnalyzeUseCase()
        result = use_case.execute([str(tmp_path / "missing.txt")])
        assert result.bundle is None
        assert len(result.errors) == 1
        assert "not found" in result.errors[0].message.lower()

    def test_analyze_multiple_files(self, tmp_path: Path) -> None:
        f1 = _write_txt(
            tmp_path / "a.txt",
            "First principle: you must validate everything.",
        )
        f2 = _write_txt(
            tmp_path / "b.txt",
            "Second principle: never trust input blindly.",
        )
        use_case = AnalyzeUseCase()
        result = use_case.execute([str(f1), str(f2)])
        assert result.bundle is not None
        assert len(result.bundle.source_ids) == 2

    def test_analyze_mixed_valid_and_invalid(self, tmp_path: Path) -> None:
        f = _write_txt(tmp_path / "ok.txt", "A valid paragraph with content.")
        use_case = AnalyzeUseCase()
        result = use_case.execute(
            [str(f), str(tmp_path / "missing.txt")]
        )
        assert result.bundle is not None
        assert len(result.bundle.source_ids) == 1
        assert len(result.errors) == 1

    def test_analyze_flags_low_confidence(self, tmp_path: Path) -> None:
        f = _write_txt(tmp_path / "thin.txt", "Short.")
        use_case = AnalyzeUseCase()
        result = use_case.execute([str(f)])
        assert result.bundle is not None
        # Short content → low confidence → flagged
        assert any(
            item.reason == "low_confidence" for item in result.bundle.review_queue
        )

    def test_analyze_does_not_generate_skill(self, tmp_path: Path) -> None:
        f = _write_txt(tmp_path / "book.txt", "Content here for the skill.")
        use_case = AnalyzeUseCase()
        result = use_case.execute([str(f)])
        assert result.bundle is not None
        # AnalysisBundle has no "skill" or "skill_ir" field — only suggestions.
        data = result.bundle.model_dump()
        assert "skill_ir" not in data
        assert "skill" not in data

    def test_analyze_json_output_is_valid(self, tmp_path: Path) -> None:
        f = _write_txt(
            tmp_path / "book.txt",
            "# Heading\n\nContent paragraph here for analysis.",
        )
        use_case = AnalyzeUseCase()
        result = use_case.execute([str(f)])
        assert result.bundle is not None
        data = result.bundle.model_dump(mode="json")
        # Must be JSON-serialisable.
        json_str = json.dumps(data, ensure_ascii=False)
        parsed = json.loads(json_str)
        assert parsed["schema_version"] == 1
        assert "structure" in parsed
        assert "candidate_units" in parsed
        assert "review_queue" in parsed

    def test_analyze_collection_id_is_deterministic(
        self, tmp_path: Path
    ) -> None:
        f = _write_txt(tmp_path / "book.txt", "Content here.")
        use_case = AnalyzeUseCase()
        r1 = use_case.execute([str(f)])
        r2 = use_case.execute([str(f)])
        assert r1.bundle is not None
        assert r2.bundle is not None
        assert r1.bundle.collection_id == r2.bundle.collection_id

    def test_analyze_explicit_collection_id(self, tmp_path: Path) -> None:
        f = _write_txt(tmp_path / "book.txt", "Content here.")
        use_case = AnalyzeUseCase()
        result = use_case.execute([str(f)], collection_id="my-collection")
        assert result.bundle is not None
        assert result.bundle.collection_id == "my-collection"

    def test_analyze_detects_duplicate_content_conflict(
        self, tmp_path: Path
    ) -> None:
        # Two different files (different overall hash → not deduped by Gate)
        # but sharing one identical paragraph → candidate content collision.
        shared = "Identical content across two sources for conflict testing."
        f1 = _write_txt(tmp_path / "a.txt", f"Unique intro A.\n\n{shared}")
        f2 = _write_txt(tmp_path / "b.txt", f"Unique intro B.\n\n{shared}")
        use_case = AnalyzeUseCase()
        result = use_case.execute([str(f1), str(f2)])
        assert result.bundle is not None
        assert len(result.bundle.conflicts) >= 1
        assert result.bundle.conflicts[0].status == "open"

    def test_analyze_rights_note_propagated(self, tmp_path: Path) -> None:
        f = _write_txt(tmp_path / "book.txt", "Content for rights note test.")
        use_case = AnalyzeUseCase()
        result = use_case.execute([str(f)], rights_note="Personal copy")
        assert result.bundle is not None

    def test_analyze_extracts_each_source_once(self, tmp_path: Path) -> None:
        class CountingTextExtractor(TextExtractor):
            calls = 0

            def extract_text_blocks(self, path: Path) -> list[TextBlock]:
                self.calls += 1
                return super().extract_text_blocks(path)

        source = _write_txt(
            tmp_path / "book.txt",
            "First paragraph.\n\nSecond paragraph.",
        )
        extractor = CountingTextExtractor()
        registry = ExtractorRegistry()
        registry.register(SourceFormat.TXT, extractor)

        result = AnalyzeUseCase(registry=registry).execute([str(source)])

        assert result.bundle is not None
        assert extractor.calls == 1

    def test_analyze_persists_original_and_trusted_extraction_map(
        self, tmp_path: Path
    ) -> None:
        source = _write_txt(
            tmp_path / "book.txt",
            "First principle with enough detail.\n\n"
            "Second technique with enough detail.",
        )
        data_home = tmp_path / "data"

        result = AnalyzeUseCase(data_home=data_home).execute([str(source)])

        assert result.bundle is not None
        source_id = result.bundle.source_ids[0]
        storage = FileRawStorage(data_home)
        assert storage.load_original(source_id, 1) == source.read_bytes()
        entries = storage.load_extraction_map(source_id, 1)
        assert len(entries) == 2
        trusted_ids = {entry.block_id for entry in entries}
        candidate_ids = {
            candidate.unit_id for candidate in result.bundle.candidate_units
        }
        referenced_ids = {
            str(ref["block_id"])
            for candidate in result.bundle.candidate_units
            for ref in candidate.source_refs
        }
        assert len(candidate_ids) == 2
        assert referenced_ids == trusted_ids

    def test_analyze_can_skip_raw_persistence_for_dry_run(
        self, tmp_path: Path
    ) -> None:
        source = _write_txt(
            tmp_path / "book.txt",
            "A principle with enough detail for analysis.",
        )
        data_home = tmp_path / "data"

        result = AnalyzeUseCase(data_home=data_home).execute(
            [str(source)], persist_raw=False
        )

        assert result.bundle is not None
        source_id = result.bundle.source_ids[0]
        assert not FileRawStorage(data_home).exists(source_id, 1)

    def test_analyze_ids_are_stable_across_repeated_runs(self, tmp_path: Path) -> None:
        source = _write_txt(
            tmp_path / "book.txt",
            "First principle with enough detail.\n\n"
            "Second technique with enough detail.",
        )
        use_case = AnalyzeUseCase()

        first = use_case.execute([str(source)])
        second = use_case.execute([str(source)])

        assert first.bundle is not None
        assert second.bundle is not None
        first_pairs = [
            (candidate.unit_id, candidate.source_refs[0]["block_id"])
            for candidate in first.bundle.candidate_units
        ]
        second_pairs = [
            (candidate.unit_id, candidate.source_refs[0]["block_id"])
            for candidate in second.bundle.candidate_units
        ]
        assert len({unit_id for unit_id, _block_id in first_pairs}) == 2
        assert first_pairs == second_pairs

    def test_analyze_replaces_untrusted_model_ids(self, tmp_path: Path) -> None:
        class UntrustedIdsAdapter(MockLLMAdapter):
            def analyze_structure(
                self, source_id: str, blocks: list[TextBlock]
            ) -> list[dict[str, object]]:
                return [
                    {
                        "block_id": "model-invented-block",
                        "locator": {"kind": "page", "page": 999},
                        "heading": "Heading",
                        "level": 1,
                        "text_preview": "Heading",
                    }
                ]

            def extract_candidates(
                self, source_id: str, blocks: list[TextBlock]
            ) -> list[dict[str, object]]:
                return [
                    {
                        "unit_id": "model-invented-unit",
                        "kind": "principle",
                        "content": blocks[0].text,
                        "source_refs": [
                            {
                                "source_id": "model-invented-source",
                                "block_id": "model-invented-block",
                            }
                        ],
                        "confidence": 0.8,
                        "review_status": "candidate",
                        "record_version": 99,
                    }
                ]

        source = _write_txt(tmp_path / "book.txt", "# Trusted heading")

        result = AnalyzeUseCase(llm=UntrustedIdsAdapter()).execute([str(source)])

        assert result.bundle is not None
        candidate = result.bundle.candidate_units[0]
        structure = result.bundle.structure[0]
        assert candidate.unit_id != "model-invented-unit"
        assert candidate.record_version == 1
        assert candidate.source_refs == [
            {
                "source_id": result.bundle.source_ids[0],
                "block_id": structure.block_id,
            }
        ]
        assert structure.block_id != "model-invented-block"
        assert structure.locator["kind"] == "paragraph"
        assert structure.locator["paragraph"] == 1
        assert structure.locator["page"] is None


# ---------------------------------------------------------------------------
# AnalysisBundle model validation
# ---------------------------------------------------------------------------


class TestAnalysisBundleModel:
    """Tests for the AnalysisBundle Pydantic model."""

    def test_minimal_bundle_validates(self) -> None:
        from book2skill.application.models import (
            CandidateUnit,
            ReviewItem,
            StructureEntry,
        )

        bundle = AnalysisBundle(
            collection_id="col-test",
            source_ids=["src1"],
            structure=[
                StructureEntry(
                    block_id="src1-1",
                    locator={"kind": "paragraph", "paragraph": 1},
                    heading="Intro",
                    level=1,
                    text_preview="Intro",
                )
            ],
            candidate_units=[
                CandidateUnit(
                    unit_id="cu-1",
                    kind="principle",
                    content="A principle statement.",
                    source_refs=[{"source_id": "src1", "block_id": "src1-1"}],
                    confidence=0.8,
                    review_status="candidate",
                    record_version=1,
                )
            ],
            review_queue=[
                ReviewItem(
                    item_id="rv-1",
                    ref_type="candidate_unit",
                    ref_id="cu-1",
                    reason="low_confidence",
                    severity="warning",
                )
            ],
        )
        assert bundle.schema_version == 1
        assert len(bundle.candidate_units) == 1

    def test_bundle_conforms_to_json_schema(self) -> None:
        from jsonschema import validate
        from jsonschema.validators import Draft202012Validator

        from book2skill.application.models import CandidateUnit

        bundle = AnalysisBundle(
            collection_id="col-test",
            source_ids=["src1"],
            structure=[],
            candidate_units=[
                CandidateUnit(
                    unit_id="cu-1",
                    kind="technique",
                    content="A technique.",
                    source_refs=[{"source_id": "src1", "block_id": "src1-1"}],
                    confidence=0.7,
                    review_status="candidate",
                )
            ],
            review_queue=[],
        )
        data = json.loads(bundle.model_dump_json())
        schema = json.loads(
            Path("schemas/analysis-bundle.schema.json").read_text(encoding="utf-8")
        )
        validate(instance=data, schema=schema, cls=Draft202012Validator)

    def test_bundle_rejects_duplicate_candidate_unit_ids(self) -> None:
        candidate = CandidateUnit(
            unit_id="cu-duplicate",
            kind="technique",
            content="A technique.",
            source_refs=[{"source_id": "src1", "block_id": "src1-p1"}],
            confidence=0.7,
            review_status="candidate",
        )

        with pytest.raises(ValidationError, match="candidate unit_id"):
            AnalysisBundle(
                collection_id="col-test",
                source_ids=["src1"],
                structure=[],
                candidate_units=[candidate, candidate],
                review_queue=[],
            )
