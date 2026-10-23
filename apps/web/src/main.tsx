import { StrictMode, useEffect, useRef, useState, type CSSProperties, type FormEvent } from "react";
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

type AnalyticsTopic = {
  topicId: string; topic: string; attemptCount: number; attemptedQuestions: number;
  correctQuestions: number; incorrectQuestions: number; ambiguousQuestions: number;
  accuracy: number | null; mastery: number | null; weak: boolean; overdueReviewCount: number;
  nextReviewAt: string | null; graphConceptId: string | null; citations: Citation[];
};
type LearningAnalytics = {
  generatedAt: string;
  totals: {
    attemptCount: number; attemptedQuestions: number; correctQuestions: number;
    incorrectQuestions: number; ambiguousQuestions: number; quizAccuracy: number | null;
    completedStudySessions: number; activeStudySessions: number; overdueReviews: number;
  };
  topics: AnalyticsTopic[];
  dailyActivity: { date: string; attempts: number; correct: number; completedStudySessions: number }[];
  recentActivity: { activityId: string; kind: "quiz_attempt" | "study_session"; topic: string; outcome: string | null; occurredAt: string; prompt: string | null }[];
  reviewHistory: { questionId: string; topicId: string; topic: string; outcome: string; attemptedAt: string; dueAt: string; status: "overdue" | "upcoming" }[];
  activeSessions: { sessionId: string; topicId: string; topic: string; startedAt: string; completedAt: string | null }[];
  methodology: { mastery: string; recencyHalfLifeDays: number; weakTopicThreshold: number; reviewIntervalsDays: Record<string, number>; reviewDueBoundary: string };
};
type StudyCoachRecommendation = {
  recommendationId: string; planDate: string; topicId: string; topic: string; title: string;
  priority: number; reasons: string[]; graphConceptId: string | null; relatedConceptIds: string[];
  status: "planned" | "accepted" | "dismissed" | "completed"; estimatedMinutes: number;
  citations: Citation[]; quizTopic: string;
};
type StudyCoachPlan = {
  planDate: string; recommendations: StudyCoachRecommendation[]; emptyReason: string | null; methodology: string;
};

type ProductAnalyticsFeature = {
  feature: string;
  eventCount: number;
  successRate: number;
  positiveOutcomes: number;
};
type ProductAnalyticsSummary = {
  generatedAt: string;
  enabled: boolean;
  windowDays: number;
  totals: { eventCount: number; successCount: number; failureCount: number; featureCount: number };
  features: ProductAnalyticsFeature[];
  trends: { date: string; eventCount: number; successCount: number; failureCount: number }[];
  privacy: { retentionDays: number; rawContent: string; identifiers: string };
};

type NormalizedRegion = { x: number; y: number; width: number; height: number };
type LearningImageRegion = {
  regionId: string;
  geometry: NormalizedRegion;
  ocrStatus: "available" | "empty" | "unavailable" | "failed";
  extractedText: string | null;
  diagramId: string | null;
  conceptId: string | null;
  conceptName: string | null;
  citations: Citation[];
};
type LearningImage = {
  imageId: string;
  displayFilename: string;
  contentType: "image/png" | "image/jpeg";
  width: number;
  height: number;
  byteSize: number;
  sha256: string;
  createdAt: string;
  ocrStatus: LearningImageRegion["ocrStatus"];
  extractedText: string | null;
  regions: LearningImageRegion[];
};
type LearningImageSummary = Omit<LearningImage, "regions" | "extractedText">;
type ImageEvidenceResult = {
  status: "evidence_found" | "insufficient_evidence";
  imageId: string;
  regionId: string;
  geometry: NormalizedRegion;
  extractedText: string | null;
  ocrStatus: LearningImageRegion["ocrStatus"];
  conceptName: string | null;
  results: Citation[];
  retrievalMode: string;
  fallbackReason: string | null;
};
type ImageTutorResult = Omit<ImageEvidenceResult, "status"> & {
  question: string;
  status: "answered" | "insufficient_evidence";
  answer: string | null;
  citations: Citation[];
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

async function recordProductAnalyticsEvent(
  eventName: string,
  feature: string,
  result: "success" | "failure" | "abstained" | "accepted" | "dismissed" | "started" | "completed",
  metadata: Record<string, string | number | boolean | null> = {},
) {
  try {
    await fetch("/api/v1/product-analytics/events", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ eventName, feature, result, metadata }),
    });
  } catch {
    // Intentionally non-blocking: product analytics must never disrupt user workflows.
  }
}

type SessionUser = { userId: string; username: string; expiresAt: string };

let csrfToken: string | null = null;

