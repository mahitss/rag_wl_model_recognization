# M31 Private Beta — Feedback System

WonderLearn M31 adds a private-beta feedback feature for authenticated accounts.
Beta users can submit bug reports, usability feedback, and feature requests,
view their own submissions, edit or withdraw them, and administrators can
review and update feedback status. All data stays local; nothing is sent to
external services.

## Endpoints

| Method | Path | Access | Purpose |
|--------|------|--------|---------|
| `POST` | `/api/v1/feedback` | Authenticated | Submit feedback (category, title, description) |
| `GET` | `/api/v1/feedback` | Authenticated | List the current user's feedback |
| `PATCH` | `/api/v1/feedback/{feedbackId}` | Owner only | Edit or withdraw (status=`withdrawn`) own feedback |
| `GET` | `/api/v1/feedback/admin` | Admin only | List all feedback for review |
| `PATCH` | `/api/v1/feedback/{feedbackId}/status` | Admin only | Update review status |

## Privacy behavior

Feedback rows store only:

- The submitter's internal account ID (`user_id`)
- A sanitized category (`bug`, `usability`, `feature_request`, `other`)
- A short title (max 200 characters)
- The description text (max 4,000 characters)
- A review status (`new`, `reviewing`, `planned`, `resolved`, `withdrawn`)
- Creation and update timestamps

The API never stores raw document contents, tutor conversations, passwords,
session tokens, camera frames, or images in feedback records. Feedback is
private to its owner and authorized administrators. Client-supplied role or
user IDs are never trusted; server-side authorization is derived from the
authenticated session and database state.

## Admin role

Administrator access is opt-in and never granted by default. Two mechanisms
exist and are checked server-side:

1. `is_admin` flag on the local `users` record (set via `mark_user_admin`)
2. `WL_ADMIN_USERNAMES` allowlist in `.env` (checked at login and session
   resolution)

A regular account cannot escalate itself to admin. The admin allowlist must be
explicitly configured before using the admin review flow.

## Local beta walkthrough

### 1. Start the application

From the repository root, open two PowerShell terminals.

Terminal 1 — API:

```powershell
uv run --project services/api uvicorn app.main:app --app-dir services/api --host 127.0.0.1 --port 8000
```

Terminal 2 — frontend:

```powershell
npm run dev --prefix apps/web
```

Open `http://127.0.0.1:5173`.

### 2. Create test accounts

Register two accounts through the UI at `http://127.0.0.1:5173`:

- **beta-user** — a regular beta tester
- **beta-admin** — an administrator (make admin via the database or allowlist)

To make an account an administrator, set `WL_ADMIN_USERNAMES=beta-admin` in
`.env` and restart the API, then log in as `beta-admin`. The `isAdmin` field
on `/api/v1/auth/me` confirms admin status.

Alternatively, directly update the SQLite database:

```powershell
sqlite3 data/local/wonderlearn.sqlite3 "UPDATE users SET is_admin = 1 WHERE username = 'beta-admin';"
```

### 3. Submit feedback as a beta user

1. Sign in as `beta-user`.
2. Scroll to the "Private beta · help us improve" section at the bottom of the page.
3. Choose a category (Bug report, Usability, Feature request, Other).
4. Enter a title and description.
5. Click "Send feedback."
6. Confirmation appears only after the report is saved: "Your feedback was saved."
7. The submission appears in "Your feedback" below the form.

### 4. Edit or withdraw feedback

From "Your feedback":

- Click **Edit** on a non-withdrawn item to open the edit form, change the
  category/title/description, and click "Save changes."
- Click **Withdraw** on a non-withdrawn item to set its status to `withdrawn`.
  Withdrawn items can no longer be edited.

### 5. Review feedback as an administrator

1. Sign in as `beta-admin`.
2. The "Admin review" section appears below your own feedback.
3. It lists all feedback from all users with submitter, category, status, and
   updated time.
4. Change a feedback item's status using the dropdown (New, Reviewing, Planned,
   Resolved, Withdrawn). The change is saved server-side and reflected in the
   table.
5. Click "View description" on any row to read the full description.

## Known limitations

- Feedback is stored in the local SQLite database only. There is no export,
  email notification, or external ticketing integration.
- Rate-limit state is in-memory per API process and resets on restart. For
  multi-instance deployments, enforce shared limits at a trusted reverse proxy.
- Withdrawal sets status to `withdrawn`; the record is retained (not hard
  deleted) for audit purposes. Deleting the user account cascades and removes
  their feedback rows through the foreign-key constraint.
- The admin allowlist (`WL_ADMIN_USERNAMES`) is read from `.env` at startup;
  changing it requires an API restart.
- No per-user opt-in UI for feedback exists; the feature is available to all
  authenticated accounts by default in the private beta.
- Tutor answer quality, quiz validity, and feedback usefulness have not been
  evaluated on real user data.

## Verification commands and results

All commands run from the repository root. Results below are from the actual
M31 verification run.

### Backend

```powershell
uv run --project services/api --extra dev pytest
```

Result: 150 passed, 1 skipped (model integration requires external cached model), 0 failed.

Feedback-specific tests (`test_feedback.py`): 5 passed
- Feedback submission and owner updates
- Admin review requires explicit admin
- Anonymous access rejected (401/403)
- Oversized input rejected (422)
- Rate limit applies (429)

### Lint and format

```powershell
uv run --project services/api --extra dev ruff check services/api
uv run --project services/api --extra dev ruff format --check services/api
```

Result: All checks passed, no formatting issues.

### API contract validation

```powershell
python -m app.contract_validation --project services/api
```

Result: Passed — frontend routes match backend OpenAPI schema.

### Frontend

```powershell
npm test --prefix apps/web
npm run typecheck --prefix apps/web
npm run build --prefix apps/web
```

Result: 40 passed, 0 failed. TypeScript typecheck passed. Production build
succeeded (dist/ emitted).

### Evaluation suite

```powershell
uv run --locked --project services/api --extra dev --extra semantic python evaluations/run_suite.py
```

Result: Exit 0. Backend tests: 150 passed, 0 failed. Retrieval evaluation:
lexical offline mode, recall@5 = 1.000, citation validity = 1.0, unsupported
abstention recall = 1.0.

### CI

The existing GitHub Actions workflow (`.github/workflows/ci.yml`) runs backend
tests, Ruff, format, contract validation, frontend tests, typecheck, build,
dependency audit, and the evaluation suite on every push and pull request to
`main`. No changes to CI configuration are required for M31.