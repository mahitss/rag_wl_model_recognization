# API contracts (proposal)

This document includes the broader proposed product contract. M1 implements local PDF and evidence retrieval; M2 adds optional semantic/hybrid retrieval and the curated diagram endpoints below. Future tutoring sessions and generated answers remain proposals rather than shipped behavior. `/answers` remains explicitly extractive and abstains when evidence is absent or below the semantic relevance threshold.

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