const fetch: typeof globalThis.fetch = async (input, init = {}) => {
  const method = (init.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
  const url = input instanceof Request ? input.url : String(input);
  const headers = new Headers(init.headers);
  if (!["GET", "HEAD", "OPTIONS", "TRACE"].includes(method)) {
    if (!csrfToken) {
      const tokenResponse = await globalThis.fetch("/api/v1/auth/csrf-token", {
        credentials: "same-origin",
      });
      if (!tokenResponse.ok) throw new Error("Could not initialize request protection.");
      const tokenBody = (await tokenResponse.json()) as { csrfToken?: string };
      if (!tokenBody.csrfToken) throw new Error("Could not initialize request protection.");
      csrfToken = tokenBody.csrfToken;
    }
    headers.set("X-CSRF-Token", csrfToken);
  }

  const response = await globalThis.fetch(input, {
    ...init,
    credentials: init.credentials ?? "same-origin",
    headers,
  });
  if (response.status === 401 && !url.includes("/api/v1/auth/")) {
    window.dispatchEvent(new Event("wonderlearn:session-expired"));
  }
  return response;
};

function AuthScreen() {
  const [session, setSession] = useState<SessionUser | null>(null);
  const [checkingSession, setCheckingSession] = useState(true);
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);
  const [authMessage, setAuthMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let active = true;
    const expired = () => {
      csrfToken = null;
      setSession(null);
      setAuthMessage("Your session has expired. Please sign in again.");
    };
    window.addEventListener("wonderlearn:session-expired", expired);
    void fetch("/api/v1/auth/me")
      .then(async (response) => {
        if (response.status === 401) return null;
        return readJson<SessionUser>(response);
      })
      .then((current) => {
        if (active) setSession(current);
      })
      .catch(() => {
        if (active) setAuthError("Could not check your session. Please try again.");
      })
      .finally(() => {
        if (active) setCheckingSession(false);
      });
    return () => {
      active = false;
      window.removeEventListener("wonderlearn:session-expired", expired);
    };
  }, []);

  useEffect(() => {
    if (!session) return;
    const expiresAt = Date.parse(session.expiresAt);
    let timer = 0;
    const expireWhenDue = () => {
      const remaining = expiresAt - Date.now();
      if (remaining <= 0) {
        window.dispatchEvent(new Event("wonderlearn:session-expired"));
      } else {
        timer = window.setTimeout(expireWhenDue, Math.min(remaining, 2_147_483_647));
      }
    };
    expireWhenDue();
    return () => window.clearTimeout(timer);
  }, [session]);

  async function submitCredentials(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setAuthError(null);
    setAuthMessage(null);
    try {
      const response = await fetch(`/api/v1/auth/${mode === "login" ? "login" : "register"}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username.trim(), password }),
      });
      if (mode === "register") {
        if (!response.ok) throw new Error("Registration could not be completed.");
        setMode("login");
        setPassword("");
        setAuthMessage("If the account can be registered, you can try signing in.");
      } else if (!response.ok) {
        setAuthError("Sign-in failed. Check your username and password.");
      } else {
        setSession(await response.json() as SessionUser);
        setPassword("");
      }
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : "Authentication could not be completed.");
    } finally {
      setSubmitting(false);
    }
  }

  async function logout() {
    try {
      const response = await fetch("/api/v1/auth/logout", { method: "POST" });
      if (!response.ok) throw new Error("Sign out could not be completed.");
      csrfToken = null;
      setSession(null);
      setAuthMessage("You have signed out.");
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : "Sign out could not be completed.");
    }
  }

  if (checkingSession) {
    return <main className="auth-shell" aria-live="polite">Checking your session…</main>;
  }
  if (session) return <LearningApp username={session.username} onLogout={() => void logout()} />;

  return (
    <main className="auth-shell">
      <section className="auth-card" aria-labelledby="auth-title">
        <p className="eyebrow">WonderLearn · private study space</p>
        <h1 id="auth-title">{mode === "login" ? "Sign in" : "Create your account"}</h1>
        <p className="muted">Your study materials and learning history are private to your account.</p>
        {authMessage && <p className="notice" role="status">{authMessage}</p>}
        {authError && <p className="notice notice--error" role="alert">{authError}</p>}
        <form className="auth-form" onSubmit={(event) => void submitCredentials(event)}>
          <label htmlFor="auth-username">Username</label>
          <input
            id="auth-username"
            name="username"
            autoComplete="username"
            minLength={3}
            maxLength={64}
            required
            value={username}
            onChange={(event) => setUsername(event.currentTarget.value)}
          />
          <label htmlFor="auth-password">Password</label>
          <input
            id="auth-password"
            name="password"
            type="password"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            minLength={8}
            maxLength={128}
            required
            value={password}
            onChange={(event) => setPassword(event.currentTarget.value)}
          />
          <button type="submit" disabled={submitting}>
            {submitting ? "Please wait…" : mode === "login" ? "Sign in" : "Register"}
          </button>
        </form>
        <p>
          {mode === "login" ? "New to WonderLearn?" : "Already registered?"}{" "}
          <button
            type="button"
            className="text-button"
            onClick={() => {
              setMode(mode === "login" ? "register" : "login");
              setAuthError(null);
            }}
          >
            {mode === "login" ? "Create an account" : "Sign in instead"}
          </button>
        </p>
      </section>
    </main>
  );
}

export function App() {
  return <AuthScreen />;
}

function LearningApp({ username, onLogout }: { username: string; onLogout: () => void }) {
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
  const [analytics, setAnalytics] = useState<LearningAnalytics | null>(null);
  const [analyticsError, setAnalyticsError] = useState<string | null>(null);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);
  const [productAnalytics, setProductAnalytics] = useState<ProductAnalyticsSummary | null>(null);
  const [sessionTopic, setSessionTopic] = useState("");
  const [studyPlan, setStudyPlan] = useState<StudyCoachPlan | null>(null);
  const [studyPlanError, setStudyPlanError] = useState<string | null>(null);
  const [studyPlanLoading, setStudyPlanLoading] = useState(false);
  const [coachExplanation, setCoachExplanation] = useState<Record<string, TutorResult>>({});
  const [coachExplanationError, setCoachExplanationError] = useState<Record<string, string>>({});
  const [learningImages, setLearningImages] = useState<LearningImageSummary[]>([]);
  const [selectedLearningImage, setSelectedLearningImage] = useState<LearningImage | null>(null);
  const [selectedImageRegionId, setSelectedImageRegionId] = useState<string | null>(null);
  const [imageConcept, setImageConcept] = useState("");
  const [imageQuestion, setImageQuestion] = useState("");
  const [imageEvidence, setImageEvidence] = useState<ImageEvidenceResult | null>(null);
  const [imageTutorResult, setImageTutorResult] = useState<ImageTutorResult | null>(null);
  const [imageLoading, setImageLoading] = useState(false);
  const [imageError, setImageError] = useState<string | null>(null);
  const [regionDraft, setRegionDraft] = useState<NormalizedRegion>({ x: 0.1, y: 0.1, width: 0.4, height: 0.4 });
  const imageDragStart = useRef<{ x: number; y: number } | null>(null);
  const [graphConcepts, setGraphConcepts] = useState<GraphConcept[]>([]);
  const [selectedGraphConcept, setSelectedGraphConcept] = useState<GraphDetail | null>(null);
  const [graphLoading, setGraphLoading] = useState(false);
  const [graphError, setGraphError] = useState<string | null>(null);
  const [graphCursor, setGraphCursor] = useState<string | null>(null);
  const [graphCursorScope, setGraphCursorScope] = useState<string | null>(null);
  const [graphDoneScope, setGraphDoneScope] = useState<string | null>(null);
  const requestNumber = useRef(0);

  useEffect(() => {
    const section = quiz?.status === "ready" ? `Quiz: ${quiz.topic}`
      : selectedGraphConcept ? `Knowledge graph: ${selectedGraphConcept.name}`
      : selectedImageRegionId ? `Image region: ${selectedImageRegionId}`
      : selectedRegion ? `Diagram: ${selectedRegion.displayName}`
      : "WonderLearn";
    document.title = `${section} — WonderLearn`;
  }, [quiz, selectedGraphConcept, selectedImageRegionId, selectedRegion]);

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
    fetch("/api/v1/learning-analytics")
      .then((response) => readJson<LearningAnalytics>(response))
      .then((result) => { if (active) setAnalytics(result); })
      .catch((error: unknown) => {
        if (active) setAnalyticsError(error instanceof Error ? error.message : "Could not load learning analytics.");
      });
    fetch("/api/v1/product-analytics")
      .then((response) => readJson<ProductAnalyticsSummary>(response))
      .then((result) => { if (active) setProductAnalytics(result); })
      .catch(() => {
        if (active) setProductAnalytics(null);
      });
    fetch("/api/v1/study-coach/plan")
      .then((response) => readJson<StudyCoachPlan>(response))
      .then((result) => { if (active) setStudyPlan(result); })
      .catch((error: unknown) => {
        if (active) setStudyPlanError(error instanceof Error ? error.message : "Could not load the study plan.");
      });
    fetch("/api/v1/knowledge-graph")
      .then((response) => readJson<{ concepts: GraphConcept[] }>(response))
      .then((result) => { if (active) setGraphConcepts(result.concepts); })
      .catch(() => { if (active) setGraphError("Could not load the local knowledge graph."); });
    fetch("/api/v1/images")
      .then((response) => readJson<{ images: LearningImageSummary[] }>(response))
      .then((result) => {
        if (!active) return;
        setLearningImages(result.images);
        if (result.images[0]) {
          void fetch(`/api/v1/images/${encodeURIComponent(result.images[0].imageId)}`)
            .then((response) => readJson<LearningImage>(response))
            .then((image) => { if (active) setSelectedLearningImage(image); })
            .catch(() => { if (active) setImageError("Could not load the saved image and its regions."); });
        }
      })
      .catch((error: unknown) => {
        if (active) setImageError(error instanceof Error ? error.message : "Could not load saved images.");
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
      void recordProductAnalyticsEvent("document_upload_completed", "documents", "success", {
        documentId: document.documentId,
        filename: file.name,
        byteSize: file.size,
      });
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : "Could not upload the PDF.");
    } finally {
      setUploading(false);
    }
  }

  async function loadLearningImage(imageId: string) {
    const response = await fetch(`/api/v1/images/${encodeURIComponent(imageId)}`);
    const image = await readJson<LearningImage>(response);
    setSelectedLearningImage(image);
    setLearningImages((current) => {
      const { regions: _regions, ...summary } = image;
      return [summary, ...current.filter((item) => item.imageId !== imageId)];
    });
    return image;
  }

  async function selectLearningImage(imageId: string) {
    setSelectedImageRegionId(null);
    setImageEvidence(null);
    setImageTutorResult(null);
    setImageError(null);
    try {
      await loadLearningImage(imageId);
    } catch (error) {
      setImageError(error instanceof Error ? error.message : "Could not load this image.");
    }
  }

  async function uploadLearningImage(file: File | undefined) {
    if (!file) return;
    setImageLoading(true);
    setImageError(null);
    try {
      const body = new FormData();
      body.append("file", file);
      const response = await fetch("/api/v1/images", { method: "POST", body });
      const image = await readJson<LearningImage>(response);
      setSelectedLearningImage(image);
      const { regions: _regions, ...summary } = image;
      setLearningImages((current) => [summary, ...current.filter((item) => item.imageId !== image.imageId)]);
      setSelectedImageRegionId(null);
      setImageEvidence(null);
      setImageTutorResult(null);
      void recordProductAnalyticsEvent("image_upload_completed", "images", "success", {
        imageId: image.imageId,
        filename: file.name,
        byteSize: file.size,
      });
    } catch (error) {
      setImageError(error instanceof Error ? error.message : "Could not upload this image.");
    } finally {
      setImageLoading(false);
    }
  }

  function imagePointerPoint(event: React.PointerEvent<HTMLDivElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)),
      y: Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height)),
    };
  }

  function startImageRegion(event: React.PointerEvent<HTMLDivElement>) {
    if (!selectedLearningImage || event.button !== 0) return;
    event.preventDefault();
    const point = imagePointerPoint(event);
    imageDragStart.current = point;
    setRegionDraft({ x: point.x, y: point.y, width: 0.01, height: 0.01 });
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function moveImageRegion(event: React.PointerEvent<HTMLDivElement>) {
    const start = imageDragStart.current;
    if (!start) return;
    const point = imagePointerPoint(event);
    setRegionDraft({
      x: Math.min(start.x, point.x),
      y: Math.min(start.y, point.y),
      width: Math.abs(point.x - start.x),
      height: Math.abs(point.y - start.y),
    });
  }

  function finishImageRegion(event: React.PointerEvent<HTMLDivElement>) {
    moveImageRegion(event);
    imageDragStart.current = null;
  }

  async function addImageRegion(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedLearningImage) return;
    setImageLoading(true);
    setImageError(null);
    try {
      const [diagramId, conceptId] = imageConcept ? imageConcept.split("|") : [];
      const response = await fetch(`/api/v1/images/${encodeURIComponent(selectedLearningImage.imageId)}/regions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          geometry: regionDraft,
          ...(diagramId && conceptId ? { diagramId, conceptId } : {}),
        }),
      });
      const region = await readJson<LearningImageRegion>(response);
      await loadLearningImage(selectedLearningImage.imageId);
      setSelectedImageRegionId(region.regionId);
      await findImageEvidence(region.regionId);
    } catch (error) {
      setImageError(error instanceof Error ? error.message : "Could not save this image region.");
    } finally {
      setImageLoading(false);
    }
  }

  async function findImageEvidence(regionId = selectedImageRegionId) {
    if (!selectedLearningImage || !regionId) return;
    setImageLoading(true);
    setImageError(null);
    setImageTutorResult(null);
    try {
      const response = await fetch(
        `/api/v1/images/${encodeURIComponent(selectedLearningImage.imageId)}/regions/${encodeURIComponent(regionId)}/evidence`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ limit: 5 }) },
      );
      const result = await readJson<ImageEvidenceResult>(response);
      setImageEvidence(result);
      await loadLearningImage(selectedLearningImage.imageId);
      setSelectedImageRegionId(regionId);
    } catch (error) {
      setImageError(error instanceof Error ? error.message : "Could not retrieve evidence for this region.");
    } finally {
      setImageLoading(false);
    }
  }

  async function askImageTutor(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedLearningImage || !selectedImageRegionId) return;
    setImageLoading(true);
    setImageError(null);
    setImageTutorResult(null);
    try {
      const response = await fetch(
        `/api/v1/images/${encodeURIComponent(selectedLearningImage.imageId)}/regions/${encodeURIComponent(selectedImageRegionId)}/tutor`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: imageQuestion, limit: 5 }),
        },
      );
      setImageTutorResult(await readJson<ImageTutorResult>(response));
      await loadLearningImage(selectedLearningImage.imageId);
      setSelectedImageRegionId(selectedImageRegionId);
    } catch (error) {
      setImageError(error instanceof Error ? error.message : "The tutor could not answer for this image region.");
    } finally {
      setImageLoading(false);
    }
  }

  async function deleteLearningImage() {
    if (!selectedLearningImage) return;
    setImageLoading(true);
    setImageError(null);
    try {
      await readJson(await fetch(`/api/v1/images/${encodeURIComponent(selectedLearningImage.imageId)}`, { method: "DELETE" }));
      const remaining = learningImages.filter((image) => image.imageId !== selectedLearningImage.imageId);
      setLearningImages(remaining);
      setSelectedLearningImage(null);
      setSelectedImageRegionId(null);
      setImageEvidence(null);
      setImageTutorResult(null);
      if (remaining[0]) await loadLearningImage(remaining[0].imageId);
    } catch (error) {
      setImageError(error instanceof Error ? error.message : "Could not delete this image.");
    } finally {
      setImageLoading(false);
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
    await refreshAnalytics();
  }

  async function refreshAnalytics() {
    const response = await fetch("/api/v1/learning-analytics");
    setAnalytics(await readJson<LearningAnalytics>(response));
    await refreshStudyPlan();
  }

  async function refreshStudyPlan() {
    const response = await fetch("/api/v1/study-coach/plan");
    setStudyPlan(await readJson<StudyCoachPlan>(response));
  }

  async function updateStudyRecommendation(
    recommendationId: string,
    action: "accept" | "edit" | "dismiss" | "complete",
    fields?: { title?: string; estimatedMinutes?: number },
  ) {
    setStudyPlanLoading(true);
    setStudyPlanError(null);
    try {
      const response = await fetch(`/api/v1/study-coach/recommendations/${encodeURIComponent(recommendationId)}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, ...(fields ?? {}) }),
      });
      await readJson<StudyCoachRecommendation>(response);
      await refreshStudyPlan();
    } catch (error) {
      setStudyPlanError(error instanceof Error ? error.message : "Could not update the study recommendation.");
    } finally { setStudyPlanLoading(false); }
  }

  async function explainStudyRecommendation(recommendationId: string) {
    setStudyPlanLoading(true);
    setCoachExplanationError((current) => ({ ...current, [recommendationId]: "" }));
    try {
      const response = await fetch(`/api/v1/study-coach/recommendations/${encodeURIComponent(recommendationId)}/explanation`, { method: "POST" });
      const result = await readJson<TutorResult>(response);
      setCoachExplanation((current) => ({ ...current, [recommendationId]: result }));
    } catch (error) {
      setCoachExplanationError((current) => ({ ...current, [recommendationId]: error instanceof Error ? error.message : "The local tutor could not explain this recommendation." }));
    } finally { setStudyPlanLoading(false); }
  }

  async function beginStudySession(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAnalyticsLoading(true);
    setAnalyticsError(null);
    try {
      const response = await fetch("/api/v1/study-sessions", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topic: sessionTopic.trim() }),
      });
      await readJson<{ sessionId: string }>(response);
      await refreshAnalytics();
    } catch (error) {
      setAnalyticsError(error instanceof Error ? error.message : "Could not start a study session.");
    } finally { setAnalyticsLoading(false); }
  }

  async function completeStudySession(sessionId: string) {
    setAnalyticsLoading(true);
    setAnalyticsError(null);
    try {
      const response = await fetch(`/api/v1/study-sessions/${encodeURIComponent(sessionId)}/complete`, { method: "POST" });
      await readJson<{ sessionId: string }>(response);
      await refreshAnalytics();
    } catch (error) {
      setAnalyticsError(error instanceof Error ? error.message : "Could not complete the study session.");
    } finally { setAnalyticsLoading(false); }
  }

  async function practiceAnalyticsTopic(topic: AnalyticsTopic) {
    if (topic.graphConceptId) {
      await loadGraphConcept(topic.graphConceptId);
      setTutorQuestion(`Help me review ${topic.topic} using my study materials.`);
      document.getElementById("tutor-question")?.focus();
    } else {
      await generateQuiz(topic.topic);
    }
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
    <main className="page" id="main-content" tabIndex={-1}>
      <a href="#main-content" className="skip-link" onClick={(event) => { event.preventDefault(); document.getElementById("main-content")?.focus(); }}>Skip to main content</a>
      <header className="page-header">
        <div className="session-controls">
          <span>Signed in as <strong>{username}</strong></span>
          <button type="button" onClick={onLogout}>Sign out</button>
        </div>
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

      <section className="image-learning" aria-labelledby="image-learning-title">
        <div className="image-learning-heading">
          <div>
            <p className="eyebrow">Local image study</p>
            <h2 id="image-learning-title">Explore an image or diagram</h2>
            <p className="muted">PNG and JPEG uploads are validated and stored on this device. They are never sent to an external image service. Camera frames are not part of this workflow.</p>
          </div>
          {selectedLearningImage && <button type="button" className="camera-action" disabled={imageLoading} onClick={() => void deleteLearningImage()}>Delete uploaded image</button>}
        </div>
        <label className="document-control" htmlFor="learning-image-upload">
          Upload an educational image
          <input
            id="learning-image-upload"
            type="file"
            accept="image/png,image/jpeg"
            disabled={imageLoading}
            onChange={(event) => void uploadLearningImage(event.currentTarget.files?.[0])}
            aria-invalid={imageError ? true : undefined}
            aria-describedby={imageError ? "learning-image-upload-error" : undefined}
          />
        </label>
        {imageLoading && <p className="muted" role="status">Processing image locally…</p>}
        {imageError && <p id="learning-image-upload-error" className="notice notice--error" role="alert">{imageError}</p>}
        {learningImages.length > 0 && <nav className="image-saved-list" aria-label="Saved educational images">
          {learningImages.map((image) => <button
            key={image.imageId}
            type="button"
            aria-pressed={selectedLearningImage?.imageId === image.imageId}
            disabled={imageLoading}
            onClick={() => void selectLearningImage(image.imageId)}
          >{image.displayFilename}</button>)}
        </nav>}
        {!selectedLearningImage && <p className="notice" role="status">Upload a PNG or JPEG image to select regions and search your indexed study materials.</p>}
        {selectedLearningImage && <div className="image-learning-grid">
          <div>
            <p className="image-dimensions">{selectedLearningImage.displayFilename} · {selectedLearningImage.width} × {selectedLearningImage.height}px</p>
            <div
              className="image-selection-canvas"
              role="group"
              aria-label="Image region selection surface. Drag to select a region, or enter normalized coordinates in the form below."
              onPointerDown={startImageRegion}
              onPointerMove={moveImageRegion}
              onPointerUp={finishImageRegion}
              onPointerCancel={() => { imageDragStart.current = null; }}
            >
              <img src={`/api/v1/images/${encodeURIComponent(selectedLearningImage.imageId)}/content`} alt={selectedLearningImage.displayFilename} draggable={false} />
              {selectedLearningImage.regions.map((region) => <span
                key={region.regionId}
                aria-hidden="true"
                className={`image-region-box${region.regionId === selectedImageRegionId ? " image-region-box--selected" : ""}`}
                style={{ left: `${region.geometry.x * 100}%`, top: `${region.geometry.y * 100}%`, width: `${region.geometry.width * 100}%`, height: `${region.geometry.height * 100}%` }}
              />)}
              {imageDragStart.current && <span aria-hidden="true" className="image-region-box image-region-box--draft" style={{ left: `${regionDraft.x * 100}%`, top: `${regionDraft.y * 100}%`, width: `${regionDraft.width * 100}%`, height: `${regionDraft.height * 100}%` }} />}
            </div>
            <p className="muted">Drag across the image to set a region. Keyboard users can enter normalized coordinates below (0–1).</p>
            <p className="notice" role="status">
              {selectedLearningImage.ocrStatus === "available"
                ? "Local OCR extracted text from this image. Review it before using it as a retrieval query."
                : selectedLearningImage.ocrStatus === "empty"
                ? "Local OCR ran but found no text in this image. You can still attach a curated concept or ask a question about a selected region."
                : selectedLearningImage.ocrStatus === "unavailable"
                ? "OCR is unavailable. Install the optional Python OCR extra and the Tesseract executable locally (see the setup guide); no OCR result is being claimed."
                : "Local OCR could not process this image. You can still attach a curated concept or ask a question about a selected region."}
            </p>
            {selectedLearningImage.extractedText && <details className="image-extracted-text"><summary>Extracted image text</summary><blockquote>{selectedLearningImage.extractedText}</blockquote></details>}
          </div>
          <div className="image-region-controls">
            <form className="image-region-form" onSubmit={(event) => void addImageRegion(event)}>
              <h3>Create a region</h3>
              <fieldset className="image-coordinate-fields">
                <legend>Normalized region coordinates</legend>
                {(["x", "y", "width", "height"] as const).map((coordinate) => <label key={coordinate}>
                  {coordinate}
                  <input
                    aria-label={`Region ${coordinate}`}
                    type="number"
                    min={coordinate === "width" || coordinate === "height" ? 0.001 : 0}
                    max={1}
                    step={0.001}
                    value={regionDraft[coordinate]}
                    onChange={(event) => setRegionDraft((current) => ({ ...current, [coordinate]: Number(event.currentTarget.value) }))}
                  />
                </label>)}
              </fieldset>
              <label className="document-control" htmlFor="image-curated-concept">Optional curated concept
                <select id="image-curated-concept" value={imageConcept} onChange={(event) => setImageConcept(event.currentTarget.value)}>
                  <option value="">Use extracted text or your question</option>
                  {diagram?.regions.map((region) => <option key={region.conceptId} value={`${diagram.diagramId}|${region.conceptId}`}>{region.displayName}</option>)}
                </select>
              </label>
              <button className="camera-action camera-action--primary" type="submit" disabled={imageLoading}>Save region and search evidence</button>
            </form>
            <h3>Selected regions</h3>
            {selectedLearningImage.regions.length === 0
              ? <p className="muted">No regions saved for this image yet.</p>
              : <ul className="image-region-list">{selectedLearningImage.regions.map((region, index) => <li key={region.regionId}>
                <button type="button" aria-pressed={region.regionId === selectedImageRegionId} onClick={() => { setSelectedImageRegionId(region.regionId); setImageEvidence(null); setImageTutorResult(null); void findImageEvidence(region.regionId); }}>Region {index + 1}{region.conceptName ? ` · ${region.conceptName}` : ""}</button>
                <span>{region.geometry.x.toFixed(2)}, {region.geometry.y.toFixed(2)} · {region.geometry.width.toFixed(2)} × {region.geometry.height.toFixed(2)}</span>
                {region.extractedText && <blockquote>{region.extractedText}</blockquote>}
              </li>)}</ul>}
            {selectedImageRegionId && <>
              <button type="button" className="camera-action" disabled={imageLoading} onClick={() => void findImageEvidence()}>Find evidence for this region</button>
              <form className="image-tutor-form" onSubmit={(event) => void askImageTutor(event)}>
                <label className="document-control" htmlFor="image-region-question">Ask about the selected region
                  <textarea id="image-region-question" value={imageQuestion} maxLength={2_000} minLength={1} required rows={3} onChange={(event) => setImageQuestion(event.currentTarget.value)} placeholder="Ask about what this part shows" aria-invalid={imageError ? true : undefined} aria-describedby={imageError ? "image-region-question-error" : undefined} />
                </label>
                <button className="camera-action camera-action--primary" type="submit" disabled={imageLoading || tutorConfigured === false}>{imageLoading ? "Searching and asking…" : "Ask grounded tutor"}</button>
              </form>
              {tutorConfigured === false && <p className="muted">Evidence search is available without the model. Configure the optional local tutor to generate an explanation.</p>}
              {imageError && <p id="image-region-question-error" className="notice notice--error" role="alert">{imageError}</p>}
              {imageTutorResult?.status === "insufficient_evidence" && <p className="notice" role="status">No supporting passage was found, or the tutor abstained. No answer was invented.</p>}
              {imageTutorResult?.status === "answered" && imageTutorResult.answer && <div className="image-tutor-answer" aria-live="polite"><h4>Grounded explanation</h4><p>{imageTutorResult.answer}</p></div>}
              {imageEvidence?.status === "insufficient_evidence" && !imageTutorResult && <p className="notice" role="status">No indexed passage supports this region yet. Try a curated concept or add learning material.</p>}
              {(imageTutorResult?.citations ?? imageEvidence?.results ?? selectedLearningImage.regions.find((item) => item.regionId === selectedImageRegionId)?.citations ?? []).length > 0 && <ol className="evidence-list image-evidence-list">
                {(imageTutorResult?.citations ?? imageEvidence?.results ?? selectedLearningImage.regions.find((item) => item.regionId === selectedImageRegionId)?.citations ?? []).map((citation) => <li className="evidence-item" key={citation.citationId}>
                  <blockquote>{citation.text}</blockquote>
                  <p className="citation-meta"><strong>{citation.displayFilename}</strong> · page {citation.pageNumber} · chunk <code>{citation.citationId}</code></p>
                </li>)}
              </ol>}
            </>}
          </div>
        </div>}
      </section>

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
                  aria-invalid={uploadError ? true : undefined}
                  aria-describedby={uploadError ? "pdf-upload-error" : undefined}
                />
              </label>
              {uploading && <p className="muted" role="status">Indexing PDF locally…</p>}
              {uploadError && <p id="pdf-upload-error" className="notice notice--error" role="alert">{uploadError}</p>}
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
                    aria-invalid={tutorError ? true : undefined}
                    aria-describedby={tutorError ? "tutor-question-error" : undefined}
                  />
                </label>
                <button className="camera-action camera-action--primary" type="submit" disabled={tutorLoading}>
                  {tutorLoading ? "Retrieving and asking…" : "Ask tutor"}
                </button>
              </form>
              {tutorLoading && <p className="muted" role="status">Finding evidence and generating a grounded explanation…</p>}
              {tutorError && <p id="tutor-question-error" className="notice notice--error" role="alert">{tutorError}</p>}
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
                    aria-invalid={quizError ? true : undefined}
                    aria-describedby={quizError ? "quiz-topic-error" : undefined}
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
              {quizError && <p id="quiz-topic-error" className="notice notice--error" role="alert">{quizError}</p>}
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
      {productAnalytics && (
        <section className="analytics-panel" aria-labelledby="product-analytics-title">
          <div className="analytics-heading">
            <div>
              <p className="eyebrow">Privacy-safe feature usage</p>
              <h2 id="product-analytics-title">Product analytics snapshot</h2>
            </div>
          </div>
          <p className="muted">{productAnalytics.totals.eventCount} product events tracked over the last {productAnalytics.windowDays} days.</p>
          <ul className="progress-topics">
            {productAnalytics.features.slice(0, 5).map((feature) => (
              <li key={feature.feature}>
                <span><strong>{feature.feature}</strong> · {feature.eventCount} events</span>
                <span>{Math.round(feature.successRate * 100)}% success</span>
              </li>
            ))}
          </ul>
        </section>
      )}
      <section className="study-coach" aria-labelledby="study-coach-title">
        <div className="analytics-heading">
          <div>
            <p className="eyebrow">A daily plan from your saved learning activity</p>
            <h2 id="study-coach-title">AI study coach</h2>
            <p className="muted">Recommendations and priorities are calculated with deterministic rules. The optional local tutor can explain a recommendation using only its linked passages.</p>
          </div>
        </div>
        {studyPlanLoading && <p className="muted" role="status">Updating today’s plan…</p>}
        {studyPlanError && <p className="notice notice--error" role="alert">{studyPlanError}</p>}
        {!studyPlan && !studyPlanError && <p className="muted" role="status">Loading today’s plan…</p>}
        {studyPlan && studyPlan.recommendations.length === 0 && <p className="muted" role="status">{studyPlan.emptyReason ?? "No recommendations are available today."}</p>}
        {studyPlan && studyPlan.recommendations.length > 0 && <ol className="study-plan-list">
          {studyPlan.recommendations.map((recommendation) => <li key={recommendation.recommendationId}>
            <article className="study-plan-item">
              <div className="study-plan-heading">
                <div><h3>{recommendation.title}</h3><p><strong>{recommendation.topic}</strong> · {recommendation.estimatedMinutes} min · priority {recommendation.priority}</p></div>
                <span className={`study-plan-status study-plan-status--${recommendation.status}`}>{recommendation.status}</span>
              </div>
              <h4>Why this is suggested</h4>
              <ul>{recommendation.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
              <div className="study-plan-links">
                <button type="button" className="text-action" disabled={quizLoading || studyPlanLoading} onClick={() => void generateQuiz(recommendation.quizTopic)}>Practice with a cited quiz</button>
                {recommendation.graphConceptId && <button type="button" className="text-action" disabled={graphLoading || studyPlanLoading} onClick={() => void loadGraphConcept(recommendation.graphConceptId!)}>Open knowledge graph topic</button>}
              </div>
              <details><summary>Supporting study passages ({recommendation.citations.length})</summary>
                <ol className="evidence-list">{recommendation.citations.map((citation) => <li className="evidence-item" key={citation.citationId}>
                  <blockquote>{citation.text}</blockquote><p className="citation-meta"><strong>{citation.displayFilename}</strong> · page {citation.pageNumber} · chunk <code>{citation.citationId}</code></p>
                </li>)}</ol>
              </details>
              {tutorConfigured && <div className="study-plan-explainer">
                <button type="button" className="text-action" disabled={studyPlanLoading} onClick={() => void explainStudyRecommendation(recommendation.recommendationId)}>Explain with local tutor</button>
                {coachExplanationError[recommendation.recommendationId] && <p className="notice notice--error" role="alert">{coachExplanationError[recommendation.recommendationId]}</p>}
                {coachExplanation[recommendation.recommendationId]?.status === "answered" && <div className="tutor-answer"><p>{coachExplanation[recommendation.recommendationId].answer}</p>{coachExplanation[recommendation.recommendationId].citations.map((citation) => <p className="citation-meta" key={citation.citationId}><strong>{citation.displayFilename}</strong> · page {citation.pageNumber} · <code>{citation.citationId}</code></p>)}</div>}
                {coachExplanation[recommendation.recommendationId]?.status === "insufficient_evidence" && <p className="muted" role="status">The tutor abstained because the saved passages did not support an explanation.</p>}
              </div>}
              <div className="study-plan-actions">
                {recommendation.status === "planned" && <button type="button" className="camera-action" disabled={studyPlanLoading} onClick={() => void updateStudyRecommendation(recommendation.recommendationId, "accept")}>Accept</button>}
                {(recommendation.status === "planned" || recommendation.status === "accepted") && <button type="button" className="camera-action camera-action--primary" disabled={studyPlanLoading} onClick={() => void updateStudyRecommendation(recommendation.recommendationId, "complete")}>Mark complete</button>}
                {(recommendation.status === "planned" || recommendation.status === "accepted") && <button type="button" className="camera-action" disabled={studyPlanLoading} onClick={() => void updateStudyRecommendation(recommendation.recommendationId, "dismiss")}>Dismiss</button>}
                {(recommendation.status === "planned" || recommendation.status === "accepted") && <details><summary>Edit</summary><form className="study-plan-edit" onSubmit={(event) => { event.preventDefault(); const form = new FormData(event.currentTarget); void updateStudyRecommendation(recommendation.recommendationId, "edit", { title: String(form.get("title")), estimatedMinutes: Number(form.get("minutes")) }); }}>
                  <label>Plan title<input name="title" defaultValue={recommendation.title} minLength={2} maxLength={160} required /></label>
                  <label>Minutes<input name="minutes" type="number" defaultValue={recommendation.estimatedMinutes} min={5} max={180} required /></label>
                  <button type="submit" className="camera-action">Save changes</button>
                </form></details>}
              </div>
            </article>
          </li>)}
        </ol>}
        {studyPlan && <details className="analytics-methodology"><summary>How priorities are calculated</summary><p>{studyPlan.methodology}</p></details>}
      </section>
      <section className="analytics-panel" aria-labelledby="analytics-title">
        <div className="analytics-heading">
          <div>
            <p className="eyebrow">Your saved learning activity</p>
            <h2 id="analytics-title">Learning analytics</h2>
            <p className="muted">These summaries use persisted quiz attempts, explicit study-session completions, and due-review records.</p>
          </div>
        </div>
          {analyticsError && <p id="session-topic-error" className="notice notice--error" role="alert">{analyticsError}</p>}
        {analyticsLoading && <p className="muted" role="status">Updating learning activity…</p>}
        {!analytics && !analyticsError && <p className="muted" role="status">Loading saved learning activity…</p>}
        {analytics && <>
          <div className="analytics-stats">
            <div><dt>Quiz accuracy</dt><dd>{analytics.totals.quizAccuracy === null ? "No scored answers" : `${Math.round(analytics.totals.quizAccuracy * 100)}%`}</dd></div>
            <div><dt>Questions attempted</dt><dd>{analytics.totals.attemptedQuestions}</dd></div>
            <div><dt>Completed study sessions</dt><dd>{analytics.totals.completedStudySessions}</dd></div>
            <div><dt>Reviews overdue</dt><dd>{analytics.totals.overdueReviews}</dd></div>
          </div>
          <form className="session-form" onSubmit={(event) => void beginStudySession(event)}>
            <label className="document-control" htmlFor="session-topic">Study session topic
              <input id="session-topic" value={sessionTopic} minLength={2} maxLength={160} required onChange={(event) => setSessionTopic(event.currentTarget.value)} placeholder="What are you studying?" aria-invalid={analyticsError ? true : undefined} aria-describedby={analyticsError ? "session-topic-error" : undefined} />
            </label>
            <button type="submit" className="camera-action camera-action--primary" disabled={analyticsLoading}>Start session</button>
          </form>
          {analytics.activeSessions.map((session) => <div className="active-session" key={session.sessionId}>
            <span>In progress: <strong>{session.topic}</strong> · started {new Date(session.startedAt).toLocaleString()}</span>
            <button type="button" className="camera-action" disabled={analyticsLoading} onClick={() => void completeStudySession(session.sessionId)}>Mark session complete</button>
          </div>)}
          {analytics.totals.attemptCount === 0 && analytics.totals.completedStudySessions === 0 ? (
            <p className="notice" role="status">No quiz attempts or completed study sessions are recorded yet. Statistics will appear after you complete learning activities.</p>
          ) : <>
            {analytics.topics.length > 0 && <section aria-labelledby="topic-comparison-title" className="analytics-subsection">
              <h3 id="topic-comparison-title">Topic mastery and review</h3>
              <p className="muted">Mastery is a recency-weighted summary of each question’s latest scored result; ambiguous answers are unscored. “Weak” is an operational threshold, not a psychological diagnosis.</p>
              <div className="analytics-topic-list">
                {analytics.topics.map((topic) => <article className="analytics-topic" key={topic.topicId}>
                  <div className="analytics-topic-heading"><h4>{topic.topic}</h4><span>{topic.attemptCount} attempts · {topic.attemptedQuestions} questions</span></div>
                  <p className="analytics-topic-metrics">Accuracy {topic.accuracy === null ? "not scored" : `${Math.round(topic.accuracy * 100)}%`} · mastery {topic.mastery === null ? "not scored" : `${Math.round(topic.mastery * 100)}%`} · {topic.overdueReviewCount} overdue review{topic.overdueReviewCount === 1 ? "" : "s"}</p>
                  {topic.mastery !== null && <div className="mastery-track" role="progressbar" aria-label={`${topic.topic} mastery`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(topic.mastery * 100)}><span style={{ width: `${Math.round(topic.mastery * 100)}%` }} /></div>}
                  {topic.weak && <div className="weak-topic-actions">
                    <strong>Review suggested</strong>
                    {topic.graphConceptId && <button type="button" className="text-action" onClick={() => void loadGraphConcept(topic.graphConceptId!)}>Open linked graph concept and sources</button>}
                    <button type="button" className="text-action" disabled={quizLoading || tutorLoading} onClick={() => void practiceAnalyticsTopic(topic)}>{topic.graphConceptId ? "Practice with grounded tutor" : "Practice with an evidence-backed quiz"}</button>
                  </div>}
                  {topic.citations.length > 0 && <details className="topic-citations"><summary>Source passages ({topic.citations.length})</summary>
                    <ul>{topic.citations.map((citation) => <li key={citation.citationId}><blockquote>{citation.text}</blockquote><span><strong>{citation.displayFilename}</strong> · page {citation.pageNumber} · chunk <code>{citation.citationId}</code></span></li>)}</ul>
                  </details>}
                </article>)}
              </div>
            </section>}
            {analytics.dailyActivity.some((day) => day.attempts > 0 || day.completedStudySessions > 0) && <figure className="activity-chart" aria-labelledby="activity-chart-title">
              <figcaption id="activity-chart-title"><h3>Activity over the last 14 days</h3><p className="muted">Bars show stored quiz attempts and completed sessions by UTC date.</p></figcaption>
              <ol>{analytics.dailyActivity.map((day) => <li key={day.date} aria-label={`${day.date}: ${day.attempts} attempts, ${day.completedStudySessions} completed sessions`}>
                <span className="activity-bar" style={{ height: `${Math.max(4, Math.round((day.attempts + day.completedStudySessions) / Math.max(1, ...analytics.dailyActivity.map((item) => item.attempts + item.completedStudySessions)) * 100))}%` }} />
                <span className="activity-bar-value">{day.attempts + day.completedStudySessions || ""}</span>
                <time dateTime={day.date}>{new Date(`${day.date}T00:00:00Z`).toLocaleDateString(undefined, { month: "numeric", day: "numeric", timeZone: "UTC" })}</time>
              </li>)}</ol>
            </figure>}
            <section className="analytics-subsection" aria-labelledby="review-history-title">
              <h3 id="review-history-title">Review schedule and activity history</h3>
              {analytics.reviewHistory.length === 0 && <p className="muted">No review dates are scheduled yet. Quiz attempts create a local reminder: correct answers in 7 days; incorrect or uncertain answers in 1 day.</p>}
              {analytics.reviewHistory.length > 0 && <ul className="review-schedule">{analytics.reviewHistory.slice(0, 20).map((item) => <li key={item.questionId}><strong>{item.topic}</strong><span>{item.status === "overdue" ? "Overdue since" : "Due"} {new Date(item.dueAt).toLocaleString()} · after {item.outcome} attempt</span></li>)}</ul>}
              {analytics.recentActivity.length === 0 ? <p className="muted">No recent activity is recorded.</p> : <ol className="analytics-history">{analytics.recentActivity.map((item) => <li key={item.activityId}><span>{item.kind === "study_session" ? "Completed study session" : `Quiz ${item.outcome ?? "attempt"}`} · {item.topic}{item.prompt ? ` — ${item.prompt}` : ""}</span><time dateTime={item.occurredAt}>{new Date(item.occurredAt).toLocaleString()}</time></li>)}</ol>}
            </section>
          </>}
          <details className="analytics-methodology"><summary>How these analytics are calculated</summary>
            <p>{analytics.methodology.mastery} The weak-topic flag is below {Math.round(analytics.methodology.weakTopicThreshold * 100)}% mastery. Review reminders use a local deterministic interval of {analytics.methodology.reviewIntervalsDays.correct} days after a correct result and {analytics.methodology.reviewIntervalsDays.incorrect} day after an incorrect or ambiguous result; a review is overdue at the displayed due time. These are scheduling rules, not validated learning-science claims.</p>
          </details>
        </>}
      </section>
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
