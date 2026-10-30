# M48 — Security Hardening & Production Remediation

## Executive summary

M48 remediates six vulnerabilities discovered in the WonderLearn API through
code inspection and reproduction. All fixes use defense-in-depth: authorization
and security decisions are enforced server-side, never trusted from the client.
No security guarantee or certification is claimed.

## Vulnerabilities found and fixed

### VULN-01: `/metrics` endpoint unauthenticated (HIGH)

**Original vulnerability:** `GET /api/v1/metrics` required no authentication
and returned operational metrics including request paths, error counts, latency
percentiles, and recent failures.

**Reproduction:** An unauthenticated `GET /api/v1/metrics` returned `200 OK`
with full telemetry data.

**Severity:** HIGH — Information disclosure of operational telemetry that could
aid further attacks.

**Root cause:** The route handler at `services/api/app/api/v1/routes.py:1061`
had no `Depends(require_auth)` dependency.

**Fix:** Added `current_user: AuthUser = Depends(require_auth)` to the route
handler.

**Verification:**
- Before: `GET /api/v1/metrics` → `200 OK` (anonymous)
- After: `GET /api/v1/metrics` → `401 Unauthorized` (anonymous), `200 OK` (authenticated)
- Regression test: `test_metrics_requires_auth`, `test_metrics_returns_200_for_authenticated`

### VULN-02: `/models/status` endpoint unauthenticated (MEDIUM)

**Original vulnerability:** `GET /api/v1/models/status` required no
authentication and returned model configuration including provider, model IDs,
timeouts, privacy levels, availability, and failure counts.

**Reproduction:** An unauthenticated `GET /api/v1/models/status` returned `200 OK`.

**Severity:** MEDIUM — Information disclosure of model configuration that could
aid targeted attacks against the model endpoint.

**Root cause:** The route handler at `services/api/app/api/v1/routes.py:1680`
had no `Depends(require_auth)` dependency.

**Fix:** Added `current_user: AuthUser = Depends(require_auth)` to the route handler.

**Verification:**
- Before: `GET /api/v1/models/status` → `200 OK` (anonymous)
- After: `GET /api/v1/models/status` → `401 Unauthorized` (anonymous), `200 OK` (authenticated)
- Regression test: `test_models_status_requires_auth`, `test_models_status_returns_200_for_authenticated`

### VULN-03: `/product-analytics/events` uses inline `require_auth` (MEDIUM)

**Original vulnerability:** `POST /api/v1/product-analytics/events` called
`require_auth(request)` directly inside the function body instead of using
FastAPI's `Depends(require_auth)`. While functionally equivalent for auth
enforcement, the inconsistent pattern meant the auth check was not visible in
the route signature and could be missed during code review or refactoring.

**Severity:** MEDIUM — Inconsistent auth pattern increasing risk of future
auth bypass during refactoring.

**Root cause:** Route handler at `services/api/app/api/v1/routes.py:1029`
called `require_auth(request)` inline instead of declaring it as a dependency.

**Fix:** Changed to `current_user: AuthUser = Depends(require_auth)`.

**Verification:**
- Before: Unauthenticated POST → `403 Forbidden` (CSRF blocks first) or `401 Unauthorized` (auth)
- After: Same behavior, but auth is now declared via FastAPI dependency injection
- Regression test: `test_product_analytics_events_requires_auth`, `test_product_analytics_events_returns_200_for_authenticated`

### VULN-04: Unhandled exception logging may leak sensitive data (MEDIUM)

**Original vulnerability:** The request middleware logged unhandled exceptions
using `_LOGGER.exception(...)`, which includes the full Python traceback.
Tracebacks can contain local variables, including sensitive request data such
as document content, prompt text, or file paths.

**Reproduction:** Any unhandled exception during request processing would log
the full traceback via the `wonderlearn.access` logger.

**Severity:** MEDIUM — Potential sensitive data leakage in application logs.

**Root cause:** `services/api/app/middleware.py:55` used `_LOGGER.exception()`
which automatically includes `exc_info=True` (full traceback).

**Fix:** Changed to `_LOGGER.error()` which logs the error message without
the traceback. Request ID, method, and path are still logged for debugging.

**Verification:**
- Before: Unhandled exception → full traceback in logs
- After: Unhandled exception → error message without traceback in logs
- Regression test: `test_unhandled_exception_does_not_log_stack_trace`

### VULN-05a: `/ready` endpoint unauthenticated (LOW)

**Original vulnerability:** `GET /api/v1/ready` required no authentication
and returned storage health check status.

**Reproduction:** An unauthenticated `GET /api/v1/ready` returned `200 OK`.

**Severity:** LOW — Minimal information disclosure (storage health status).

