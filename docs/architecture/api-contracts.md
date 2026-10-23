# API contracts (proposal)

This document includes the broader product contract. Local PDF retrieval, optional semantic/hybrid retrieval, curated diagram selection, the optional grounded tutor, and evidence-backed quiz/progress endpoints are implemented. `/answers` remains extractive. Generation is a separate optional path and abstains when evidence is absent.

Base path `/api/v1`; JSON camelCase in browser and explicit Pydantic mapping at API boundary. All IDs are opaque strings. Enforce request-size and field limits. Local origins only in MVP; configure CORS narrowly.

Versioning policy: this project maintains a single `/api/v1` surface while the app is still local-first and in active development. New capabilities are added without changing the base route unless a backwards-incompatible schema change is required; when that happens, introduce `/api/v2` only with a documented migration window and a compatibility fallback. The contract is validated against the UI by a deterministic script that compares the FastAPI OpenAPI routes to the frontend `fetch()` URLs and is run in CI.

## Endpoints

| Method/path | Purpose |
|---|---|
| `POST /documents` | Multipart upload, validate and ingest PDF/text |
| `GET /documents` | List document metadata/status |
| `GET /documents/{documentId}` | Status and extraction summary |
| `DELETE /documents/{documentId}` | Delete document, chunks, indexes and derived assets |
| `POST /retrieve` | Search evidence without generation; useful baseline/debug endpoint |
| `POST /answers` | Resolve concept + question, retrieve evidence, return grounded answer or abstention |
| `GET /tutor/status` | Report whether a loopback local tutor model endpoint is configured |
| `POST /tutor/answers` | Retrieve source passages and optionally generate an evidence-grounded explanation |
| `POST /quizzes` | Retrieve evidence and ask the optional local model for validated multiple-choice and short-answer questions |
| `GET /quizzes/{quizId}` | Load a persisted local quiz; `review_incorrect=true` filters to latest incorrect or ambiguous questions |
| `POST /quizzes/{quizId}/questions/{questionId}/attempts` | Deterministically score and persist a response, then return explanation and stored citations |
| `GET /progress` | Return topic-level latest outcomes and recent local review history |
| `GET /learning-analytics` | Return persisted quiz, completed-session, daily activity, topic mastery, and due-review analytics |
| `POST /study-sessions` | Start an explicitly learner-initiated local study session |
| `POST /study-sessions/{sessionId}/complete` | Record the learner's explicit session completion |
| `GET /study-coach/plan` | Build and persist today's deterministic, source-linked recommendations from quiz/review/session/graph records |
| `PATCH /study-coach/recommendations/{recommendationId}` | Accept, edit, dismiss, or complete a persisted recommendation |
| `POST /study-coach/recommendations/{recommendationId}/explanation` | Optionally explain a recommendation through the existing grounded tutor and its stored citations |
| `GET /study-coach/plan` | Build and persist today's deterministic, source-linked recommendations from quiz/review/session/graph records |
| `PATCH /study-coach/recommendations/{recommendationId}` | Accept, edit, dismiss, or complete a persisted recommendation |
| `POST /study-coach/recommendations/{recommendationId}/explanation` | Optionally explain a recommendation through the existing grounded tutor and its stored citations |
| `GET /knowledge-graph` | List locally stored evidence-backed concepts |
| `POST /knowledge-graph/extract` | Extract bounded candidate concepts/relationships from selected stored chunks with the optional loopback tutor model |
| `GET /knowledge-graph/concepts/{conceptId}` | Return a concept, connected concepts, exact supporting passages, and document/chunk/page provenance |
| `POST /knowledge-graph/relationships/{relationshipId}/verify` | Mark a candidate relationship human-verified after explicit review |
| `GET /diagrams` | List the authored photosynthesis diagram, normalized selectable regions, concepts, and aliases |
| `POST /diagrams/{diagramId}/regions/{regionId}/evidence` | Resolve a curated region server-side and retrieve cited evidence for its configured concept |
| `POST /images` | Validate and store a local PNG/JPEG, normalize it, and report local OCR status/text |
| `GET /images` | List saved educational image metadata |
| `GET /images/{imageId}` | Return an image, its normalized regions, OCR text, and currently valid linked citations |
| `GET /images/{imageId}/content` | Return the sanitized image bytes from local storage with no-store headers |
| `DELETE /images/{imageId}` | Delete an uploaded image and cascade its regions and image-to-passage links |
| `POST /images/{imageId}/regions` | Save a normalized rectangle and optionally resolve a server-curated diagram concept |
| `POST /images/{imageId}/regions/{regionId}/evidence` | OCR-search the selected region against indexed documents and persist actual retrieved chunk citations |
| `POST /images/{imageId}/regions/{regionId}/tutor` | Retrieve passages for the selected image region and answer through the existing grounded tutor |
| `GET /concepts/{conceptId}` | Resolve supported concept metadata/aliases |
| `POST /sessions` | Optional ephemeral learning session |
| `POST /sessions/{sessionId}/responses` | Submit answer against authored question/rubric |
| `DELETE /sessions/{sessionId}` | Delete local session record |
| `GET /health` | Liveness and enabled capability flags; no secrets or file paths |

