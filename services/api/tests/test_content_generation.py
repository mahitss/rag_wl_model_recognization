"""
M53 — AI Learning Content Studio & Content Generation Pipeline regressions.

These tests exercise the *existing* content-generation engine (M38/M39) to
verify that M53's requirements are met by the current implementation.  They
use synthetic learner profiles and fixed evidence packs; no AI model or
external service is involved.

Per the M53 constraints, this file does NOT duplicate existing
content-generation functionality — it only verifies it.  A compatibility fix
(the M49 working tree changed `retrieve_with_mode` to synchronous but left an
`await` on it in `app/api/v1/content.py`) is also included so content
generation actually executes.
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from app.config import Settings
from app.content_studio import ContentGenerationError, generate_content
from app.tutor import TutorTimeoutError


def _settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        database_path=os.path.join(str(tmp_path), "content-gen.sqlite3"),
        tutor_base_url="http://127.0.0.1:11434",
    )


def _evidence() -> list[dict]:
    return [
        {
            "documentId": "doc_1",
            "displayFilename": "photosynthesis.pdf",
            "pageNumber": 1,
            "chunkIndex": 0,
            "text": "Photosynthesis converts light energy into chemical energy.",
            "retrieval": {"method": "fts5_bm25", "rank": 1},
        },
        {
            "documentId": "doc_1",
            "displayFilename": "photosynthesis.pdf",
            "pageNumber": 2,
            "chunkIndex": 1,
            "text": "Carbon dioxide and water are inputs; oxygen is released as a byproduct.",
            "retrieval": {"method": "fts5_bm25", "rank": 2},
        },
    ]


def _valid_payload() -> str:
    return json.dumps(
        {
            "title": "Photosynthesis Study Notes",
            "sections": [
                {
                    "section_type": "heading",
                    "section_title": "Overview",
                    "content": "Photosynthesis converts light energy into chemical energy [E1].",
                    "citations": ["E1"],
                },
                {
                    "section_type": "body",
                    "section_title": "Inputs and outputs",
                    "content": "Carbon dioxide and water are inputs; oxygen is released [E2].",
                    "citations": ["E2"],
                },
            ],
            "abstain": False,
            "evidence_gaps": [],
        }
    )


# ---------------------------------------------------------------------------
# 2. Content generation: all formats × detail levels
# ---------------------------------------------------------------------------

FORMATS = [
    "notes",
    "summary",
    "flashcards",
    "questions",
    "revision_sheet",
    "concept_outline",
]
DETAIL_LEVELS = ["brief", "standard", "comprehensive"]


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("detail", DETAIL_LEVELS)
def test_all_formats_and_detail_levels_generate_valid_content(tmp_path, fmt, detail):
    """Every supported format and detail level produces structured, cited output."""
    settings = _settings(tmp_path)
    with patch("app.content_studio.local_chat_completion", return_value=_valid_payload()):
        result = generate_content(
            settings,
            topic="photosynthesis",
            format=fmt,
            detail_level=detail,
            evidence=_evidence(),
        )
    assert result["abstain"] is False
    assert len(result["sections"]) >= 1
    assert result["evidenceGaps"] == []
    for section in result["sections"]:
        assert set(section) == {
            "sectionType",
            "sectionTitle",
            "content",
            "citations",
        }
        assert section["sectionType"] in {
            "heading",
            "body",
            "flashcard",
            "question",
            "summary",
        }
        # Every inline marker must be declared in the citations array.
        import re

        markers = set(re.findall(r"\[(E\d+)\]", section["content"]))
        # citation label E<index+1> -> evidence item index <index>.
        expected_doc_ids = {_evidence()[int(m[1:]) - 1]["documentId"] for m in markers}
        cited_doc_ids = {c["documentId"] for c in section["citations"]}
        assert cited_doc_ids == expected_doc_ids, f"markers={markers} citedDocIds={cited_doc_ids}"
        # Every declared citation must resolve to an evidence item.
        for citation in section["citations"]:
            assert citation["documentId"] in {"doc_1"}
            assert citation["pageNumber"] in {1, 2}


# ---------------------------------------------------------------------------
# 3. Grounding & citation validity
# ---------------------------------------------------------------------------


def test_citation_validity_every_marker_declared(tmp_path):
    """Inline citation markers must exactly match the citations array."""
    settings = _settings(tmp_path)
    bad = json.dumps(
        {
            "title": "Notes",
            "sections": [
                {
                    "section_type": "body",
                    "section_title": "Claim",
                    "content": "Light energy becomes chemical [E1].",
                    "citations": [],  # E1 used inline but not declared
                }
            ],
            "abstain": False,
            "evidence_gaps": [],
        }
    )
    with patch("app.content_studio.local_chat_completion", return_value=bad):
        with pytest.raises(ContentGenerationError) as exc:
            generate_content(
                settings,
                topic="photosynthesis",
                format="notes",
                detail_level="standard",
                evidence=_evidence(),
            )
        assert exc.value.status_code == 502
        assert exc.value.code == "CONTENT_INVALID_RESPONSE"


def test_undeclared_citation_label_rejected(tmp_path):
    """A citation label that does not exist in the evidence pack is rejected."""
    settings = _settings(tmp_path)
    bad = json.dumps(
        {
            "title": "Notes",
            "sections": [
                {
                    "section_type": "body",
                    "section_title": "Claim",
                    "content": "Light energy becomes chemical energy [E9].",
                    "citations": ["E9"],
                }
            ],
            "abstain": False,
            "evidence_gaps": [],
        }
    )
    with patch("app.content_studio.local_chat_completion", return_value=bad):
        with pytest.raises(ContentGenerationError):
            generate_content(
                settings,
                topic="photosynthesis",
                format="notes",
                detail_level="standard",
                evidence=_evidence(),
            )


# ---------------------------------------------------------------------------
# 4. Abstention & insufficient evidence
# ---------------------------------------------------------------------------


def test_abstention_when_evidence_insufficient(tmp_path):
    """When the tutor declares abstention, no sections are returned."""
    settings = _settings(tmp_path)
    abstain = json.dumps(
        {
            "title": "Photosynthesis",
            "sections": [],
            "abstain": True,
            "evidence_gaps": ["No passages describe the Calvin cycle."],
        }
    )
    with patch("app.content_studio.local_chat_completion", return_value=abstain):
        result = generate_content(
            settings,
            topic="calvin cycle",
            format="notes",
            detail_level="standard",
            evidence=_evidence(),
        )
    assert result["abstain"] is True
    assert result["sections"] == []
    assert len(result["evidenceGaps"]) >= 1


def test_empty_evidence_abstains_without_model(tmp_path):
    """With no evidence, generation abstains without calling the model."""
    settings = _settings(tmp_path)
    called = []

    def _mock(*args, **kwargs):
        called.append(True)
        return "{}"

    with patch("app.content_studio.local_chat_completion", side_effect=_mock):
        result = generate_content(
            settings,
            topic="anything",
            format="notes",
            detail_level="standard",
            evidence=[],
        )
    assert result["abstain"] is True
    assert called == []  # model was not called


# ---------------------------------------------------------------------------
# 5. Quiz answer-key correctness
# ---------------------------------------------------------------------------


def test_quiz_sections_have_answer_content_and_citations(tmp_path):
    """Question sections carry a suggested answer and cite supporting evidence."""
    settings = _settings(tmp_path)
    quiz_payload = json.dumps(
        {
            "title": "Photosynthesis Quiz",
            "sections": [
                {
                    "section_type": "question",
                    "section_title": "What does chlorophyll absorb?",
                    "content": (
                        "Light energy — chlorophyll absorbs light energy "
                        "to drive photosynthesis [E1]."
                    ),
                    "citations": ["E1"],
                }
            ],
            "abstain": False,
            "evidence_gaps": [],
        }
    )
    with patch("app.content_studio.local_chat_completion", return_value=quiz_payload):
        result = generate_content(
            settings,
            topic="photosynthesis",
            format="questions",
            detail_level="standard",
            evidence=_evidence(),
        )
    assert result["abstain"] is False
    q = result["sections"][0]
    assert q["sectionType"] == "question"
    assert "Light energy" in q["content"]
    assert q["citations"][0]["documentId"] == "doc_1"


# ---------------------------------------------------------------------------
# 6. Malformed model responses
# ---------------------------------------------------------------------------


def test_malformed_json_rejected(tmp_path):
    """Non-JSON model output is rejected safely."""
    settings = _settings(tmp_path)
    with patch("app.content_studio.local_chat_completion", return_value="not json at all"):
        with pytest.raises(ContentGenerationError) as exc:
            generate_content(
                settings,
                topic="photosynthesis",
                format="notes",
                detail_level="standard",
                evidence=_evidence(),
            )
        assert exc.value.status_code == 502
        assert exc.value.code == "CONTENT_INVALID_RESPONSE"


def test_missing_required_keys_rejected(tmp_path):
    """Output missing required top-level keys is rejected."""
    settings = _settings(tmp_path)
    bad = json.dumps({"title": "Notes", "sections": []})  # no abstain/evidence_gaps
    with patch("app.content_studio.local_chat_completion", return_value=bad):
        with pytest.raises(ContentGenerationError):
            generate_content(
                settings,
                topic="photosynthesis",
                format="notes",
                detail_level="standard",
                evidence=_evidence(),
            )


def test_invalid_section_type_rejected(tmp_path):
    """A section with an unsupported section_type is rejected."""
    settings = _settings(tmp_path)
    bad = json.dumps(
        {
            "title": "Notes",
            "sections": [
                {
                    "section_type": "poem",  # not allowed
                    "section_title": "Verse",
                    "content": "Roses are red [E1].",
                    "citations": ["E1"],
                }
            ],
            "abstain": False,
            "evidence_gaps": [],
        }
    )
    with patch("app.content_studio.local_chat_completion", return_value=bad):
        with pytest.raises(ContentGenerationError):
            generate_content(
                settings,
                topic="photosynthesis",
                format="notes",
                detail_level="standard",
                evidence=_evidence(),
            )


# ---------------------------------------------------------------------------
# 6. Model timeout & unavailability
# ---------------------------------------------------------------------------


def test_model_timeout_raises_content_generation_error(tmp_path):
    """A tutor timeout is surfaced as a content-generation error, not a crash."""
    settings = _settings(tmp_path)
    with patch(
        "app.content_studio.local_chat_completion",
        side_effect=TutorTimeoutError("Tutor request timed out after 90s"),
    ):
        with pytest.raises(ContentGenerationError) as exc:
            generate_content(
                settings,
                topic="photosynthesis",
                format="notes",
                detail_level="standard",
                evidence=_evidence(),
            )
        assert exc.value.status_code == 502
        assert exc.value.code == "CONTENT_INVALID_RESPONSE"


def test_tutor_unavailable_propagates(tmp_path):
    """Tutor unavailability is surfaced as a content-generation error."""
    settings = Settings(_env_file=None, database_path=tmp_path / "x.sqlite3")
    # tutor_base_url is None -> tutor_configured is False -> TutorUnavailable

    # local_chat_completion raises TutorUnavailable; generate_content converts
    # it to ContentGenerationError (502) rather than letting it propagate.
    with pytest.raises(ContentGenerationError) as exc:
        generate_content(
            settings,
            topic="photosynthesis",
            format="notes",
            detail_level="standard",
            evidence=_evidence(),
        )
    assert exc.value.status_code == 502
    assert exc.value.code == "CONTENT_INVALID_RESPONSE"
