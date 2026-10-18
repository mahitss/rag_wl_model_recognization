import { StrictMode, useEffect, useRef, useState, type CSSProperties } from "react";
import { createRoot } from "react-dom/client";
import { CameraPanel } from "./CameraPanel";
import { normalizedRegionStyle } from "./geometry";
import "./style.css";

type DiagramRegion = {
  regionId: string;
  conceptId: string;
  displayName: string;
  aliases: string[];
  geometry: { x: number; y: number; width: number; height: number };
};

type Diagram = {
  diagramId: string;
  title: string;
  description: string;
  imageUrl: string;
  imageAspectRatio: number;
  regions: DiagramRegion[];
};

type Citation = {
  citationId: string;
  documentId: string;
  displayFilename: string;
  pageNumber: number;
  chunkIndex: number;
  text: string;
  retrieval: { method: string; rank: number; similarity?: number; rrfScore?: number };
};

type Evidence = {
  status: "evidence_found" | "insufficient_evidence";
  concept: { conceptId: string; displayName: string; aliases: string[] };
  results: Citation[];
  retrievalMode: string;
  fallbackReason: string | null;
};

async function readJson<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T | { error?: { message?: string } };
  if (!response.ok) {
    const error = body as { error?: { message?: string } };
    throw new Error(error.error?.message ?? `Request failed (${response.status}).`);
  }
  return body as T;
}

export function App() {
  const [diagram, setDiagram] = useState<Diagram | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [selectedRegion, setSelectedRegion] = useState<DiagramRegion | null>(null);
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [loading, setLoading] = useState(false);
  const requestNumber = useRef(0);

  useEffect(() => {
    let active = true;
    fetch("/api/v1/diagrams")
      .then((response) => readJson<{ diagrams: Diagram[] }>(response))
      .then((result) => {
        if (active) {
          setDiagram(result.diagrams[0] ?? null);
          if (result.diagrams.length === 0) setPageError("No curated diagrams are available.");
        }
      })
      .catch((error: unknown) => {
        if (active) {
          setPageError(error instanceof Error ? error.message : "Could not load the diagram.");
        }
      });
    return () => {
      active = false;
    };
  }, []);

  async function selectRegion(region: DiagramRegion) {
    if (!diagram) return;
    const currentRequest = ++requestNumber.current;
    setSelectedRegion(region);
    setEvidence(null);
    setPageError(null);
    setLoading(true);
    try {
      const response = await fetch(
        `/api/v1/diagrams/${encodeURIComponent(diagram.diagramId)}/regions/${encodeURIComponent(region.regionId)}/evidence`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ limit: 5 }),
        },
      );
      const result = await readJson<Evidence>(response);
      if (currentRequest === requestNumber.current) setEvidence(result);
    } catch (error) {
      if (currentRequest === requestNumber.current) {
        setPageError(error instanceof Error ? error.message : "Could not retrieve evidence.");
      }
    } finally {
      if (currentRequest === requestNumber.current) setLoading(false);
    }
  }

  return (
    <main className="page">
      <header className="page-header">
        <p className="eyebrow">WonderLearn · curated diagram</p>
        <h1>Explore a concept</h1>
        <p className="description">
          Choose a labeled region to find evidence in your indexed study materials. Select a region
          with a pointer or use Tab and Enter.
        </p>
      </header>

      {pageError && (
        <p className="notice notice--error" role="alert">
          {pageError} Check that the local API is running.
        </p>
      )}

      {diagram && (
        <section className="learning-layout" aria-label="Diagram concept selection">
          <div className="diagram-card">
            <div className="diagram-heading">
              <div>
                <p className="eyebrow">Supported diagram</p>
                <h2>{diagram.title}</h2>
              </div>
              <span className="diagram-id">{diagram.diagramId}</span>
            </div>
            <p className="diagram-description">{diagram.description}</p>
            <div
              className="diagram-stage"
              style={{ "--diagram-ratio": diagram.imageAspectRatio } as CSSProperties}
            >
              <img
                className="diagram-image"
                src={diagram.imageUrl}
                alt="Photosynthesis diagram showing light, plant inputs, and glucose and oxygen outputs"
              />
              {diagram.regions.map((region) => {
                return (
                  <button
                    className={`region-button${selectedRegion?.regionId === region.regionId ? " region-button--selected" : ""}`}
                    key={region.regionId}
                    type="button"
                    style={normalizedRegionStyle(region.geometry)}
                    aria-label={`Explore ${region.displayName}`}
                    aria-pressed={selectedRegion?.regionId === region.regionId}
                    onClick={() => void selectRegion(region)}
                  >
                    <span className="region-button__label">{region.displayName}</span>
                  </button>
                );
              })}
            </div>
            <nav className="region-list" aria-label="Diagram regions">
              {diagram.regions.map((region) => (
                <button
                  className="region-choice"
                  key={region.regionId}
                  type="button"
                  aria-pressed={selectedRegion?.regionId === region.regionId}
                  onClick={() => void selectRegion(region)}
                >
                  {region.displayName}
                </button>
              ))}
            </nav>
            <CameraPanel
              diagram={diagram}
              onSelectRegion={(regionId) => {
                const region = diagram.regions.find((item) => item.regionId === regionId);
                if (region) void selectRegion(region);
              }}
            />
          </div>

          <section className="evidence-card" aria-labelledby="evidence-title" aria-live="polite">
            <p className="eyebrow">Your study materials</p>
            <h2 id="evidence-title">{selectedRegion?.displayName ?? "Select a region"}</h2>
            {!selectedRegion && (
              <p className="muted">Evidence is searched only after you choose a diagram region.</p>
            )}
            {loading && <p className="muted">Searching indexed study materials…</p>}
            {evidence?.status === "insufficient_evidence" && !loading && (
              <>
                <p className="notice" role="status">
                  No supporting passage was found for this concept in your indexed materials. No
                  answer or citation has been inferred.
                </p>
                {evidence.fallbackReason && (
                  <p className="muted">
                    {evidence.retrievalMode.replaceAll("_", " ")} ·{" "}
                    {evidence.fallbackReason.replaceAll("_", " ")}
                  </p>
                )}
              </>
            )}
            {evidence?.status === "evidence_found" && (
              <>
                <p className="retrieval-note">
                  {evidence.results.length} passage{evidence.results.length === 1 ? "" : "s"} ·{" "}
                  {evidence.retrievalMode.replaceAll("_", " ")} retrieval
                </p>
                {evidence.fallbackReason && (
                  <p className="muted">Retrieval note: {evidence.fallbackReason.replaceAll("_", " ")}</p>
                )}
                <ol className="evidence-list">
                  {evidence.results.map((citation) => (
                    <li className="evidence-item" key={citation.citationId}>
                      <blockquote>{citation.text}</blockquote>
                      <p className="citation-meta">
                        <strong>{citation.displayFilename}</strong> · page {citation.pageNumber}
                      </p>
                      <p className="citation-id">
                        Document ID: <code>{citation.documentId}</code> · chunk index {citation.chunkIndex}
                      </p>
                      <p className="citation-id">
                        Citation / chunk ID: <code>{citation.citationId}</code>
                      </p>
                    </li>
                  ))}
                </ol>
              </>
            )}
          </section>
        </section>
      )}
    </main>
  );
}

const rootElement = document.getElementById("root");
if (rootElement) {
  createRoot(rootElement).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
