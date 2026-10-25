# M32 Feedback Analysis & Product Improvement Loop

WonderLearn M32 adds a feedback analysis and product improvement loop on top of the
M31 private-beta feedback system. Administrators can filter and sort feedback,
transition it through an expanded review lifecycle, record resolution notes,
convert feedback into tracked improvement tasks, and prioritize those tasks with
a transparent, documented formula. All data stays local; nothing is sent to
external services or AI models.

## Endpoints

| Method | Path | Access | Purpose |
|--------|------|--------|---------|
| `GET` | `/api/v1/feedback/admin` | Admin only | List all feedback with filtering (category, status, date range) and sorting (recency, created) |
| `PATCH` | `/api/v1/feedback/{feedbackId}/status` | Admin only | Update feedback review status |
| `PATCH` | `/api/v1/feedback/admin/{feedbackId}` | Admin only | Update feedback including resolution notes and improvement task link |
| `POST` | `/api/v1/feedback/admin/improvement-tasks` | Admin only | Create a tracked improvement task from feedback |
| `GET` | `/api/v1/feedback/admin/improvement-tasks` | Admin only | List improvement tasks with optional status filter and sorting |
| `PATCH` | `/api/v1/feedback/admin/improvement-tasks/{taskId}` | Admin only | Update an improvement task; priority score recomputes when factors change |
| `POST` | `/api/v1/feedback/admin/improvement-tasks/{taskId}/feedback/{feedbackId}` | Admin only | Link a feedback item to an improvement task; report count recomputes |
| `DELETE` | `/api/v1/feedback/admin/improvement-tasks/{taskId}/feedback/{feedbackId}` | Admin only | Unlink a feedback item from an improvement task |
| `GET` | `/api/v1/feedback/admin/prioritization` | Admin only | Return the documented prioritization formula and field classifications |

## Prioritization method

The priority score is a transparent, deterministic blend of measured data and
administrator estimates:

```
priority_score = severity×20 + impact×15 + report_count×10 +
                 reproducibility×10 + workflow_frequency×10 −
                 implementation_effort×5
```

### Field classifications

| Field | Type | Scale | Source |
|-------|------|-------|--------|
| severity | Estimated | 1–5 | Administrator judgment |
| impact | Estimated | 1–5 | Administrator judgment |
| report_count | **Measured** | 0+ | Count of distinct feedback items linked to the task |
| reproducibility | Estimated | 1–5 | Administrator judgment |
| workflow_frequency | Estimated | 1–5 | Administrator judgment |
| implementation_effort | Estimated | 1–5 | Administrator judgment (higher = more effort = lower score) |

### Result range

Approximately 0 to 150. Higher scores indicate higher priority.

### Important distinctions

- **report_count** is the only measured value. It is derived from stored feedback
  rows linked to the task and updates automatically when feedback is linked or
  unlinked.
- All other fields are administrator estimates on a 1–5 scale. They are never
  treated as observed facts and should be reviewed as opinions, not data.
- AI-generated classifications are never stored as facts. The system does not
  call external AI services for categorization or prioritization.

## Feedback status lifecycle

Feedback can transition through the following statuses:

| Status | Meaning |
|--------|---------|
| new | Submitted, not yet reviewed |
| reviewing | Under administrator review |
| planned | Approved for future work |
| in_progress | Actively being worked on |
| resolved | Addressed; resolution notes recorded |
| withdrawn | Withdrawn by the submitter |
| declined | Reviewed; will not be addressed |

## Improvement task statuses

| Status | Meaning |
|--------|---------|
| proposed | Task identified, not yet started |
| in_progress | Work is underway |
| resolved | Fix implemented and verified |
| declined | Task will not be pursued |

## Privacy and security

- Every backend operation enforces admin permissions. Client-supplied role or
  user IDs are never trusted; authorization is derived from the authenticated
  session and database state.
- Feedback remains private to its owner and authorized administrators.
- Resolution notes store only administrator-entered text (max 4,000 characters).
- No document contents, tutor conversations, passwords, session tokens, camera
  frames, or images are stored in feedback or improvement task records.
- No external AI services are called for categorization, prioritization, or
  any other feedback processing.
- Feedback and improvement tasks follow the same backup, retention, and
  deletion rules as other user data. Deleting a user account cascades and
  removes their feedback rows and linked improvement task references through
  foreign-key constraints.

