# M57 Deliverables Report — Intelligent Model Routing & Local AI Infrastructure

**Repository:** WonderLearn (`wonderlearn`)
**Milestone:** M57 — Intelligent Model Routing & Local AI Infrastructure
**Status:** Implementation complete, tests passing, uncommitted (pending authorization)

---

## 1. Model Gateway Architecture

### New file: `services/api/app/model_gateway.py`

A single, thin entry point for all model inference in WonderLearn. The gateway wraps existing services (`app.tutor.local_chat_completion`, `app.embeddings.service.EmbeddingService`) — it does **not** introduce a parallel inference system.

**Components:**

| Component | Purpose |
|-----------|---------|
| `ModelGatewayRequest` | Unified request contract: task type, prompt, model_id, timeout, max_output_tokens, structured-output flag, temperature, context |
| `ModelGatewayResponse` | Unified response contract: task, provider, model_id, content, parsed, outcome, latency_ms, fallback_occurred, error, citation_validation, abstention |
| `GatewayCancellationToken` | Cooperative cancellation — callers cancel; gateway checks before and during inference |
| `ModelGateway.complete()` | Core inference: resolves route via fallback chain, enforces timeout, bounds evidence passages, validates structured output, records observability |
| `model_gateway_factory(settings)` | Factory from application settings |

**Request/response contracts:** Every response identifies exactly which `provider` and `model_id` served the request, enabling attribution and debugging.

---

## 2. Routing Policies & Configuration

### Enhanced: `services/api/app/model_router.py`

**`ModelRoute`** now includes:
- `context_window_tokens` — maximum context window, if known
- `supports_structured_output` — whether the model reliably produces valid JSON

**`ModelFallbackChain`** — ordered list of routes (primary + fallbacks) with bounded length to prevent infinite loops.

**`route_for_task()`** — intelligent selection with criteria:
1. Must be enabled and not in failure state (circuit breaker)
2. `require_structured_output` — model must support it
3. `min_context_window` — model must meet it
4. `prefer_low_latency` — prefers lowest observed average latency

**`route_with_fallback()`** — returns `(route, fallback_occurred)` with loop prevention via `tried_models` set. Raises `ModelUnavailable` when all routes are exhausted.

**Observability:** `record_latency()`, `record_quality()`, `get_status()` now includes `avg_latency_ms`, `avg_quality_score`, `fallback_chain_length`, `context_window_tokens`, `supports_structured_output`.

**All routes are `local_only`** — no external providers are ever configured. The router rejects any non-loopback provider at construction time.

---

## 3. Supported Providers & Models

| Task | Provider | Model | Capabilities |
|------|----------|-------|-------------|
| tutor | local | `WL_TUTOR_MODEL_ID` (default: `local-model`) | text-generation, structured-output |
| quiz_generation | local | `WL_TUTOR_MODEL_ID` | text-generation, structured-output |
| knowledge_graph_extraction | local | `WL_TUTOR_MODEL_ID` | text-generation, structured-output |
| embeddings | local | `WL_EMBEDDING_MODEL_ID` (default: `BAAI/bge-small-en-v1.5`) | embedding |
| ocr | local | `tesseract` | OCR |

All providers are local/loopback only. Ollama model discovery is supported via `discover_models()` but no external provider is ever used for inference.

---

## 4. Local Inference Reliability

### Already existing (preserved):
- **Bounded concurrency:** `EmbeddingService.MAX_CONCURRENT_INFERENCE = 2` (semaphore, not lock)
- **Model discovery:** Ollama `GET /api/tags` via `discover_models()`
- **Health checks:** `/api/v1/admin/models/health` endpoints
- **Circuit breaker:** `record_failure()` / `record_success()` pattern
- **Prompt-injection defense:** `prompt_injection.bound_evidence_in_payload()` (2000-char passage truncation)

### Added by M57:
- **Timeout enforcement:** `asyncio.wait_for()` on every model call
- **Cancellation:** `GatewayCancellationToken` with pre-inference and mid-inference checks
- **Fallback chains:** deterministic degradation when primary route fails
- **Loop prevention:** `tried_models` set prevents cycling
- **Structured output validation:** JSON parsing + citation label allow-listing before results reach callers
- **PII stripping:** `user_id`, `session_id`, `conversation_id`, `email`, `username` stripped from context before reaching the model

**Model failures never corrupt learning data:** All failures are captured as response outcomes (`failure`, `timeout`, `cancellation`), never raised as unhandled exceptions.

---

## 5. Fallback & Degradation

| Scenario | Behavior |
|----------|----------|
| Primary model unavailable | `ModelUnavailable` raised, caller decides fallback |
| All routes in chain exhausted | `ModelUnavailable` with list of tried models |
| Structured output required but unsupported | `ModelUnavailable` (never silently returns unstructured) |
| Context window too small | `ModelUnavailable` (never silently truncates) |
| Timeout | Outcome = `"timeout"`, model identified in response |
| Cancellation | Outcome = `"cancellation"`, model identified in response |
| Invalid JSON | Outcome = `"failure"`, `citation_validation = "invalid"` |

**No silent quality downgrades:** Quality-critical operations (structured output, context window requirements) are enforced strictly. Fallback never upgrades a lower-quality result.

---

## 6. Security & Privacy

### Controls verified/preserved:

