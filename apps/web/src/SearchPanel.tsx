/**
 * M78: Universal Search & Learning Progress Portal search interface.
 *
 * Provides:
 *   * Search input with query submission
 *   * Results filtered by resource type
 *   * Result display with titles, snippets, scores, and source attribution
 *   * Accessible keyboard navigation and screen-reader labels
 *   * Empty and error states
 *
 * All data comes from the persisted search API (already user-scoped and
 * authorized). No private learner data is logged or exposed beyond what the
 * backend already returns.
 */

import { useCallback, useRef, useState } from "react";

type SearchResult = {
  resultType: string;
  id: string;
  title: string;
  snippet: string;
  score: number;
  sourceType: string;
  sourceId: string;
  sourceTitle: string | null;
  pageNumber: number | null;
  citations: Array<{
    citationId: string;
    documentId: string;
    displayFilename: string;
    pageNumber: number;
    chunkIndex: number;
    charStart: number;
    charEnd: number;
    text: string;
  }>;
  createdAt: string | null;
  updatedAt: string | null;
};

export default function SearchPanel() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resourceTypeFilter, setResourceTypeFilter] = useState<string | null>(
    null
  );

  const searchRef = useRef<HTMLInputElement>(null);

  const search = useCallback(
    async (q: string) => {
      if (!q.trim()) {
        setResults([]);
        setError(null);
        return;
      }

      setIsLoading(true);
      setError(null);

      try {
        const response = await fetch("/api/v1/search", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            query: q,
            resourceTypes: resourceTypeFilter
              ? [resourceTypeFilter]
              : undefined,
            limit: 20,
            offset: 0,
            mode: "lexical",
          }),
        });

        if (!response.ok) {
          throw new Error(`Search failed: ${response.status}`);
        }

        const data = await response.json();
        setResults(data.results || []);
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "An unexpected error occurred while searching."
        );
        setResults([]);
      } finally {
        setIsLoading(false);
      }
    },
    [resourceTypeFilter],
  );

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    search(query.trim());
  };

  const handleResourceTypeFilter = (
    event: React.ChangeEvent<HTMLSelectElement>
  ) => {
    setResourceTypeFilter(event.currentTarget.value || null);
  };

  const filteredResults = results.filter(
    (r) =>
      !resourceTypeFilter || r.sourceType === resourceTypeFilter
  );

  const resourceTypes = [
    { value: "", label: "All types" },
    { value: "document", label: "Documents" },
    { value: "content_resource", label: "Content resources" },
    { value: "quiz", label: "Quizzes" },
    { value: "learning_objective", label: "Learning objectives" },
    { value: "knowledge_concept", label: "Knowledge concepts" },
  ];

  return (
    <section className="search-panel" aria-label="Search">
      <h2 className="sr-only">Search</h2>

      <form onSubmit={handleSubmit} className="search-form">
        <label htmlFor="search-input" className="sr-only">
          Search query
        </label>
        <input
          id="search-input"
          ref={searchRef}
          type="search"
          placeholder="Search your learning materials…"
          value={query}
          onChange={(event) => setQuery(event.currentTarget.value)}
          autoComplete="off"
        />
        <button type="submit" className="search-button" disabled={isLoading}>
          {isLoading ? "Searching…" : "Search"}
        </button>
      </form>

      <div className="search-filters">
        <label htmlFor="resource-type-filter" className="sr-only">
          Filter by resource type
        </label>
        <select
          id="resource-type-filter"
          value={resourceTypeFilter ?? ""}
          onChange={handleResourceTypeFilter}
        >
          {resourceTypes.map((rt) => (
            <option key={rt.value} value={rt.value}>
              {rt.label}
            </option>
          ))}
        </select>
      </div>

      <div className="search-results">
        <h3>
          Search results
          {results.length > 0 ? ` (${results.length} results)` : ""}
        </h3>

        {error && (
          <div className="search-error" role="alert">
            {error}
          </div>
        )}

        {!error && results.length === 0 && query.trim() && (
          <p className="search-empty">No results found for "{query}".</p>
        )}

        {!error && results.length > 0 && (
          <ul className="search-results-list" role="list" aria-label="Search results">
            {filteredResults.map((result) => (
              <li key={result.id} className="search-result-item">
                <div className="search-result-header">
                  <span className="search-result-title">{result.title}</span>
                  <span className="search-result-type">
                    {result.sourceType}
                  </span>
                  {result.score !== undefined && (
                    <span className="search-result-score">
                      {result.score.toFixed(2)}
                    </span>
                  )}
                </div>
                <p className="search-result-snippet">{result.snippet}</p>
                <div className="search-result-meta">
                  {result.sourceTitle && (
                    <span className="search-result-source">
                      {result.sourceTitle}
                    </span>
                  )}
                  {result.pageNumber !== null && result.pageNumber !== undefined && (
                    <span className="search-result-page">
                      p. {result.pageNumber}
                    </span>
                  )}
                </div>
                {result.citations && result.citations.length > 0 && (
                  <details className="search-result-citations">
                    <summary>Sources</summary>
                    <ul>
                      {result.citations.map((citation) => (
                        <li key={citation.citationId}>
                          {citation.displayFilename} (
                          p. {citation.pageNumber})
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