## Database schema (SCHEMA_VERSION 13)

The migration from version 12 to 13:

1. Creates `improvement_tasks` table with priority score, factor fields, and
   implementation tracking.
2. Creates `improvement_task_feedback` junction table for many-to-many
   feedback-to-task links.
3. Recreates `feedback_items` with expanded status CHECK (adds `in_progress`,
   `declined`) and new columns: `resolution_notes`, `improvement_task_id`.

## Admin workflow

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

Register two accounts through the UI:

- **beta-user** — a regular beta tester
- **beta-admin** — an administrator

Make `beta-admin` an administrator by setting `WL_ADMIN_USERNAMES=beta-admin` in
`.env` and restarting the API, or by directly updating the SQLite database:

```powershell
sqlite3 data/local/wonderlearn.sqlite3 "UPDATE users SET is_admin = 1 WHERE username = 'beta-admin';"
```

### 3. Review feedback as an administrator

1. Sign in as `beta-admin`.
2. Scroll to the "Admin review" section in the feedback panel.
3. Use the filter controls to narrow feedback by category, status, or date range.
4. Use the sort controls to order by last updated or date created.
5. Update a feedback item's status using the dropdown (New, Reviewing, Planned,
   In progress, Resolved, Withdrawn, Declined).
6. Add resolution notes using the "Save notes" button.
7. Link feedback to an improvement task using the "Link to task" button.

### 4. Create and prioritize improvement tasks

1. In the "Improvement tasks" section, click "Create task."
2. Enter a title, description, and administrator estimates (severity, impact,
   reproducibility, workflow frequency, implementation effort).
3. Optionally link existing feedback IDs (comma-separated) to the task. The
   report count is measured automatically from linked feedback.
4. Optionally specify a regression test that verifies the fix.
5. Click "Create task." The priority score is calculated from the documented
   formula.
6. Tasks can be edited later. Changing priority factors recomputes the score.
7. Mark a task as "Implemented" when the fix is complete; record the regression
   test that verifies it.

## Verification commands and results

All commands run from the repository root. Results below are from the actual
M32 verification run.

### Backend

```powershell
uv run --project services/api --extra dev pytest
```

Result: 162 passed, 1 skipped (model integration requires external cached model), 0 failed.

Feedback-specific tests (`test_feedback.py`): 17 passed
- Feedback submission and owner updates
- Admin review requires explicit admin
- Anonymous access rejected (401/403)
- Oversized input rejected (422)
- Rate limit applies (429)
- Admin filtering by category and status
- Admin sorting by recency and created
- Status transitions including new statuses (in_progress, declined)
- Admin resolution notes
- Invalid status rejection (422)
- Improvement task creation and duplicate prevention
- Improvement task priority recompute on link/unlink
- Improvement task creation with linked feedback
- Improvement task list, status filter, and update
- Improvement task not found (404)
- Admin endpoints require admin (403)

### Lint and format

```powershell
uv run --project services/api --extra dev ruff check services/api
uv run --project services/api --extra dev ruff format --check services/api
```

Result: All checks passed, 60 files already formatted.

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

Result: Exit 0. Backend tests: 162 passed, 0 failed. Retrieval evaluation:
lexical offline mode, recall@5 = 1.000, citation validity = 1.0, unsupported
abstention recall = 1.0.

### CI

The existing GitHub Actions workflow (`.github/workflows/ci.yml`) runs backend
tests, Ruff, format, contract validation, frontend tests, typecheck, build,
dependency audit, and the evaluation suite on every push and pull request to
`main`. No changes to CI configuration are required for M32.

## Known limitations

- The prioritization formula is an operational heuristic, not a validated
  measure of user impact or business value.
- Administrator estimates (severity, impact, etc.) are subjective judgments and
  are not validated against actual outcomes.
- Duplicate prevention is based on exact title matching (case-insensitive);
  semantically similar but differently titled tasks are not detected.
- Feedback linking to improvement tasks is manual; there is no automatic
  suggestion of related feedback.
- No export, email notification, or external ticketing integration exists.
- Rate-limit state is in-memory per API process and resets on restart.
- Withdrawal sets status to `withdrawn`; the record is retained for audit
  purposes. Deleting the user account cascades and removes their feedback rows
  through the foreign-key constraint.
- Tutor answer quality, quiz validity, and feedback usefulness have not been
  evaluated on real user data.
