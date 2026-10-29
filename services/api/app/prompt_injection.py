"""Defense-in-depth helpers for prompt-injection resistance in model inputs.

WonderLearn sends retrieved passages and user text to an optional local
loopback model. That material is **untrusted data**, never trusted
instructions. This module provides structural controls that reduce the blast
radius of prompt injection; it does NOT provide a detector that can be relied
upon to catch every injection. Prompt injection cannot be eliminated
completely with current language-model architectures, and no claim to the
contrary is made.

Controls implemented here:
* Evidence passage truncation: each retrieved/OCR passage is bounded in length
  before it reaches the model, preventing evidence-flooding attacks that try
  to drown out the system instructions.
* Payload construction that keeps the system prompt, the user request, and the
  evidence pack as clearly separated fields in the model request.

Server-side schema validation (citation label allow-listing, JSON-shape
checks) in app/tutor.py, app/quiz.py, app/content_studio.py remains the
authoritative safety gate: the model can only emit citations that refer to
retrieved evidence labels, and malformed output is rejected before it reaches
any user.
"""

from __future__ import annotations

from typing import Any

# Maximum characters retained from any single evidence/OCR passage before it
# is sent to the model.  Long passages are truncated with a marker so the
# model still sees a bounded, legible excerpt instead of an attacker-controlled
# wall of text that could displace the system prompt from the context window.
MAX_EVIDENCE_PASSAGE_CHARS = 2_000


def truncate_evidence_passage(text: str, *, max_chars: int = MAX_EVIDENCE_PASSAGE_CHARS) -> str:
    """Return ``text`` truncated to ``max_chars`` with a truncation marker.

    Never raises for non-string input; returns the input unchanged.
    """
    if not isinstance(text, str):
        return text
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n…[passage truncated]"


def bound_evidence_in_payload(
    user_payload: dict[str, Any], *, max_chars: int = MAX_EVIDENCE_PASSAGE_CHARS
) -> dict[str, Any]:
    """Return a copy of ``user_payload`` with every evidence passage text bounded.

    The ``evidence`` key, when present and a list, is expected to contain
    mappings with a ``text`` field (the shape used by every WonderLearn model
    call site).  Other keys and non-evidence values are copied unchanged.
    """
    bounded: dict[str, Any] = dict(user_payload)
    evidence = bounded.get("evidence")
    if isinstance(evidence, list):
        bounded["evidence"] = [
            {**item, "text": truncate_evidence_passage(item.get("text", ""), max_chars=max_chars)}
            if isinstance(item, dict)
            else item
            for item in evidence
        ]
    return bounded
