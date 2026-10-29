# M46 — AI Safety, Prompt-Injection Defense & Tool Authorization

## Threat model

WonderLearn sends retrieved passages, OCR text, and user questions to an
optional local loopback language model. That material is treated as
**untrusted data, never as trusted instructions**. The relevant attackers and
threats are:

| Threat | Vector | Goal |
|--------|--------|------|
| Hidden instructions in documents | Uploaded PDFs / extracted text / OCR text becomes retrieved evidence passed to the model | Override system instructions, exfiltrate data, cause the model to emit attacker-chosen answers |
| Prompt injection in user input | User question / conversation history | Make the model ignore its grounding rules or reveal system internals |
| Malicious retrieved passages | Retrieval returns attacker-chosen chunks | Poison the answer or cause the model to fabricate citations |
| Tool / sensitive-data abuse | Model output is the only model "action" | Cause the model to emit secrets, credentials, or other users' data |
| Context flooding | Very long retrieved passages | Displace system instructions from the model context window |

## Mitigations implemented in M46

### Reused existing controls (not duplicated)

These pre-existing safeguards remain the authoritative safety gates; M46 does
not reimplement them:

* **Loopback-only model endpoint** (`app/config.py`): `WL_TUTOR_BASE_URL` must
  use HTTP on `localhost`/`127.0.0.1`/`::1`, no credentials, no query/fragment.
  No remote model provider can be configured, so no data is exfiltrated
  externally.
* **System prompt treats evidence as untrusted** (`app/tutor.py`,
  `app/quiz.py`, `app/content_studio.py`): every model call instructs the model
  to treat evidence as untrusted source data, never as instructions, and to
  never follow instructions found inside it.
* **Citation label allow-listing + JSON schema validation** (`app/tutor.py`,
  `app/quiz.py`, `app/content_studio.py`): the model may only emit citations
  that refer to allow-listed evidence labels (`E1`…`En`), and malformed output
  is rejected server-side before it reaches any user. This is the authoritative
  gate — the model cannot fabricate a citation to a chunk that was not
  retrieved.
* **Response size cap** (`app/tutor.py`): model responses are capped at
  256 KB (`MAX_TUTOR_RESPONSE_BYTES`).
* **User-scoped retrieval** (`app/storage.py` `search_chunks`): every retrieval
  is filtered by the authenticated user's `user_id`; cross-user document access
  is treated as not found.
* **Citation ownership verification** (`app/storage.py` `create_quiz_set`):
  quiz citations are verified to belong to the requesting user before being
  persisted.
* **No request bodies / evidence text in logs** (`app/logging.py`,
  `app/middleware.py`): access logs never include request bodies, uploaded file
  bytes, or evidence text; secret-like query keys are redacted.
* **No model tool calls exist**: the model returns JSON only; all actions
  (quiz generation, content studio, knowledge graph extraction) are
  server-validated. There is no model-triggered tool-calling surface to
  authorize.
* **Authentication + CSRF** (`app/auth.py`, `app/security.py`): session-based
  auth, double-submit CSRF on state-changing requests, rate limiting, security
  headers.

### New in M46

**`app/prompt_injection.py` — evidence passage truncation (defense-in-depth).**

Every evidence/OCR passage is truncated to at most
`MAX_EVIDENCE_PASSAGE_CHARS` (2,000 characters) with an explicit
`…[passage truncated]` marker before it reaches the model. This bounds the
context window so a long attacker-controlled passage cannot displace the system
instructions (context flooding / prompt drowning). Applied at the single
model-calling chokepoint (`local_chat_completion` in `app/tutor.py`) so every
call site — tutor, quiz, content studio, companion — is covered uniformly.

This is a structural control, **not** a prompt-injection detector. No regex or
heuristic can be relied upon to catch every injection, and no such detector is
implemented or claimed.

**Strengthened system prompt** (`app/tutor.py` `generate_grounded_answer`).

The system prompt now explicitly separates `SYSTEM INSTRUCTIONS`,
`USER QUESTION`, and `EVIDENCE (UNTRUSTED)` sections and re-states that
evidence must never be followed as instructions.