**Root cause:** Route handler at `services/api/app/api/v1/routes.py:1049`
had no `Depends(require_auth)` dependency.

**Fix:** Added `current_user: AuthUser = Depends(require_auth)`.

### VULN-05b: `/health` endpoint unauthenticated (LOW)

**Original vulnerability:** `GET /api/v1/health` required no authentication
and returned environment, capabilities, and model inference status.

**Reproduction:** An unauthenticated `GET /api/v1/health` returned `200 OK`.

**Severity:** LOW — Minimal information disclosure (environment, capabilities).

**Root cause:** Route handler at `services/api/app/api/v1/routes.py:263`
had no `Depends(require_auth)` dependency.

**Fix:** Added `current_user: AuthUser = Depends(require_auth)`.

### VULN-05c: `/diagrams` endpoint unauthenticated (LOW)

**Original vulnerability:** `GET /api/v1/diagrams` required no authentication
and returned curated diagram configuration data.

**Reproduction:** An unauthenticated `GET /api/v1/diagrams` returned `200 OK`.

**Severity:** LOW — Public reference data, but inconsistent with other system
endpoints that now require auth.

**Root cause:** Route handler at `services/api/app/api/v1/routes.py:2346`
had no `Depends(require_auth)` dependency.

**Fix:** Added `current_user: AuthUser = Depends(require_auth)`.

## Files changed

| File | Change |
|------|--------|
| `services/api/app/api/v1/routes.py` | Added `require_auth` dependency to `/metrics`, `/models/status`, `/ready`, `/health`, `/diagrams`; changed `/product-analytics/events` to use `Depends(require_auth)` |
| `services/api/app/middleware.py` | Changed `_LOGGER.exception()` to `_LOGGER.error()` in unhandled exception handler to avoid logging stack traces |
| `services/api/tests/test_security_remediation.py` (new) | 14 regression tests covering all fixes |
| `services/api/tests/test_health.py` (modified) | Updated 3 tests to authenticate before accessing now-protected endpoints |
| `services/api/tests/test_observability.py` (modified) | Updated 2 tests to authenticate before accessing now-protected endpoints |

## Test results

| Suite | Before M48 | After M48 |
|-------|-----------|-----------|
| Backend tests | 276 passed, 1 skipped | 290 passed, 1 skipped |
| M47 red-team tests | 19 passed | 19 passed (unchanged) |
| M48 regression tests | N/A | 14 passed |
| ruff check (changed files) | N/A | Clean |
| ruff format (changed files) | N/A | Clean |
| Frontend tsc | 24 pre-existing errors | 24 pre-existing errors (unchanged) |
| Frontend vite build | Succeeds | Succeeds |
| M46 prompt-injection tests | 13 passed | 13 passed (unchanged) |

Total: **290 backend tests pass** (276 baseline + 14 new M48 regression tests),
**1 pre-existing skip** (model integration test requiring `WL_RUN_MODEL_INTEGRATION=1`).

## CI integration

The existing CI `security` job (added in M47) already runs:
- `test_security_redteam.py`
- `test_prompt_injection.py`
- `test_tutor.py`

M48's `test_security_remediation.py` should be added to this job for complete
coverage. The CI job blocks the pipeline on any critical or any test failure.

## Remaining risks and accepted limitations

| Risk | Status | Notes |
|------|--------|-------|
| Prompt injection cannot be eliminated | Residual | M46 structural controls reduce blast radius; no detector claimed |
| `/diagnostics` uses token-based auth (not session) | Accepted | Separate token mechanism; intended for operational use |
| Multi-process rate limiting | Untested | In-memory rate limits reset on restart; use reverse proxy for multi-instance |
| Real-world model behavior | Untested | Tests use fake opener; no actual model inference exercised |
| Frontend tsc errors | Pre-existing | 24 errors in `main.tsx` related to commented-out revision queue code; unrelated to M48 |

## Recommended deployment restrictions

1. **Authentication required:** All system endpoints (`/health`, `/ready`, `/metrics`, `/models/status`, `/diagrams`) now require a valid session cookie.
2. **CSRF protection:** All state-changing endpoints require a valid double-submit CSRF token (unchanged from M46).
3. **Rate limiting:** Enabled by default; per-IP, per-path fixed-window limits apply.
4. **Production mode:** `WL_ENVIRONMENT=production` requires HTTPS CORS origins, enabled rate limiting, and bounded request body size.
5. **Loopback-only model:** `WL_TUTOR_BASE_URL` must use HTTP on localhost only (enforced by config validator).

## Recommended next milestone

**M48.5 (or M49):** Add `test_security_remediation.py` to the CI `security` job's pytest invocation for complete coverage. Then proceed to human-review gating for AI-generated content (content studio already marks output unverified; extend to tutor answers and quiz questions with explicit review/approve workflow).
