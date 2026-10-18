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

type IndexedDocument = { documentId: string; displayFilename: string };

type TutorResult = {
  status: "answered" | "insufficient_evidence";
  answer: string | null;
  citations: Citation[];
  retrievalMode: string;
  fallbackReason: string | null;
};

type QuizCitation = Citation;
type QuizQuestion = {
  questionId: string;
  questionType: "multiple_choice" | "short_answer";
  prompt: string;
  options: { id: string; text: string }[];
  explanation: string | null;
  citations: QuizCitation[];
  latestOutcome: "correct" | "incorrect" | "ambiguous" | null;
  attemptCount: number;
};
type QuizSet = {
  status: "ready" | "insufficient_evidence";
  quizId: string | null;
  topicId: string | null;
  topic: string;
  questions: QuizQuestion[];
};
type QuizFeedback = {
  outcome: "correct" | "incorrect" | "ambiguous";
  expectedAnswer: string;
  explanation: string;
  citations: Citation[];
  attemptCount: number;
};
type LearningProgress = {
  topics: {
    topicId: string;
    topic: string;
    attemptedQuestions: number;
    correctQuestions: number;
    incorrectQuestions: number;
    ambiguousQuestions: number;
    accuracy: number;
    needsReview: boolean;
  }[];
  totalAttempts: number;
  recentAttempts: {
    attemptId: string;
    outcome: "correct" | "incorrect" | "ambiguous";
    createdAt: string;
    prompt: string;
    topicId: string;
    topic: string;
  }[];
};

type Evidence = {
  status: "evidence_found" | "insufficient_evidence";
  concept: { conceptId: string; displayName: string; aliases: string[] };
  results: Citation[];
  retrievalMode: string;
  fallbackReason: string | null;
};