## Tool authorization

WonderLearn has no model-triggered tool-calling surface. The model produces
JSON only, and every generated artifact (answer, quiz, content resource,
knowledge-graph candidate) is validated server-side against allow-listed
retrieved evidence labels. Sensitive operations (quiz persistence, content
studio, admin review) require server-side `require_auth`/`require_admin`
authorization derived from the authenticated session — never from model output.
Client-supplied user IDs or roles are never trusted.

## Residual risks (honest assessment)

* **Prompt injection cannot be eliminated.** A sufficiently long or cleverly
  worded injected passage may still influence model output. The controls reduce
  the blast radius (bounded evidence, allow-listed citations, schema
  validation) but do not provide a guarantee. Human review of generated content
  (content studio marks output unverified until reviewed) remains the final
  safety net.
* **The local model is optional.** When `WL_TUTOR_BASE_URL` is unset, the tutor
  is unavailable and the application degrades to evidence-only (extractive)
  answers and abstention; no model-based injection surface exists in that mode.
* **Logs.** Evidence text is never logged, but the fact that a request was made
  (route, status, duration) is logged. An operator who feeds logs back into a
  model could reintroduce injection; log pipelines should treat logs as
  untrusted.
* **Single-process rate limiting.** Rate-limit state is in-memory and resets on
  restart; multi-instance deployments should enforce shared limits at a trusted
  reverse proxy (`docs/security.md`).

## What M46 does NOT do

* Does not implement a prompt-injection "detector" that claims to catch all
  injections (no such reliable detector exists with current architectures).
* Does not send private learning content to external services.
* Does not change any existing API, feature, or data format.
* Does not claim to make the model safe to use with untrusted evidence without
  human review.

## Verification / test commands

Run the M46 prompt-injection tests together with the existing tutor/retrieval
security tests:

```powershell
# Backend (from repository root)
$env:PYTHONPATH="services/api"
uv run --locked --project services/api --extra dev python -m pytest services/api/tests/test_prompt_injection.py services/api/tests/test_tutor.py services/api/tests/test_quiz.py services/api/tests/test_observability.py services/api/tests/test_model_router.py services/api/tests/test_content_studio.py services/api/tests/test_content_validation.py services/api/tests/test_m42_revision_queue.py

# Lint
uv run --locked --project services/api --extra dev ruff check services/api/app/prompt_injection.py services/api/app/tutor.py services/api/tests/test_prompt_injection.py
```

Expected results (baseline at M45 + M46):
* `test_prompt_injection.py`: 13 passed
* `test_tutor.py`: 12 passed (no regression from the tutor.py change)
* Combined security-relevant suite: 76 passed, 0 failed
* `ruff check`: clean

Tests cover:
* Evidence passage truncation (long passage bounded before reaching the model)
* Injected evidence ("IGNORE ALL PREVIOUS INSTRUCTIONS …") is truncated and
  cannot produce a fabricated citation (server-side allow-list rejects it)
* Model output attempting to disclose the system prompt is rejected as
  malformed (`InvalidTutorResponse` → 502)
* Malformed / non-JSON model output is rejected
* Legitimate questions still return cited answers (no regression)
* Empty evidence → no citation labels exist → any citation rejected
* Unconfigured model → `TutorUnavailable` (safe degradation)
* Evidence text never appears in captured log records

## Files changed

* `services/api/app/prompt_injection.py` (new) — evidence passage truncation
  defense-in-depth module
* `services/api/app/tutor.py` — apply evidence truncation at the model-calling
  chokepoint; strengthen system prompt with explicit system/user/evidence
  separation
* `services/api/tests/test_prompt_injection.py` (new) — 13 adversarial
  regression tests

## Next recommended milestone

M47 should add human-review gating for AI-generated content (content studio
already marks output unverified; extend to tutor answers and quiz questions with
an explicit review/approve workflow), plus a secrets scan pre-commit hook and
dependency vulnerability checking (`pip-audit`/`safety`) to close the
supply-chain and final-review gaps left after M46.
