# Authentication and user-data isolation

WonderLearn's local API uses SQLite-backed, cookie-authenticated accounts. The
implementation is intended for a single API process behind HTTPS when deployed;
it is not a substitute for a multi-instance identity or rate-limiting service.

## Authentication and browser security

- Passwords are stored as Argon2id hashes using `argon2-cffi` defaults. Login
  returns one generic error for unknown accounts and incorrect passwords and
  verifies a dummy Argon2 hash for unknown usernames.
- Registration returns the same response for new and already-registered
  usernames. Accounts are not automatically signed in after registration.
- Login creates a random session token. SQLite stores only its SHA-256 digest;
  the token is sent only in the `wl_session` HttpOnly cookie. Cookies use
  `SameSite=Lax`, and `Secure` is enabled in production. Sessions expire after
  `WL_SESSION_TTL_SECONDS` (30 days by default); logout deletes the database
  session and clears both cookies.
- State-changing `/api/v1/` requests require the double-submit CSRF token:
  fetch `/api/v1/auth/csrf-token`, then send the returned value in
  `X-CSRF-Token`. The matching `wl_csrf_token` cookie is intentionally readable
  by the browser; the session cookie is not. CORS allows credentials only for
  the explicitly configured origins.
- Serve the browser UI and API on the same site (for example, through one
  reverse proxy). `SameSite=Lax` cookies will not work for a frontend hosted on
  a different site even if that origin is added to CORS.

Authentication endpoints:

| Endpoint | Access |
|---|---|
| `POST /api/v1/auth/register` | Anonymous; CSRF required |
| `POST /api/v1/auth/login` | Anonymous; CSRF required |
| `POST /api/v1/auth/logout` | Authenticated; CSRF required |
| `GET /api/v1/auth/me` | Authenticated |
| `GET /api/v1/auth/csrf-token` | Anonymous |

## Ownership and migration

All user-data API routes derive the owner from the verified session. Ownership
is passed into storage operations and included in database lookups, including
documents/chunks/retrieval, images and regions, quizzes/attempts/citations,
study sessions/reviews, knowledge graphs, analytics, and coach recommendations.
Client-supplied owner IDs are not used to authorize access. A resource owned by
another account is treated as not found.

SQLite schema migrations 9 and 10 add owner-aware keys and reconcile ownership
between existing parent and child records. Existing rows without a provable
owner remain unowned (`user_id IS NULL`); they are not assigned to the first
new account and authenticated APIs do not expose them. Migration aborts if it
finds conflicting parent/child ownership rather than guessing. The migrations
preserve existing rows and do not delete them. Back up the SQLite database
before upgrade and plan a separate, explicit administrative import if legacy
data needs to become account-owned.

## Abuse controls and deployment

- Request bodies are limited to 12 MiB by default; PDF/image and model
  processing have additional configured bounds.
- Per-IP, per-path fixed-window rate limits are enabled by default. Login and
  other auth endpoints and expensive upload/model endpoints have separate
  limits. Rate-limit state is in memory and therefore is per process and resets
  on restart; keep a single API process or enforce shared limits at a trusted
  reverse proxy.
- Client identity does not trust `X-Forwarded-For`. Behind a proxy, requests
  may share the proxy address for limiting; configure network-level limits
  rather than accepting untrusted forwarded headers.
- Production startup requires HTTPS CORS origins, enabled rate limiting, and a
  bounded request body. Set `WL_ENVIRONMENT=production`, use HTTPS end to end,
  and restrict CORS to the actual same-site frontend origin.
- Logs omit request bodies and redact potentially sensitive HTTP-client
  arguments. Do not enable payload logging or expose local database files.

## Product analytics and privacy controls

WonderLearn records a small, self-hosted product-analytics feed for feature
adoption and recovery checks. The analytics table stores only:

- a per-user internal `user_id` for user-scoped reports,
- a sanitized event name and feature label,
- a result (such as `success`, `failure`, `accepted`, or `dismissed`),
- an event timestamp, and
- a small metadata payload limited to operational values such as duration, count,
  mode, or a non-sensitive status flag.

It never stores raw document contents, prompts, answers, passwords, session
cookies, tokens, camera frames, or IP addresses. The backend enforces user
isolation on every analytics read by querying only the authenticated user's
records. If the user account is deleted, the related product-events rows are
removed through the table's foreign-key cascade.

Product analytics is **off by default**. An operator must explicitly opt in
through the local configuration; there is no per-user opt-in UI yet, so the
feature must remain disabled for deployments where operator opt-in does not
meet the applicable consent requirements. When enabled, the analytics
subsystem is configured with:

- `WL_ANALYTICS_ENABLED` (default `false`)
- `WL_PRODUCT_ANALYTICS_RETENTION_DAYS` (default `90`)

Records older than the retention window are pruned when a summary is requested.
The local analytics dashboard shows only aggregated, per-user trends and never
a raw event stream with sensitive content. Because the app stays local-first,
there is no external telemetry provider.
Pruning or deleting an account from the live database does not remove those
records from already-created backup archives; the configured backup retention
policy controls how long those archives remain.

## Private beta feedback and administrator review

WonderLearn also includes a private-beta feedback feature for authenticated
accounts. Feedback rows store only the submitter's internal account ID, a
sanitized category, a short title, the description text, and a review status.
The API never stores raw document contents, session tokens, password material,
images, or camera frames in feedback records. They are kept private to the
owner and administrators who have an explicit `is_admin` flag set in the local
user record or an allowlist configured with `WL_ADMIN_USERNAMES`.

The workflow is intentionally conservative:

- A user may submit, edit, and withdraw their own feedback while the item is
  still active.
- Only an administrator may list all feedback or change its review status.
- Client-supplied role or user IDs are ignored; server-side authorization is
  derived from the authenticated session and the database state.

Because the feature is private-beta only, it is kept local and does not send
telemetry externally. Existing backup and retention rules still apply to the
feedback table, and an operator must explicitly configure the admin allowlist
before using the admin review flow.

No public deployment is performed by this milestone. Before public use, review
proxy/TLS configuration, backup and restore procedures, registration abuse,
password reset/account recovery, multi-process rate limiting, and the local data
retention schedule for analytics.
