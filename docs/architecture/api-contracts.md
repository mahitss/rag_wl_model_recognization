# API contracts (proposal)

This document includes the broader product contract. Local PDF retrieval, optional semantic/hybrid retrieval, curated diagram selection, the optional grounded tutor, and evidence-backed quiz/progress endpoints are implemented. `/answers` remains extractive. Generation is a separate optional path and abstains when evidence is absent.

Base path `/api/v1`; JSON camelCase in browser and explicit Pydantic mapping at API boundary. All IDs are opaque strings. Enforce request-size and field limits. Local origins only in MVP; configure CORS narrowly.

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
| `GET /diagrams` | List the authored photosynthesis diagram, normalized selectable regions, concepts, and aliases |
| `POST /diagrams/{diagramId}/regions/{regionId}/evidence` | Resolve a curated region server-side and retrieve cited evidence for its configured concept |
| `GET /concepts/{conceptId}` | Resolve supported concept metadata/aliases |
| `POST /sessions` | Optional ephemeral learning session |
| `POST /sessions/{sessionId}/responses` | Submit answer against authored question/rubric |
| `DELETE /sessions/{sessionId}` | Delete local session record |
| `GET /health` | Liveness and enabled capability flags; no secrets or file paths |

No WebSocket is justified for the MVP: camera work stays in browser, upload and retrieval are request/response, and generation can return a complete response. Add streaming only if measured local generation latency and UI need justify it.

## Shipped curated diagram contract

`GET /api/v1/diagrams` returns `diagramId`, title, description, the web asset URL, aspect ratio, and regions. Each region has a stable `regionId`, configured `conceptId`, display name, aliases, and normalized `{x, y, width, height}` geometry. Coordinates are fractions of the diagram image dimensions, bounded to `[0, 1]`; rectangles must be non-empty, fit within the image, and not overlap.

`POST /api/v1/diagrams/{diagramId}/regions/{regionId}/evidence` accepts only `{ "limit": 5 }` (limit 1–10). Unknown fields such as `query`, `conceptId`, or `documentIds` are rejected. The server resolves the region from its curated catalog and calls the existing retrieval service with the configured concept query and the user's indexed library. A response returns `status` (`evidence_found` or `insufficient_evidence`), the resolved concept, retrieval mode/fallback reason, and the retrieval service's citation records. Citations retain the stored chunk ID as `citationId`, along with document ID, display filename, page number, chunk index, source offsets, and exact stored text. No evidence is synthesized when there is no match.

`POST /api/v1/tutor/answers` accepts a question, optional indexed document IDs, retrieval mode/limit, and either both a curated `diagramId`/`conceptId` or neither. Unknown concepts are rejected server-side. The existing retrieval service assembles evidence; generation receives only those passages. The optional local server must expose the OpenAI-compatible `/chat/completions` API at a loopback URL. The model returns inline evidence labels (`[E1]`) and a matching label list. The API rejects malformed output or any label absent from the current evidence pack, then maps labels to stored citation records itself. Empty evidence abstains without calling the model. If the local model is disabled or unreachable, the endpoint returns a safe `TUTOR_MODEL_UNAVAILABLE` error. Existing `/answers` output is unchanged.

`POST /api/v1/quizzes` accepts a topic, question count, and optional document/concept scope. It uses the same retrieval service and optional loopback model adapter as the tutor. Empty retrieval returns `insufficient_evidence` and never calls the model. Generated questions must match the strict multiple-choice/short-answer schema, cite only retrieved evidence labels, and pass a conservative lexical support check for the question, expected answer, and explanation. The API persists questions and foreign-key citations to actual chunks. Correct answers are withheld from quiz responses until submission.

`POST /api/v1/quizzes/{quizId}/questions/{questionId}/attempts` accepts an answer and records it locally. Multiple-choice answers use exact option IDs. Short answers are compared with the expected answer and accepted alternatives after Unicode normalization, case-folding, and tokenization; token-set F1 >= 0.80 is correct, <= 0.20 is incorrect, and intermediate scores are ambiguous. This is a conservative lexical heuristic, not semantic grading. The response returns the explanation and citations resolved from current stored chunks. `GET /api/v1/progress` summarizes each question's latest outcome by topic and returns up to 100 recent attempts. Deleting a source document cascades its cited quiz questions and related attempt history so deleted source passages are not retained as stale citations.

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