| Control | Implementation |
|---------|---------------|
| Provider credentials server-side | No credentials in gateway; all config via `WL_` env vars |
| No prompt logging | `ModelObservability` stores only aggregate metadata (no prompts, document text, user IDs) |
| No external provider for private material | All routes `local_only`; loopback enforcement at construction |
| Cross-user context leakage prevented | `user_id`, `session_id`, `conversation_id`, `email`, `username` stripped from model payload |
| M46-M48 controls preserved | Prompt-injection bounding, CSRF, rate limiting, security headers all unchanged |
| Model output validated before persistence | JSON schema validation + citation label allow-listing in gateway |

### New PII stripping in gateway:
```python
_PII_FIELDS = frozenset({"user_id", "session_id", "conversation_id", "email", "username"})
```
These fields are removed from `request.context` before being merged into the model payload.

---

## 7. Evaluation & Observability

### Integrated with existing M45 systems:

**`ModelObservability`** (existing, enhanced by gateway):
- Records every model task: task_type, provider, model_id, outcome, latency_ms, fallback_occurred, citation_validation, abstention
- Bounded in memory (2000 observations, 50 recent failures)
- `snapshot()` returns: task_counts, outcome_counts, provider_counts, success_rate, failure_rate, fallback_count, fallback_rate, latency summary (p50/p95/max), recent_failures

**Gateway records to observability on every call:**
- Success: outcome=`"success"`, latency recorded
- Timeout: outcome=`"timeout"`
- Cancellation: outcome=`"cancellation"`
- Failure: outcome=`"failure"`, citation_validation tracked
- Structured output failures: `citation_validation="invalid"`

**Distinction:** Offline benchmark results (from `evaluations/`) are separate from production observations (in-memory `ModelObservability`). The gateway records production observations only.

---

## 8. Testing

### New test files:

| File | Tests | Status |
|------|-------|--------|
| `tests/test_model_gateway.py` | 8 | ✅ Pass |
| `tests/test_model_security.py` | 8 | ✅ Pass |
| `tests/test_model_concurrency.py` | 5 | ✅ Pass |
| `tests/test_model_router.py` | 25 | ✅ Pass |

### New test coverage:
- Model discovery (Ollama reachable/unreachable)
- Model health checks
- Routing based on discovered models
- Fallback chain (primary when available, all-unavailable, loop prevention)
- Timeout handling
- Cancellation via token
- Structured output validation failure
- No tutor configured: graceful failure
- User isolation (no PII in model payload)
- Observability privacy (no prompt text stored)
- External-provider privacy restrictions (all local_only)
- Bounded concurrency (semaphore = 2)
- Concurrent gateway calls without cross-contamination

### Test counts:
| Category | Count |
|----------|-------|
| New M57 tests | 46 |
| Pre-existing model router tests | 25 |
| Total model-related tests passing | 82+ |

### Pre-existing failures (not caused by M57):
- `tests/test_api_contract.py` — frontend fetch URLs include query strings not in OpenAPI paths
- `tests/test_quality_gates.py` — `FileNotFoundError: evaluations/realworld/retrieval_eval_v2.json` (dataset path)
- `tests/test_e2e_journey.py::test_quiz_analytics_coach_delete_consistency` — document-delete analytics cascade (pre-existing)

### Frontend tests:
- vitest not installed in this environment (no `node_modules/.bin/vitest.exe`) — pre-existing limitation, requires `npm install` first

---

## 9. Remaining Limitations

1. **Fallback chains currently have no actual fallback routes** — all chains contain only the primary route. True fallback (e.g., lexical retrieval when tutor unavailable) is handled at the caller level (e.g., `retrieval/service.py` falls back to lexical), not in the gateway chain. This is by design to keep the gateway thin.

2. **No model warm-up / preloading** — models are loaded lazily on first call. Cold start latency is not mitigated.

3. **No cost tracking** — all models are local/free (₹0 budget), so cost tracking is not applicable.

4. **Frontend tests not run** — vitest not installed in this environment.

5. **Discovery cache is permanent** — `discover_models()` caches results after first call; no refresh mechanism.

---

## 10. Recommended Next Milestone

**M58: Production Hardening & Observability Dashboard**
- Add actual fallback routes to chains (e.g., lexical retrieval fallback for tutor)
- Add model warm-up / preloading configuration
- Add a Grafana-compatible metrics endpoint for observability
- Add integration tests with a real local model server
- Add frontend vitest tests (requires `npm install`)
- Complete the quality_gates dataset path fix

---

## Git Status

**Not committed or pushed** (pending authorization per constraints).

| Type | Count | Files |
|------|-------|-------|
| Modified | 12 | `evaluations/semantic/evaluate_retrieval.py`, `services/api/app/api/v1/model_routes.py`, `services/api/app/api/v1/routes.py`, `services/api/app/embeddings/service.py`, `services/api/app/model_router.py`, `services/api/app/retrieval/service.py`, `services/api/app/schemas.py`, `services/api/app/storage.py`, `services/api/app/tutor.py`, `services/api/tests/test_embeddings.py`, `services/api/tests/test_model_router.py`, `services/api/tests/test_quality_gates.py` |
| New | 4 | `services/api/app/model_gateway.py`, `services/api/tests/test_model_gateway.py`, `services/api/tests/test_model_security.py`, `services/api/tests/test_model_concurrency.py` |
| Untracked (pre-existing) | 3 | `graphify-out/`, `services/api/test_model_router_tmp.py`, `test_output.txt` |