No WebSocket is justified for the MVP: camera work stays in browser, upload and retrieval are request/response, and generation can return a complete response. Add streaming only if measured local generation latency and UI need justify it.

## Shipped curated diagram contract

`GET /api/v1/diagrams` returns `diagramId`, title, description, the web asset URL, aspect ratio, and regions. Each region has a stable `regionId`, configured `conceptId`, display name, aliases, and normalized `{x, y, width, height}` geometry. Coordinates are fractions of the diagram image dimensions, bounded to `[0, 1]`; rectangles must be non-empty, fit within the image, and not overlap.

## Uploaded educational images

`POST /api/v1/images` accepts only actual PNG/JPEG image content, irrespective of the client MIME claim. Pillow verifies and fully decodes the bytes, rejects animation and malformed files, enforces configured byte/dimension/pixel limits, applies EXIF orientation, and re-encodes to strip metadata. The returned `imageId` is generated by the server. Sanitized image bytes, OCR output/status, and region records are stored in the configured local SQLite database. `GET /images/{imageId}/content` serves only those sanitized bytes with `Cache-Control: private, no-store`; no camera frames enter this API.

OCR uses optional `pytesseract` with the locally installed Tesseract executable. Status is explicitly `available`, `empty`, `unavailable`, or `failed`; unavailable OCR never returns fabricated text. Image regions use normalized `{x,y,width,height}` rectangles relative to the oriented, stored image, each coordinate in `[0,1]`, positive dimensions, and right/bottom bounds within `1`. Regions can optionally associate with a concept ID only when the diagram and concept resolve through the server's curated catalog. Region evidence retrieval combines only region OCR text, an optional curated retrieval query, and the learner's tutor question; clients cannot submit document filters or citations. The retrieval service provides actual stored chunk citations, which are retained as region provenance and exposed only while the source chunks exist. Tutor responses reuse the existing evidence-only generator and citation-label validation. Empty results explicitly abstain. Deleting an image cascades its regions and citations; deleting a source document cascades the linked image citation while leaving the learner's image region intact.

`POST /api/v1/diagrams/{diagramId}/regions/{regionId}/evidence` accepts only `{ "limit": 5 }` (limit 1–10). Unknown fields such as `query`, `conceptId`, or `documentIds` are rejected. The server resolves the region from its curated catalog and calls the existing retrieval service with the configured concept query and the user's indexed library. A response returns `status` (`evidence_found` or `insufficient_evidence`), the resolved concept, retrieval mode/fallback reason, and the retrieval service's citation records. Citations retain the stored chunk ID as `citationId`, along with document ID, display filename, page number, chunk index, source offsets, and exact stored text. No evidence is synthesized when there is no match.

