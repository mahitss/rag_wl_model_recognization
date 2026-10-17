import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

function App() {
  return (
    <main className="page">
      <p className="eyebrow">M0 · Development foundation</p>
      <h1>WonderLearn</h1>
      <p className="description">
        The local development shell is ready. Document ingestion, retrieval, and AI inference have
        not been implemented yet.
      </p>
      <section className="status" aria-labelledby="status-title">
        <h2 id="status-title">API status</h2>
        <p>
          Check <code>/api/v1/health</code> on the local API server for service status.
        </p>
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
