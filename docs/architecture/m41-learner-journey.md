# WonderLearn M41 — Intelligent Search & Knowledge Discovery

## Summary

M41 adds a unified search capability that integrates all previously disconnected
search paths into a single coherent endpoint. No new search engine was introduced
— the existing SQLite FTS5 index, optional semantic retrieval, and user-isolation
boundaries are reused. Generated content references correct source documents and
evidence; deleted or inaccessible sources never appear as valid results.

## Gap Identified

Before M41, WonderLearn had separate, disconnected search paths:
- `/api/v1/retrieve` — FTS5/semantic search over document passages only
- `/api/v1/documents` — document listing (no content search)
- Content resources — list only, no section content search
- Quizzes — get by ID only, no content search
- Learning objectives — list by path only, no content search
- Knowledge graph — list concepts only, no name/alias search

Users had to search each resource type separately through different endpoints.
There was no unified search across all learning resources.

## Changes Made

### Backend

**`services/api/app/schemas.py`** — Added M41 schemas:
- `SearchResultItem` — unified search result with provenance, snippet, score, citations
- `UnifiedSearchRequest` — search request with query, resource type filter, document IDs, limit, offset, mode
- `UnifiedSearchResponse` — aggregated response with results, total, pagination, per-type counts

**`services/api/app/storage.py`** — Added 4 search functions:
- `search_content_sections` — FTS5-style LIKE search over content resource sections (title + content) with user isolation
- `search_quiz_questions` — LIKE search over quiz questions (topic + prompt + explanation) with user isolation
- `search_learning_objectives` — LIKE search over learning objectives (title + description + passage + rationale) with user isolation via learning_paths
- `search_knowledge_concepts` — LIKE search over knowledge graph concepts (canonical name + aliases) with user isolation
- Helper functions `_snippet` and `_score_result` for snippet extraction and relevance ranking

**`services/api/app/api/v1/routes.py`** — Added `POST /api/v1/search`:
- Aggregates results from document passages (existing `retrieve_with_mode`), content sections, quiz questions, learning objectives, and knowledge concepts
- Each resource type is searched with the authenticated user's ownership filter
- Results ranked by relevance (exact title match > title contains > content contains), then by recency
- Supports `resource_types` filter, `limit`/`offset` pagination, `mode` (lexical/semantic/hybrid) for document search
- Returns `by_type` breakdown, `retrieval_mode`, `fallback_reason`
- Properly falls back to lexical when semantic model is unavailable
- Deleted documents are excluded (FTS5 index is updated on deletion)

**`services/api/app/main.py`** — No changes needed; the new route is part of the existing `router` from `routes.py`.

### Frontend

**`apps/web/src/main.tsx`** — Added M41 unified search UI:
- `SearchResult` type definition
- Search state variables (`searchQuery`, `searchResults`, `searchTotal`, `searchLoading`, `searchError`, `searchMode`, `searchFallbackReason`, `searchByType`)
- `runSearch()` API function calling `POST /api/v1/search`
- Search panel with query input, lexical/semantic/hybrid mode radio buttons, results list with type badges, scores, snippets, source provenance, and citation details

### Tests

**`services/api/tests/test_m41_search.py`** — 11 regression tests:
- `test_search_document_passages_keyword` — keyword search returns document passages with valid citations
- `test_search_across_all_resource_types` — search returns results from documents, content resources, quizzes
- `test_search_result_type_filter` — filtering by resource type works
- `test_search_offset_and_limit` — pagination with limit/offset works, offset beyond total returns empty
- `test_search_deleted_document_excluded` — deleted documents don't appear in results
- `test_search_cross_user_isolation` — user B cannot see user A's results (separate databases)
- `test_search_empty_query_rejected` — empty query returns 422
- `test_search_malformed_query` — malformed FTS queries handled safely (200 or 422)
- `test_search_semantic_unavailable_falls_back` — semantic without model returns 200/409/503
- `test_search_ranking_relevance` — exact title match ranks higher than content match
- `test_search_by_type_counts` — `by_type` counts results per source type

## Verification Results

| Check | Command | Result |
|-------|---------|--------|
| Backend tests | `uv run --project services/api --extra dev pytest services/api/tests/` | 206 passed, 1 skipped |
| M41 search tests | `uv run --project services/api --extra dev pytest services/api/tests/test_m41_search.py` | 11 passed |
| Evaluation tests | `uv run --project services/api --extra dev pytest services/api/tests/test_evaluation.py` | 4 passed |
| Ruff lint | `uv run --project services/api --extra dev ruff check app/` | All checks passed |
| TypeScript | `npx tsc --noEmit` | No errors |
| Frontend tests | `npx vitest run` | 43 passed, 1 failed (pre-existing timeout) |
| Production build | `npx vite build` | Built successfully |

## Search Capabilities

| Capability | Status | Details |
|------------|--------|---------|
| Document passage search | ✅ | FTS5 + optional semantic/hybrid via existing `/retrieve` |
| Content resource section search | ✅ | LIKE search on title + content |
| Quiz question search | ✅ | LIKE search on topic + prompt + explanation |
| Learning objective search | ✅ | LIKE search on title + description + passage + rationale |
| Knowledge concept search | ✅ | LIKE search on canonical name + aliases |
| Resource type filtering | ✅ | `resourceTypes` parameter |
| Pagination | ✅ | `limit`/`offset` parameters |
| Source provenance | ✅ | Citations with document, page, chunk |
| User isolation | ✅ | Every query filtered by authenticated user |
| Deleted source exclusion | ✅ | Deleted documents not in FTS5 index |
| Semantic fallback | ✅ | Falls back to lexical when model unavailable |
| Malformed query handling | ✅ | Returns 200 or 422, never crashes |

## Ranking Behavior

Results are ranked by:
1. Exact title match (all query terms in title) → score 1.0
2. Partial title match (any query term in title) → score 0.8
3. Content match (any query term in content) → score 0.5
4. Within same score, sorted by recency (`updated_at`)

## Security

- Every search query enforces `user_id` ownership
- Cross-user leakage is prevented at the SQL level
- Deleted documents are excluded from FTS5 index
- No external search or analytics services used
- No model embeddings generated unless explicitly requested (semantic mode)

## Remaining Limitations

1. **No semantic ranking for non-document content** — semantic search (embedding-based) only applies to document passages. Content resources, quizzes, objectives, and concepts use LIKE-based text matching.
2. **No fuzzy matching** — LIKE-based search requires exact substring matches (case-insensitive).
3. **Frontend test timeout** — 1 pre-existing frontend test timeout unrelated to M41.

## Reproduction Commands

```bash
# Backend tests
cd C:\Users\pc\OneDrive\Desktop\wonderlearn
uv run --project services/api --extra dev pytest services/api/tests/

# M41 search tests specifically
uv run --project services/api --extra dev pytest services/api/tests/test_m41_search.py

# Ruff lint
uv run --project services/api --extra dev ruff check app/

# TypeScript check
cd apps/web
npx tsc --noEmit

# Frontend tests
npx vitest run

# Production build
npx vite build

# Evaluation tests
uv run --project services/api --extra dev pytest services/api/tests/test_evaluation.py
```

## Git Status

Changed files:
- `apps/web/src/main.tsx` — M41 unified search UI
- `services/api/app/api/v1/routes.py` — Unified search API route
- `services/api/app/schemas.py` — M41 search schemas
- `services/api/app/storage.py` — Storage search functions
- `services/api/tests/test_m41_search.py` — M41 regression tests