`POST /api/v1/tutor/answers` accepts a question, optional indexed document IDs, retrieval mode/limit, and either both a curated `diagramId`/`conceptId` or neither. Unknown concepts are rejected server-side. The existing retrieval service assembles evidence; generation receives only those passages. The optional local server must expose the OpenAI-compatible `/chat/completions` API at a loopback URL. The model returns inline evidence labels (`[E1]`) and a matching label list. The API rejects malformed output or any label absent from the current evidence pack, then maps labels to stored citation records itself. Empty evidence abstains without calling the model. If the local model is disabled or unreachable, the endpoint returns a safe `TUTOR_MODEL_UNAVAILABLE` error. Existing `/answers` output is unchanged.

`POST /api/v1/quizzes` accepts a topic, question count, and optional document/concept scope. It uses the same retrieval service and optional loopback model adapter as the tutor. Empty retrieval returns `insufficient_evidence` and never calls the model. Generated questions must match the strict multiple-choice/short-answer schema, cite only retrieved evidence labels, and pass a conservative lexical support check for the question, expected answer, and explanation. The API persists questions and foreign-key citations to actual chunks. Correct answers are withheld from quiz responses until submission.

`POST /api/v1/quizzes/{quizId}/questions/{questionId}/attempts` accepts an answer and records it locally. Multiple-choice answers use exact option IDs. Short answers are compared with the expected answer and accepted alternatives after Unicode normalization, case-folding, and tokenization; token-set F1 >= 0.80 is correct, <= 0.20 is incorrect, and intermediate scores are ambiguous. This is a conservative lexical heuristic, not semantic grading. The response returns the explanation and citations resolved from current stored chunks. `GET /api/v1/progress` summarizes each question's latest outcome by topic and returns up to 100 recent attempts. Deleting a source document cascades its cited quiz questions and related attempt history so deleted source passages are not retained as stale citations.

## Learning analytics and local study sessions

`GET /api/v1/learning-analytics` derives its output from persisted quiz attempts, quiz citation rows, current source chunks, knowledge graph concepts, local study-session rows, and the current per-question review schedule. It returns totals, topic comparisons, daily activity for the current UTC day and preceding 13 days, recent activity, citations, active sessions, and upcoming/overdue review items. It does not estimate activity from page views or fabricate empty-history values. Accuracy is `null` when there are no scored answers; ambiguous quiz outcomes are not counted as correct or incorrect.

Topic mastery is the weighted accuracy of each question's latest outcome when that outcome is scored. For a scored result with age `d` days, weight is `2^(-d/30)`; the mastery score is weighted correct outcomes divided by total scored weight. Ambiguous latest results are excluded. The operational weak-topic marker is mastery below 0.60; this threshold and formula are reporting rules, not validated measures of knowledge or learning.

Quiz attempts schedule a local review item per question: one day after incorrect or ambiguous outcomes and seven days after correct outcomes. A later attempt replaces that question's due date. Due means `dueAt <= current UTC time`. `POST /api/v1/study-sessions` records a learner-initiated topic and start time; `POST /api/v1/study-sessions/{sessionId}/complete` records explicit completion. These records are local to the SQLite database. Deleting source material cascades its quiz/citation/attempt/review data; independently recorded study sessions are retained because they have no source-document association. Completing a session records the learner's action, not proof of learning.

## Personalized study coach

`GET /api/v1/study-coach/plan` creates at most three deterministic recommendations for the current UTC date. It only recommends topics with a persisted quiz attempt and at least one currently stored quiz citation. Priority is overdue reviews (+100 each), the analytics weak-topic review flag (+40), latest incorrect questions (+10 each, capped at 3), ambiguous questions (+5 each, capped at 3), graph neighbors (+1 each, capped at 5), and completed study sessions on that topic today (-10 each, capped at 2). Ties are ordered by topic name. This is an operational scheduling heuristic, not a diagnosis or validated mastery estimate. Reasons expose actual contributing record counts and graph links; candidate graph edges remain labeled candidate.