type GraphConcept = { conceptId: string; name: string; aliases: string[]; evidenceCount: number; relationshipCount: number };
type GraphDetail = GraphConcept & {
  evidence: { chunkId: string; documentId: string; displayFilename: string; pageNumber: number; supportingText: string }[];
  relationships: {
    relationshipId: string; sourceConceptId: string; sourceName: string; targetConceptId: string;
    targetName: string; relation: string; chunkId: string; documentId: string;
    displayFilename: string; pageNumber: number; supportingText: string; status: "candidate" | "human_verified";
  }[];
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
  const [documents, setDocuments] = useState<IndexedDocument[]>([]);
  const [selectedDocumentId, setSelectedDocumentId] = useState("");
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [tutorConfigured, setTutorConfigured] = useState<boolean | null>(null);
  const [tutorModelId, setTutorModelId] = useState<string | null>(null);
  const [tutorQuestion, setTutorQuestion] = useState("");
  const [tutorResult, setTutorResult] = useState<TutorResult | null>(null);
  const [tutorError, setTutorError] = useState<string | null>(null);
  const [tutorLoading, setTutorLoading] = useState(false);
  const [quizTopic, setQuizTopic] = useState("");
  const [quiz, setQuiz] = useState<QuizSet | null>(null);
  const [quizAnswers, setQuizAnswers] = useState<Record<string, string>>({});
  const [quizFeedback, setQuizFeedback] = useState<Record<string, QuizFeedback>>({});
  const [quizLoading, setQuizLoading] = useState(false);
  const [quizError, setQuizError] = useState<string | null>(null);
  const [progress, setProgress] = useState<LearningProgress | null>(null);
  const [graphConcepts, setGraphConcepts] = useState<GraphConcept[]>([]);
  const [selectedGraphConcept, setSelectedGraphConcept] = useState<GraphDetail | null>(null);
  const [graphLoading, setGraphLoading] = useState(false);
  const [graphError, setGraphError] = useState<string | null>(null);
  const [graphCursor, setGraphCursor] = useState<string | null>(null);
  const [graphCursorScope, setGraphCursorScope] = useState<string | null>(null);
  const [graphDoneScope, setGraphDoneScope] = useState<string | null>(null);
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

  useEffect(() => {
    let active = true;
    fetch("/api/v1/documents")
      .then((response) => readJson<{ documents: IndexedDocument[] }>(response))
      .then((result) => {
        if (active) setDocuments(result.documents);
      })
      .catch(() => {
        if (active) setUploadError("Could not load the indexed document list.");
      });
    fetch("/api/v1/tutor/status")
      .then((response) => readJson<{ configured: boolean; modelId: string | null }>(response))
      .then((result) => {
        if (active) {
          setTutorConfigured(result.configured);
          setTutorModelId(result.modelId);
        }
      })
      .catch(() => {
        if (active) setTutorConfigured(false);
      });
    fetch("/api/v1/progress")
      .then((response) => readJson<LearningProgress>(response))
      .then((result) => {
        if (active) setProgress(result);
      })
      .catch(() => {
        if (active) setQuizError("Could not load local learning progress.");
      });
    fetch("/api/v1/knowledge-graph")
      .then((response) => readJson<{ concepts: GraphConcept[] }>(response))
      .then((result) => { if (active) setGraphConcepts(result.concepts); })
      .catch(() => { if (active) setGraphError("Could not load the local knowledge graph."); });
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

  async function uploadDocument(file: File | undefined) {
    if (!file) return;
    setUploading(true);
    setUploadError(null);
    try {
      const body = new FormData();
      body.append("file", file);
      const response = await fetch("/api/v1/documents", { method: "POST", body });
      const document = await readJson<IndexedDocument>(response);
      setDocuments((current) => [document, ...current.filter((item) => item.documentId !== document.documentId)]);
      setSelectedDocumentId(document.documentId);
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : "Could not upload the PDF.");
    } finally {
      setUploading(false);
    }
  }

  async function askTutor(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setTutorLoading(true);
    setTutorError(null);
    setTutorResult(null);
    try {
      const payload = {
        question: tutorQuestion,
        ...(selectedGraphConcept
          ? { graphConceptId: selectedGraphConcept.conceptId }
          : selectedRegion && diagram
          ? { diagramId: diagram.diagramId, conceptId: selectedRegion.conceptId }
          : {}),
        ...(selectedDocumentId ? { documentIds: [selectedDocumentId] } : {}),
      };
      const response = await fetch("/api/v1/tutor/answers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      setTutorResult(await readJson<TutorResult>(response));
    } catch (error) {
      setTutorError(error instanceof Error ? error.message : "The tutor could not answer right now.");
    } finally {
      setTutorLoading(false);
    }
  }

  async function loadGraphConcept(conceptId: string) {
    setGraphLoading(true);
    setGraphError(null);
    try {
      const response = await fetch(`/api/v1/knowledge-graph/concepts/${encodeURIComponent(conceptId)}`);
      setSelectedGraphConcept(await readJson<GraphDetail>(response));
    } catch (error) {
      setGraphError(error instanceof Error ? error.message : "Could not load this graph concept.");
    } finally {
      setGraphLoading(false);
    }
  }

  async function extractGraph() {
    setGraphLoading(true);
    setGraphError(null);
    try {
      const response = await fetch("/api/v1/knowledge-graph/extract", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...(selectedDocumentId ? { documentIds: [selectedDocumentId] } : {}),
          ...(graphCursor && graphCursorScope === selectedDocumentId ? { afterChunkId: graphCursor } : {}),
          chunkLimit: 10,
        }),
      });
      const result = await readJson<{ concepts: GraphConcept[]; nextAfterChunkId: string | null; chunksProcessed: number }>(response);
      setGraphConcepts(result.concepts);
      setGraphCursorScope(selectedDocumentId);
      setGraphCursor(result.nextAfterChunkId);
      if (result.nextAfterChunkId === null) setGraphDoneScope(selectedDocumentId);
      if (result.concepts.length === 0 && result.nextAfterChunkId === null) setGraphError("No evidence-backed concepts were extracted from the selected material.");
    } catch (error) {
      setGraphError(error instanceof Error ? error.message : "Could not extract graph candidates.");
    } finally {
      setGraphLoading(false);
    }
  }

  async function verifyGraphRelationship(relationshipId: string) {
    if (!selectedGraphConcept) return;
    setGraphLoading(true);
    setGraphError(null);
    try {
      const response = await fetch(`/api/v1/knowledge-graph/relationships/${encodeURIComponent(relationshipId)}/verify`, { method: "POST" });
      await readJson<{ relationshipId: string; status: string }>(response);
      await loadGraphConcept(selectedGraphConcept.conceptId);
    } catch (error) {
      setGraphError(error instanceof Error ? error.message : "Could not verify this relationship.");
    } finally {
      setGraphLoading(false);
    }
  }

  async function refreshProgress() {
    const response = await fetch("/api/v1/progress");
    setProgress(await readJson<LearningProgress>(response));
  }

  async function generateQuiz(topic = quizTopic) {
    const normalizedTopic = topic.trim();
    if (!normalizedTopic) return;
    setQuizLoading(true);
    setQuizError(null);
    setQuiz(null);
    setQuizFeedback({});
    setQuizAnswers({});
    setQuizTopic(normalizedTopic);
    try {
      const payload = {
        topic: normalizedTopic,
        questionCount: 5,
        ...(selectedDocumentId ? { documentIds: [selectedDocumentId] } : {}),
        ...(selectedRegion && diagram
          ? { diagramId: diagram.diagramId, conceptId: selectedRegion.conceptId }
          : {}),
      };
      const response = await fetch("/api/v1/quizzes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      setQuiz(await readJson<QuizSet>(response));
    } catch (error) {
      setQuizError(error instanceof Error ? error.message : "Could not generate a quiz.");
    } finally {
      setQuizLoading(false);
    }
  }

  async function submitQuizAnswer(question: QuizQuestion) {
    if (!quiz?.quizId || !quizAnswers[question.questionId]) return;
    setQuizLoading(true);
    setQuizError(null);
    try {
      const response = await fetch(
        `/api/v1/quizzes/${encodeURIComponent(quiz.quizId)}/questions/${encodeURIComponent(question.questionId)}/attempts`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ answer: quizAnswers[question.questionId] }),
        },
      );
      const result = await readJson<QuizFeedback>(response);
      setQuizFeedback((current) => ({ ...current, [question.questionId]: result }));
      setQuiz((current) => current && ({
        ...current,
        questions: current.questions.map((item) => item.questionId === question.questionId
          ? { ...item, latestOutcome: result.outcome, attemptCount: result.attemptCount, explanation: result.explanation, citations: result.citations }
          : item),
      }));
      await refreshProgress();
    } catch (error) {
      setQuizError(error instanceof Error ? error.message : "Could not record that answer.");
    } finally {
      setQuizLoading(false);
    }
  }

  async function retryMissedQuestions() {
    if (!quiz?.quizId) return;
    setQuizLoading(true);
    setQuizError(null);
    try {
      const response = await fetch(
        `/api/v1/quizzes/${encodeURIComponent(quiz.quizId)}?review_incorrect=true`,
      );
      const result = await readJson<QuizSet>(response);
      setQuiz(result);
      setQuizAnswers({});
      setQuizFeedback({});
      if (result.questions.length === 0) setQuizError("There are no missed or uncertain questions to retry.");
    } catch (error) {
      setQuizError(error instanceof Error ? error.message : "Could not load review questions.");
    } finally {
      setQuizLoading(false);
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
            <div className="document-controls">
              <label className="document-control" htmlFor="study-document">
                Search material
                <select
                  id="study-document"
                  value={selectedDocumentId}
                  onChange={(event) => setSelectedDocumentId(event.currentTarget.value)}
                >
                  <option value="">All indexed documents</option>
                  {documents.map((document) => (
                    <option key={document.documentId} value={document.documentId}>
                      {document.displayFilename}
                    </option>
                  ))}
                </select>
              </label>
              <label className="document-control" htmlFor="pdf-upload">
                Add a learning PDF
                <input
                  id="pdf-upload"
                  type="file"
                  accept="application/pdf,.pdf"
                  disabled={uploading}
                  onChange={(event) => {
                    void uploadDocument(event.currentTarget.files?.[0]);
                    event.currentTarget.value = "";
                  }}
                />
              </label>
              {uploading && <p className="muted" role="status">Indexing PDF locallyâ€¦</p>}
              {uploadError && <p className="notice notice--error" role="alert">{uploadError}</p>}
            </div>
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
            <section className="tutor-section" aria-labelledby="tutor-title">
              <p className="eyebrow">Optional local model</p>
              <h2 id="tutor-title">Ask the grounded tutor</h2>
              {tutorConfigured === false && (
                <p className="notice" role="status">
                  Local tutor unavailable. Follow the optional tutor setup guide to configure a
                  loopback model server. Evidence search continues to work without it.
                </p>
              )}
              {tutorConfigured && (
                <p className="muted">Configured local model: {tutorModelId}</p>
              )}
              <form className="tutor-form" onSubmit={(event) => void askTutor(event)}>
                <label className="document-control" htmlFor="tutor-question">
                  {selectedRegion ? `Question about ${selectedRegion.displayName}` : "Question about your materials"}
                  <textarea
                    id="tutor-question"
                    value={tutorQuestion}
                    maxLength={2_000}
                    required
                    rows={3}
                    onChange={(event) => setTutorQuestion(event.currentTarget.value)}
                    placeholder="Ask a question grounded in your indexed passages"
                  />
                </label>
                <button className="camera-action camera-action--primary" type="submit" disabled={tutorLoading}>
                  {tutorLoading ? "Retrieving and askingâ€¦" : "Ask tutor"}
                </button>
              </form>
              {tutorLoading && <p className="muted" role="status">Finding evidence and generating a grounded explanationâ€¦</p>}
              {tutorError && <p className="notice notice--error" role="alert">{tutorError}</p>}
              {tutorResult?.status === "insufficient_evidence" && (
                <p className="notice" role="status">No supporting passages were found, or the tutor abstained because the evidence did not support an answer.</p>
              )}
              {tutorResult?.status === "answered" && tutorResult.answer && (
                <div className="tutor-answer" aria-live="polite">
                  <p>{tutorResult.answer}</p>
                  <p className="retrieval-note">Sources from {tutorResult.retrievalMode} retrieval</p>
                  <ol className="evidence-list">
                    {tutorResult.citations.map((citation) => (
                      <li className="evidence-item" key={citation.citationId}>
                        <blockquote>{citation.text}</blockquote>
                        <p className="citation-meta"><strong>{citation.displayFilename}</strong> Â· page {citation.pageNumber}</p>
                        <p className="citation-id">Citation / chunk ID: <code>{citation.citationId}</code></p>
                      </li>
                    ))}
                  </ol>
                </div>
              )}
            </section>
            <section className="quiz-section" aria-labelledby="quiz-title">
              <p className="eyebrow">Practice from your sources</p>
              <h2 id="quiz-title">Quiz and progress</h2>
              {tutorConfigured === false && (
                <p className="notice" role="status">
                  Quiz generation needs the optional local tutor model. Your progress remains stored locally.
                </p>
              )}
              <form
                className="quiz-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void generateQuiz();
                }}
              >
                <label className="document-control" htmlFor="quiz-topic">
                  Topic to practice
                  <input
                    id="quiz-topic"
                    value={quizTopic}
                    maxLength={160}
                    minLength={2}
                    required
                    onChange={(event) => setQuizTopic(event.currentTarget.value)}
                    placeholder="For example, photosynthesis"
                  />
                </label>
                <button
                  className="camera-action camera-action--primary"
                  type="submit"
                  disabled={quizLoading || tutorConfigured === false}
                >
                  {quizLoading ? "Building quiz…" : "Generate quiz from evidence"}
                </button>
              </form>
              {quizLoading && <p className="muted" role="status">Retrieving passages and preparing questions…</p>}
              {quizError && <p className="notice notice--error" role="alert">{quizError}</p>}
              {quiz?.status === "insufficient_evidence" && (
                <p className="notice" role="status">No supporting passages were found, so no quiz was generated.</p>
              )}
              {quiz?.status === "ready" && (
                <div className="quiz-questions" aria-label={`Quiz about ${quiz.topic}`}>
                  <h3>{quiz.topic}</h3>
                  {quiz.questions.map((question, index) => {
                    const feedback = quizFeedback[question.questionId];
                    const outcome = feedback?.outcome ?? question.latestOutcome;
                    const explanation = feedback?.explanation ?? question.explanation;
                    const citations = feedback?.citations ?? question.citations;
                    return (
                      <article className="quiz-question" key={question.questionId}>
                        <h4>{index + 1}. {question.prompt}</h4>
                        {question.questionType === "multiple_choice" ? (
                          <fieldset className="quiz-options">
                            <legend className="sr-only">Choose one answer</legend>
                            {question.options.map((option) => (
                              <label key={option.id}>
                                <input
                                  type="radio"
                                  name={`answer-${question.questionId}`}
                                  value={option.id}
                                  checked={quizAnswers[question.questionId] === option.id}
                                  onChange={() => setQuizAnswers((current) => ({ ...current, [question.questionId]: option.id }))}
                                />
                                <span><strong>{option.id}.</strong> {option.text}</span>
                              </label>
                            ))}
                          </fieldset>
                        ) : (
                          <label className="document-control" htmlFor={`answer-${question.questionId}`}>
                            Your short answer
                            <input
                              id={`answer-${question.questionId}`}
                              value={quizAnswers[question.questionId] ?? ""}
                              maxLength={2_000}
                              onChange={(event) => setQuizAnswers((current) => ({ ...current, [question.questionId]: event.currentTarget.value }))}
                            />
                          </label>
                        )}
                        <button
                          className="camera-action"
                          type="button"
                          disabled={quizLoading || !quizAnswers[question.questionId]?.trim()}
                          onClick={() => void submitQuizAnswer(question)}
                        >
                          {question.attemptCount > 0 ? "Submit retry" : "Check answer"}
                        </button>
                        {outcome && (
                          <p className={`quiz-outcome quiz-outcome--${outcome}`} role="status">
                            {outcome === "correct" ? "Correct" : outcome === "incorrect" ? "Not correct" : "Uncertain — review the evidence"}
                            {feedback && ` · Expected answer: ${feedback.expectedAnswer}`}
                          </p>
                        )}
                        {explanation && (
                          <div className="quiz-explanation">
                            <p>{explanation}</p>
                            <ol className="evidence-list">
                              {citations.map((citation) => (
                                <li className="evidence-item" key={citation.citationId}>
                                  <blockquote>{citation.text}</blockquote>
                                  <p className="citation-meta"><strong>{citation.displayFilename}</strong> · page {citation.pageNumber}</p>
                                  <p className="citation-id">Citation / chunk ID: <code>{citation.citationId}</code></p>
                                </li>
                              ))}
                            </ol>
                          </div>
                        )}
                      </article>
                    );
                  })}
                  {quiz.questions.some((question) => question.latestOutcome === "incorrect" || question.latestOutcome === "ambiguous") && (
                    <button className="camera-action" type="button" disabled={quizLoading} onClick={() => void retryMissedQuestions()}>
                      Retry missed or uncertain questions
                    </button>
                  )}
                </div>
              )}
              {progress && (
                <div className="progress-panel" aria-label="Learning progress">
                  <h3>Local progress · {progress.totalAttempts} attempts</h3>
                  {progress.topics.length === 0 ? (
                    <p className="muted">Your topic progress will appear after your first answer.</p>
                  ) : (
                    <ul className="progress-topics">
                      {progress.topics.map((topic) => (
                        <li key={topic.topicId}>
                          <span><strong>{topic.topic}</strong> · {topic.correctQuestions}/{topic.attemptedQuestions} latest answers correct</span>
                          <span>{Math.round(topic.accuracy * 100)}%{topic.needsReview ? " · review suggested" : ""}</span>
                          {topic.needsReview && (
                            <button type="button" className="text-action" disabled={quizLoading} onClick={() => void generateQuiz(topic.topic)}>
                              Practice this topic
                            </button>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                  {progress.recentAttempts.length > 0 && (
                    <details>
                      <summary>Review history</summary>
                      <ol className="attempt-history">
                        {progress.recentAttempts.map((attempt) => (
                          <li key={attempt.attemptId}>
                            <span>{attempt.topic}: {attempt.prompt}</span>
                            <span>{attempt.outcome} · {new Date(attempt.createdAt).toLocaleString()}</span>
                          </li>
                        ))}
                      </ol>
                    </details>
                  )}
                </div>
              )}
            </section>
          </section>
        </section>
      )}
      <section className="knowledge-graph" aria-labelledby="knowledge-graph-title">
        <div className="graph-heading">
          <div>
            <p className="eyebrow">Evidence-linked candidates</p>
            <h2 id="knowledge-graph-title">Knowledge graph</h2>
            <p className="muted">Relationships remain model-extracted candidates until a person verifies them. Extraction runs locally through the optional tutor model.</p>
          </div>
          <button className="camera-action camera-action--primary" type="button" disabled={graphLoading || tutorConfigured !== true || documents.length === 0 || graphDoneScope === selectedDocumentId} onClick={() => void extractGraph()}>
            {graphLoading ? "Working…" : graphCursor && graphCursorScope === selectedDocumentId ? "Extract next batch" : "Extract from study materials"}
          </button>
        </div>
        {tutorConfigured === false && <p className="notice" role="status">Graph extraction needs the optional local tutor. Existing graph data and citations remain available.</p>}
        {graphError && <p className="notice notice--error" role="alert">{graphError}</p>}
        {graphConcepts.length === 0 ? <p className="muted">No graph concepts yet. Index a PDF, configure the local tutor, then extract candidates.</p> : (
          <div className="graph-layout">
            <nav className="graph-nodes" aria-label="Knowledge graph concepts">
              {graphConcepts.map((concept) => <button type="button" key={concept.conceptId} aria-pressed={selectedGraphConcept?.conceptId === concept.conceptId} onClick={() => void loadGraphConcept(concept.conceptId)}>{concept.name}<small>{concept.relationshipCount} connected relations</small></button>)}
            </nav>
            {selectedGraphConcept && <article className="graph-detail" aria-live="polite">
              <h3>{selectedGraphConcept.name}</h3>
              {!!selectedGraphConcept.aliases.length && <p className="muted">Aliases: {selectedGraphConcept.aliases.join(", ")}</p>}
              <button type="button" className="text-action" onClick={() => { setTutorQuestion(`Explain ${selectedGraphConcept.name} using my study materials.`); document.getElementById("tutor-question")?.focus(); }}>Ask tutor about this concept</button>
              <h4>Connected concepts and evidence</h4>
              {selectedGraphConcept.relationships.length === 0 && <p className="muted">No relationships have been extracted for this concept.</p>}
              <ul className="graph-relations">{selectedGraphConcept.relationships.map((relation) => {
                const otherId = relation.sourceConceptId === selectedGraphConcept.conceptId ? relation.targetConceptId : relation.sourceConceptId;
                const otherName = relation.sourceConceptId === selectedGraphConcept.conceptId ? relation.targetName : relation.sourceName;
                return <li key={relation.relationshipId}>
                  <button type="button" className="text-action" onClick={() => void loadGraphConcept(otherId)}>{otherName}</button>
                  <span> · {relation.relation} ({relation.sourceConceptId === selectedGraphConcept.conceptId ? "outgoing" : "incoming"})</span>
                  <p><strong>{relation.status === "human_verified" ? "Human verified" : "Candidate — needs review"}</strong></p>
                  {relation.status === "candidate" && <button type="button" className="text-action" disabled={graphLoading} onClick={() => void verifyGraphRelationship(relation.relationshipId)}>Mark as human-verified</button>}
                  <blockquote>{relation.supportingText}</blockquote>
                  <p className="citation-meta"><strong>{relation.displayFilename}</strong> · page {relation.pageNumber} · chunk <code>{relation.chunkId}</code> · document <code>{relation.documentId}</code></p>
                </li>;
              })}</ul>
              <h4>Concept evidence</h4>
              <ul className="graph-relations">{selectedGraphConcept.evidence.map((item) => <li key={`${item.chunkId}-${item.supportingText}`}><blockquote>{item.supportingText}</blockquote><p className="citation-meta"><strong>{item.displayFilename}</strong> · page {item.pageNumber} · chunk <code>{item.chunkId}</code> · document <code>{item.documentId}</code></p></li>)}</ul>
            </article>}
          </div>
        )}
      </section>
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
