# M33 Onboarding & First-Time User Experience

WonderLearn M33 adds a lightweight onboarding flow and getting-started checklist to the browser UI. The goal is to make the first experience clear, useful, and genuinely functional: new users understand what WonderLearn does, its limitations, and its privacy model, then complete their first real learning actions (upload a document, ask a grounded question, take a quiz, complete a study session).

## Onboarding flow

A five-step onboarding panel is shown after sign-in when the user has not yet completed or skipped it:

| Step | Title | What it covers |
|------|-------|----------------|
| 1 | What WonderLearn does | Local-first visual learning assistant; upload PDFs, choose diagram concepts, find evidence with citations; nothing is invented. |
| 2 | Your data stays on this device | Local SQLite storage; camera frames never saved; no documents, tutor conversations, or camera frames sent externally; feedback and analytics private to the account. |
| 3 | Upload your first document | Upload a PDF through "Add a learning PDF"; a downloadable, license-safe sample PDF is provided; indexed documents enable evidence lookup. |
| 4 | Ask a grounded question | Answers are built only from retrieved evidence; citations show source document, page, and exact passage. |
| 5 | Try a quiz and plan study | Generate an evidence-backed quiz, record answers, start a study session; local analytics track progress and schedule reviews. |

Each step has "Back", "Next", and "Skip tour" buttons. The final step has "Finish". A "Restart tour" button is always present inside the onboarding panel.

### Skippable and revisitable

- Users can skip the onboarding at any step with the "Skip tour" button.
- After completing or skipping, a "Show onboarding again" link appears next to the sign-out button in the session controls, allowing the onboarding to be revisited at any time.
- Onboarding state is stored in `localStorage` under a key that includes the username (`wl-onboarding-<username>-v1`), so one user's onboarding state never leaks to another user on the same device.

## Getting-started checklist

A checklist section appears on the main dashboard for every signed-in user. Completion is derived exclusively from actual persisted application state — never from button clicks, tour progress, or fabricated data:

| Checklist item | Completion derived from |
|----------------|------------------------|
| Account ready | Always true once signed in |
| First document uploaded and processed | `documents.length > 0` from `GET /api/v1/documents` |
| First grounded question answered | `progress.totalAttempts > 0` OR evidence/tutor result with status answered/found |
| First quiz completed | Any quiz question with `latestOutcome !== null` |
| First study session completed | `analytics.totals.completedStudySessions > 0` |

Each item shows an honest hint (e.g., "Upload a PDF to get started" when incomplete). The checklist updates automatically when underlying records change — for example, after a user uploads a document, the corresponding checklist item marks itself complete on the next data load.

## Sample material

A clearly labeled, license-safe educational sample PDF about photosynthesis is provided for download from the onboarding step "Upload your first document." The sample:

- Is separate from actual user documents — it is downloaded as a file, then the user uploads it through the normal upload flow like any other PDF.
- Is never inserted into the user's documents list automatically — the "First document uploaded" checklist item only completes after the user actually uploads something.
- Does not affect learning analytics or progress metrics in any way.
- Can be removed simply by not uploading it, or by deleting the uploaded document through the normal delete flow.

The sample text is original educational content about photosynthesis, written specifically for this demonstration purpose.

## Accessibility and UX

- **Keyboard navigation:** All onboarding buttons (Back, Next, Skip tour, Finish, Restart tour) are native `<button>` elements, focusable and activatable with Enter/Space.
- **Screen readers:** The onboarding panel uses `aria-labelledby` to announce its title; checklist items use semantic structure with check/uncheck symbols hidden via `aria-hidden="true"` and text labels for the actual state.
- **Responsive layout:** The onboarding card and checklist use responsive max-width constraints and integrate with the existing page grid.
- **Reduced motion:** The existing `@media (prefers-reduced-motion: reduce)` rule disables transitions globally, covering onboarding animations.
- **Error handling:** Upload failures show actionable error messages (e.g., "Could not upload the PDF.") via the existing notice pattern. Empty states are shown honestly (e.g., "You have not uploaded any documents yet").
- **No blocking:** Returning users who have completed or skipped onboarding see the dashboard immediately with no modal or tutorial blocking their workflow. No decorative animations are added.

## Privacy

- Onboarding state (completed/skipped/step) is stored only in `localStorage` on the user's device, keyed by username. It is never sent to any server.
- No documents, tutor conversations, passwords, session tokens, or camera frames are collected as part of onboarding.
- The sample PDF is downloaded and uploaded entirely client-side; nothing about it is reported to external services.

## Verification commands and results

All commands run from the repository root.

### Frontend

```powershell
npm test --prefix apps/web
npm run typecheck --prefix apps/web
npm run build --prefix apps/web
```

Result: 40 passed, 0 failed. TypeScript typecheck passed. Production build succeeded (dist/ emitted).

### Backend (no changes; regression check)

```powershell
uv run --project services/api --extra dev pytest
```

Result: 162 passed, 1 skipped (model integration requires external cached model), 0 failed.

### Lint and format

```powershell
uv run --project services/api --extra dev ruff check services/api
uv run --project services/api --extra dev ruff format --check services/api
```

Result: All checks passed, 60 files already formatted.

### Evaluation suite

```powershell
uv run --locked --project services/api --extra dev --extra semantic python evaluations/run_suite.py
```

Result: Exit 0. Backend tests: 162 passed, 0 failed. Retrieval evaluation: lexical offline mode, recall@5 = 1.000, citation validity = 1.0, unsupported abstention recall = 1.0.

## Known limitations

- Onboarding state is stored in `localStorage`, which is per-browser and per-origin. Clearing browser data resets onboarding state; it is not synced across devices.
- The checklist derives completion from the most recently loaded API state. If a user uploads a document in another tab, the checklist will not update until the page is reloaded or data is refetched.
- The sample PDF is a minimal demonstration document; real learning materials will produce richer evidence and citation results.
- Tutor answer quality, quiz validity, and onboarding effectiveness have not been evaluated on real user data.
