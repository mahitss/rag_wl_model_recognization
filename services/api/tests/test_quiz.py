import json

import pytest

from app.quiz import evaluate_answer, generate_quiz_questions
from app.tutor import InvalidTutorResponse


def evidence_fixture():
    return [
        {
            "citationId": "chunk-real-001",
            "documentId": "doc-real-001",
            "displayFilename": "biology.pdf",
            "pageNumber": 4,
            "chunkIndex": 1,
            "charStart": 12,
            "charEnd": 64,
            "text": "Chlorophyll absorbs light energy in leaves.",
            "retrieval": {"method": "fts5_bm25", "rank": 1},
        }
    ]


def generated_questions():
    return {
        "questions": [
            {
                "type": "multiple_choice",
                "question": "What does chlorophyll absorb?",
                "options": [
                    {"id": "A", "text": "Light energy"},
                    {"id": "B", "text": "Sound energy"},
                    {"id": "C", "text": "Soil"},
                    {"id": "D", "text": "Oxygen"},
                ],
                "correctOption": "A",
                "expectedAnswer": "",
                "acceptedAnswers": [],
                "explanation": "The passage says chlorophyll absorbs light energy.",
                "citations": ["E1"],
            },
            {
                "type": "short_answer",
                "question": "What kind of energy does chlorophyll absorb?",
                "options": [],
                "correctOption": "",
                "expectedAnswer": "light energy",
                "acceptedAnswers": ["energy from light"],
                "explanation": "Chlorophyll absorbs light energy.",
                "citations": ["E1"],
            },
        ]
    }


def test_generated_quiz_is_schema_checked_and_citations_map_to_retrieved_records(monkeypatch):
    monkeypatch.setattr(
        "app.quiz.local_chat_completion",
        lambda *_args, **_kwargs: json.dumps(generated_questions()),
    )
    from app.config import Settings

    result = generate_quiz_questions(
        Settings(_env_file=None, tutor_base_url="http://localhost:8080/v1"),
        topic="chlorophyll",
        count=2,
        evidence=evidence_fixture(),
    )
    assert [item["questionType"] for item in result] == ["multiple_choice", "short_answer"]
    assert all(item["citations"] == evidence_fixture() for item in result)
    assert result[0]["answerKey"] == "A"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data["questions"][0].update(citations=["E99"]),
        lambda data: data["questions"][0].update(correctOption="Z"),
        lambda data: data["questions"][0].update(extraField="unsupported"),
        lambda data: data["questions"].pop(),
        lambda data: data["questions"][1].update(expectedAnswer=""),
        lambda data: data["questions"][0]["options"][0].update(text="moon dust"),
    ],
)
def test_malformed_or_unsupported_quiz_questions_are_rejected(monkeypatch, mutate):
    from app.config import Settings

    result = generated_questions()
    mutate(result)
    monkeypatch.setattr(
        "app.quiz.local_chat_completion", lambda *_args, **_kwargs: json.dumps(result)
    )
    with pytest.raises(InvalidTutorResponse):
        generate_quiz_questions(
            Settings(_env_file=None, tutor_base_url="http://localhost:8080/v1"),
            topic="chlorophyll",
            count=2,
            evidence=evidence_fixture(),
        )


def test_short_answer_evaluation_is_deterministic_and_marks_partial_overlap_ambiguous():
    short = {
        "questionType": "short_answer",
        "answerKey": "light energy",
        "acceptedAnswers": ["energy from light"],
    }
    assert evaluate_answer(short, "LIGHT, energy!") == "correct"
    assert evaluate_answer(short, "chlorophyll uses energy") == "ambiguous"
    assert evaluate_answer(short, "gravity") == "incorrect"


def test_multiple_choice_answer_evaluation_is_exact_and_rejects_unknown_options():
    question = {
        "questionType": "multiple_choice",
        "answerKey": "A",
        "options": [{"id": key} for key in "ABCD"],
    }
    assert evaluate_answer(question, "A") == "correct"
    assert evaluate_answer(question, "B") == "incorrect"
    with pytest.raises(ValueError):
        evaluate_answer(question, "E")