Recommendations are persisted by UTC date and topic in SQLite, so repeated reads preserve accept/edit/dismiss/complete decisions. Editing may change only the displayed plan title and estimated minutes; it cannot replace the source topic, citations, or graph IDs. Source citations are resolved from current quiz citation rows on every response. When source deletion cascades related quiz material, the coach omits the now unsupported recommendation. Every recommendation includes a direct quiz topic, current document/chunk/page citations, and an optional graph concept link.

The rule-based plan works without a tutor model. An explicit explanation request may use the already configured loopback tutor with only that recommendation's stored citation passages; standard tutor abstention and citation-label validation apply. When no model is configured, rule reasons and source passages remain available, while the optional explanation endpoint reports model-unavailable. Empty attempt history returns an honest empty state.

## Knowledge graph

Graph extraction is an optional local-model operation over stored chunks, bounded to at most 20 chunks per request. `nextAfterChunkId` provides a stable cursor for processing larger libraries in repeated batches. Extracted output is schema-checked, and every concept and relationship must quote text that occurs verbatim in its source chunk. The server derives stable concept IDs from normalized canonical names; explicit aliases are stored for display but do not trigger merges. Potentially ambiguous alias matches remain separate concepts. Relationships are persisted as `candidate` and carry the originating document ID, chunk ID, page number, and supporting text. A human can explicitly mark a candidate `human_verified`; extraction itself never does so. Source deletion cascades evidence and relationships, then removes concepts with no remaining provenance. Graph concept IDs can scope the existing tutor endpoint through `graphConceptId`; retrieval and citation construction remain server-controlled.

## Representative schemas

```json
POST /retrieve
{
  "query": "What does the alveolus exchange?",
  "conceptId": "respiratory.alveolus",
  "documentIds": ["doc_opaque"],
  "limit": 5
}
```

```json
{
  "results": [{
    "evidenceId": "ev_opaque",
    "documentId": "doc_opaque",
    "documentName": "biology.pdf",
    "pageStart": 12,
    "pageEnd": 12,
    "section": "Gas exchange",
    "text": "...",
    "retrieval": {"lexicalRank": 1, "denseRank": null}
  }]
}
```

`POST /answers` input: `{ "question": string, "conceptId": string|null, "detailLevel": "beginner"|"technical", "documentIds": string[]|null }`. Output: `{ "answer": string, "status": "supported"|"partial"|"insufficient_evidence", "claims": [{"text": string, "evidenceIds": string[]}], "citations": [{"evidenceId": string, "documentId": string, "pageStart": number|null, "pageEnd": number|null, "section": string|null}], "example": string|null, "followUpQuestion": string|null }`.

Upload response: `{ "documentId": string, "status": "indexed"|"partial"|"rejected", "pagesProcessed": number, "warnings": string[] }`. Do not expose stack traces or parser internals.

## Error envelope

`{ "error": { "code": string, "message": string, "requestId": string, "details": object|null } }`. Codes include `INVALID_FILE`, `FILE_TOO_LARGE`, `UNSUPPORTED_FORMAT`, `PARSE_FAILED`, `DOCUMENT_NOT_FOUND`, `INVALID_REQUEST`, `NO_EVIDENCE`, and `INTERNAL_ERROR`. User-facing messages must not leak local absolute paths, stack traces, or secrets.

## Validation and versioning

Set documented max upload bytes/pages, query length, result limit, and session payload sizes during implementation. Reject extra unexpected fields where practical. Keep `/api/v1` stable; breaking schema changes use `/api/v2`. Document OpenAPI schema generated from typed models. Never accept client-supplied citation text as trusted provenance.
