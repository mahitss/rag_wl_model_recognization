# API contracts (proposal)

This document remains the broader proposed product contract. M1 implements the local PDF and evidence subset listed in `docs/development/setup.md`; future concepts, tutoring sessions, semantic retrieval, generated answers, and the representative schemas below remain proposals rather than shipped behavior. M1's `/answers` response is explicitly extractive and abstains when retrieval finds no evidence.

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
| `GET /concepts/{conceptId}` | Resolve supported concept metadata/aliases |
| `POST /sessions` | Optional ephemeral learning session |
| `POST /sessions/{sessionId}/responses` | Submit answer against authored question/rubric |
| `DELETE /sessions/{sessionId}` | Delete local session record |
| `GET /health` | Liveness and enabled capability flags; no secrets or file paths |

No WebSocket is justified for the MVP: camera work stays in browser, upload and retrieval are request/response, and generation can return a complete response. Add streaming only if measured local generation latency and UI need justify it.

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
