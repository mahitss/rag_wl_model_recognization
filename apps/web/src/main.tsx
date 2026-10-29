import { StrictMode, useEffect, useRef, useState, type CSSProperties, type FormEvent } from "react";
import { createRoot } from "react-dom/client";
import { CameraPanel } from "./CameraPanel";
import { normalizedRegionStyle } from "./geometry";
import { fetchWithOffline, isOffline, onOnlineChange } from "./offlineClient";
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
  positiveOutcomeRate: number | null;
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

type FeedbackCategory = "bug" | "usability" | "feature_request" | "other";
type FeedbackStatus = "new" | "reviewing" | "planned" | "resolved" | "withdrawn";
type FeedbackItem = {
  feedbackId: string;
  userId: string;
  username: string | null;
  category: FeedbackCategory;
  title: string;
  description: string;
  status: FeedbackStatus;
  resolutionNotes: string | null;
  improvementTaskId: string | null;
  createdAt: string;
  updatedAt: string;
};

type ImprovementTask = {
  taskId: string;
  title: string;
  description: string;
  status: "proposed" | "in_progress" | "resolved" | "declined";
  priorityScore: number;
  priorityFactors: {
    severity: number;
    impact: number;
    reportCount: number;
    reproducibility: number;
    workflowFrequency: number;
    implementationEffort: number;
    measured: string[];
    estimated: string[];
  };
  severity: number;
  impact: number;
  reportCount: number;
  reproducibility: number;
  workflowFrequency: number;
  implementationEffort: number;
  regressionTest: string | null;
  implemented: boolean;
  implementedAt: string | null;
  userId: string;
  username: string | null;
  createdAt: string;
  updatedAt: string;
  feedbackIds: string[];
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

type ContentResourceSection = {
  sectionId: string;
  resourceId: string;
  sectionType: "heading" | "body" | "flashcard" | "question" | "summary";
  sectionTitle: string;
  content: string;
  citations: Citation[];
  version: number;
  createdAt: string;
  verificationStatus?: "verified" | "needs_review" | "insufficient_evidence" | null;
  verificationMethod?: "deterministic" | "human" | null;
  verificationDetails?: { reviewerNotes?: string } | null;
  verifiedAt?: string | null;
  verifiedBy?: string | null;
};
type ContentResource = {
  resourceId: string;
  userId: string;
  title: string;
  description: string | null;
  format: "notes" | "summary" | "flashcards" | "questions" | "revision_sheet" | "concept_outline";
  detailLevel: "brief" | "standard" | "comprehensive";
  status: "draft" | "reviewed" | "archived";
  sourceType: "document" | "workspace";
  sourceRefs: any[];
  sections: ContentResourceSection[];
  createdAt: string;
  updatedAt: string;
  reviewedAt: string | null;
  reviewedBy: string | null;
};
type ContentResourceCreateRequest = {
  title: string;
  description?: string;
  format: "notes" | "summary" | "flashcards" | "questions" | "revision_sheet" | "concept_outline";
  detailLevel?: "brief" | "standard" | "comprehensive";
  topic: string;
  documentIds?: string[];
  spaceId?: string;
};

type ContentResourceValidation = {
  validationId: string;
  resourceId: string;
  userId: string;
  citationValidity: boolean;
  evidenceCoverage: number;
  schemaValidity: boolean;
  unsupportedClaimCount: number;
  questionAnswerCorrectness: number | null;
  verificationMethod: string;
  status: "verified" | "needs_review" | "insufficient_evidence";
  details: {
    invalidCitations: Array<{ citationId: string | null; reason: string }>;
    sectionResults: Array<{
      sectionId: string;
      citationValidity: boolean;
      schemaValidity: boolean;
      evidenceCoverage: number;
      unsupportedClaimCount: number;
      questionAnswerCorrectness: number | null;
    }>;
  };
  sections: ContentResourceSection[];
};

type SearchResult = {
  resultType: "document_passage" | "content_section" | "quiz_question" | "learning_objective" | "knowledge_concept";
  id: string;
  title: string;
  snippet: string;
  score: number;
  sourceType: "document" | "content_resource" | "quiz" | "learning_objective" | "knowledge_concept";
  sourceId: string;
  sourceTitle: string | null;
  pageNumber: number | null;
  citations: Citation[];
  createdAt: string | null;
  updatedAt: string | null;
};

type RevisionQueueItem = {
  itemId: string;
  itemType: "quiz" | "flashcard";
  topic: string;
  title: string;
  prompt: string;
  questionType: "multiple_choice" | "short_answer" | null;
  options: { id: string; text: string }[] | null;
  explanation: string | null;
  lastResult: "remembered" | "partially" | "forgotten";
  lastAttemptedAt: string | null;
  dueAt: string;
  citations: Citation[];
  sourceType: "quiz" | "content_resource";
  sectionTitle: string | null;
  sectionType: "heading" | "body" | "flashcard" | "question" | "summary" | null;
  resourceTitle: string | null;
};

type RevisionQueueResponse = {
  items: RevisionQueueItem[];
  total: number;
  limit: number;
  offset: number;
};

// --- M43: Personal AI Learning Companion ---
type CompanionAction = {
  action: "start_quiz" | "start_revision" | "start_session" | "create_path" | "accept_recommendation" | "dismiss_recommendation" | "none";
  description: string;
  payload: { [key: string]: any };
  requiresConfirmation: boolean;
};
type CompanionMessage = {
  messageId: string;
  conversationId: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  status: "ok" | "insufficient_evidence" | "model_unavailable" | "error";
  createdAt: string;
};
type CompanionConversation = {
  conversationId: string;
  userId: string;
  title: string;
  createdAt: string;
  updatedAt: string;
};
type CompanionConversationListResponse = {
  conversations: CompanionConversation[];
  total: number;
  limit: number;
  offset: number;
};
type CompanionConversationCreateRequest = {
  title?: string;
};
type CompanionMessageRequest = {
  message: string;
  conversationId?: string;
  mode?: "ask" | "quiz" | "revision" | "progress" | "recommend" | "manage";
  documentIds?: string[];
  limit?: number;
};
type CompanionMessageResponse = {
  messageId: string;
  conversationId: string;
  role: "assistant";
  content: string;
  status: "answered" | "insufficient_evidence" | "model_unavailable" | "error";
  citations: Citation[];
  retrievalMode?: string;
  fallbackReason?: string | null;
  action: CompanionAction | null;
  requiresConfirmation: boolean;
  createdAt: string;
};
type CompanionMessagesResponse = {
  messages: CompanionMessage[];
  total: number;
  limit: number;
  offset: number;
};


// --- M45: AI Observability & Model Evaluation Dashboard ---
type ModelLatencySummary = {
  count: number;
  p50Ms: number;
  p95Ms: number;
  maxMs: number;
};
type ModelMetricsSnapshot = {
  taskCounts: { [key: string]: number };
  outcomeCounts: { [key: string]: number };
  providerCounts: { [key: string]: number };
  totalObservations: number;
  successRate: number | null;
  failureRate: number | null;
  fallbackCount: number;
  fallbackRate: number | null;
  latencyMs: ModelLatencySummary;
  recentFailures: Array<{
    timestamp: string;
    taskType: string;
    modelId: string;
    outcome: string;
    latencyMs: number;
    fallbackOccurred: boolean;
  }>;
};
type ModelEvaluationRequest = {
  taskType: string;
  dataset: string;
  metric: string;
};
type ModelEvaluationResult = {
  taskType: string;
  dataset: string;
  metric: string;
  value: number | { [key: string]: any } | null;
  details: { [key: string]: any } | null;
  evaluatedAt: string | null;
};


async function fetchModelObservabilitySnapshot(): Promise<ModelMetricsSnapshot> {
  const response = await fetch("/api/v1/admin/models/observability/snapshot", {
    method: "GET",
    headers: { "Content-Type": "application/json" },
  });
  return readJson<ModelMetricsSnapshot>(response);
}

async function runModelEvaluation(payload: ModelEvaluationRequest): Promise<ModelEvaluationResult> {
  const response = await fetch("/api/v1/admin/models/evaluate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return readJson<ModelEvaluationResult>(response);
}


type Workspace = {
  spaceId: string;
  ownerId: string;
  title: string;
  description: string;
  learningObjective: string | null;
  status: "active" | "archived";
  memberCount: number;
  createdAt: string;
  updatedAt: string;
  userRole: "owner" | "member";
};
type WorkspaceMember = {
  userId: string;
  username: string | null;
  role: "owner" | "member";
  joinedAt: string;
};
type WorkspaceDocument = {
  documentId: string;
  displayFilename: string;
  sha256: string;
  byteSize: number;
  pageCount: number;
  chunkCount: number;
  createdAt: string;
  parserVersion: string;
  sharedAt: string;
  sharedBy: string;
  sharedByUsername: string | null;
};
type WorkspaceQuiz = {
  quizId: string;
  topicId: string;
  topic: string;
  createdAt: string;
  sharedAt: string;
  sharedBy: string;
  sharedByUsername: string | null;
};
type WorkspaceDiscussion = {
  discussionId: string;
  spaceId: string;
  userId: string;
  username: string | null;
  content: string;
  createdAt: string;
};
type WorkspaceDiscussionReply = {
  replyId: string;
  discussionId: string;
  userId: string;
  username: string | null;
  content: string;
  createdAt: string;
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
  eventName:
    | "document_upload_completed"
    | "document_upload_failed"
    | "image_upload_completed"
    | "image_upload_failed"
    | "tutor_request_completed"
    | "tutor_request_abstained"
    | "tutor_request_failed"
    | "quiz_started"
    | "quiz_completed"
    | "quiz_failed"
    | "study_session_completed"
    | "study_session_failed"
    | "study_coach_recommendation_accepted"
    | "study_coach_recommendation_dismissed",
  feature: "documents" | "images" | "tutor" | "quizzes" | "study_sessions" | "study_coach",
  result: "success" | "failure" | "abstained" | "accepted" | "dismissed" | "started" | "completed",
) {
  try {
    await fetch("/api/v1/product-analytics/events", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ eventName, feature, result }),
    });
  } catch {
    // Intentionally non-blocking: product analytics must never disrupt user workflows.
  }
}

type SessionUser = { userId: string; username: string; expiresAt: string; isAdmin: boolean };

let csrfToken: string | null = null;
let currentUsername = "";

const fetch: typeof globalThis.fetch = async (input, init = {}, opts: { username?: string } = {}) => {
  const username: string | undefined = opts.username || currentUsername;
  const method = (init.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
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

  return fetchWithOffline(input, { ...init, headers, credentials: "same-origin" }, {
    username,
    fetchFn: (input: RequestInfo | URL, init?: RequestInit) => globalThis.fetch(input, { ...init, credentials: "same-origin", headers }),
  });
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

  currentUsername = session?.username ?? "";

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
  if (session) return <LearningApp username={session.username} isAdmin={session.isAdmin} onLogout={() => void logout()} />;

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

function LearningApp({ username, isAdmin, onLogout }: { username: string; isAdmin: boolean; onLogout: () => void }) {
  currentUsername = username;
  const [diagram, setDiagram] = useState<Diagram | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [selectedRegion, setSelectedRegion] = useState<DiagramRegion | null>(null);
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [loading, setLoading] = useState(false);
  const [documents, setDocuments] = useState<IndexedDocument[]>([]);
  const [selectedDocumentId, setSelectedDocumentId] = useState("");
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  // --- M35: Offline state ---
  const [offline, setOffline] = useState(isOffline());
  const [syncing, setSyncing] = useState(false);
  const [syncStatus, setSyncStatus] = useState<{ synced: number; failed: number } | null>(null);

  useEffect(() => {
    const unsubscribe = onOnlineChange((isOnline: boolean) => {
      setOffline(!isOnline);
      if (isOnline) {
        flushQueue().then((result) => {
          setSyncStatus(result as unknown as { synced: number; failed: number });
        });
      }
    });
    return () => unsubscribe();
  }, []);

  const flushQueue = async () => {
    setSyncing(true);
    try {
      const result = await flushQueue();
      setSyncStatus(result as unknown as { synced: number; failed: number });
    } finally {
      setSyncing(false);
    }
  };
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
  const [productAnalyticsError, setProductAnalyticsError] = useState<string | null>(null);
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

  // --- M36: Shared study spaces ---
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspacesLoading, setWorkspacesLoading] = useState(false);
  const [workspacesError, setWorkspacesError] = useState<string | null>(null);
  const [selectedSpaceId, setSelectedSpaceId] = useState<string | null>(null);
  const [selectedSpace, setSelectedSpace] = useState<Workspace | null>(null);
  const [workspaceMembers, setWorkspaceMembers] = useState<WorkspaceMember[]>([]);
  const [workspaceMembersLoading, setWorkspaceMembersLoading] = useState(false);
  const [workspaceDocuments, setWorkspaceDocuments] = useState<WorkspaceDocument[]>([]);
  const [workspaceQuizzes, setWorkspaceQuizzes] = useState<WorkspaceQuiz[]>([]);
  const [workspaceDiscussions, setWorkspaceDiscussions] = useState<WorkspaceDiscussion[]>([]);
  const [workspaceDiscussionReplies, setWorkspaceDiscussionReplies] = useState<WorkspaceDiscussionReply[]>([]);
  const [selectedDiscussionId, setSelectedDiscussionId] = useState<string | null>(null);
  const [newWorkspaceTitle, setNewWorkspaceTitle] = useState("");
  const [newWorkspaceDescription, setNewWorkspaceDescription] = useState("");
  const [newWorkspaceObjective, setNewWorkspaceObjective] = useState("");
  const [creatingWorkspace, setCreatingWorkspace] = useState(false);
  const [inviteUsername, setInviteUsername] = useState("");
  const [invitingMember, setInvitingMember] = useState(false);
  const [newDocumentShare, setNewDocumentShare] = useState("");
  const [newQuizShare, setNewQuizShare] = useState("");
  const [newDiscussionContent, setNewDiscussionContent] = useState("");
  const [newReplyContent, setNewReplyContent] = useState("");
  const [creatingDiscussion, setCreatingDiscussion] = useState(false);

  // --- M38: Content studio ---
  const [contentResources, setContentResources] = useState<ContentResource[]>([]);
  const [contentResourcesLoading, setContentResourcesLoading] = useState(false);
  const [contentResourcesError, setContentResourcesError] = useState<string | null>(null);
  const [selectedResourceId, setSelectedResourceId] = useState<string | null>(null);
  const [selectedResource, setSelectedResource] = useState<ContentResource | null>(null);
  const [newContentTitle, setNewContentTitle] = useState("");
  const [newContentDescription, setNewContentDescription] = useState("");
  const [newContentTopic, setNewContentTopic] = useState("");
  const [newContentFormat, setNewContentFormat] = useState<"notes" | "summary" | "flashcards" | "questions" | "revision_sheet" | "concept_outline">("notes");
  const [newContentDetail, setNewContentDetail] = useState<"brief" | "standard" | "comprehensive">("standard");
  const [creatingContent, setCreatingContent] = useState(false);

  // --- M39: Content validation ---
  const [contentValidation, setContentValidation] = useState<ContentResourceValidation | null>(null);
  const [contentValidationLoading, setContentValidationLoading] = useState(false);
  const [contentValidationError, setContentValidationError] = useState<string | null>(null);
  const [verifyingSectionId, setVerifyingSectionId] = useState<string | null>(null);
  const [regeneratingSectionId, setRegeneratingSectionId] = useState<string | null>(null);

  // --- M42: Revision queue ---  const [revisionQueue, setRevisionQueue] = useState<RevisionQueueItem[]>([]);  const [revisionTotal, setRevisionTotal] = useState(0);  const [revisionLoading, setRevisionLoading] = useState(false);  const [revisionError, setRevisionError] = useState<string | null>(null);

  // --- M43: Personal AI Learning Companion ---
  const [conversations, setConversations] = useState<CompanionConversation[]>([]);
  const [conversationTotal, setConversationTotal] = useState(0);
  const [conversationLoading, setConversationLoading] = useState(false);
  const [conversationError, setConversationError] = useState<string | null>(null);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<CompanionMessage[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [messagesError, setMessagesError] = useState<string | null>(null);
  const [composingMessage, setComposingMessage] = useState("");
  const [sendingMessage, setSendingMessage] = useState(false);
  const [messageError, setMessageError] = useState<string | null>(null);
  const [confirmAction, setConfirmAction] = useState<CompanionAction | null>(null);
  const [confirmPayload, setConfirmPayload] = useState<{ [key: string]: any }>({});

  // --- M45: AI Observability & Model Evaluation Dashboard state ---
  const [observabilitySnapshot, setObservabilitySnapshot] = useState<ModelMetricsSnapshot | null>(null);
  const [observabilityLoading, setObservabilityLoading] = useState(false);
  const [observabilityError, setObservabilityError] = useState<string | null>(null);
  const [evaluationResult, setEvaluationResult] = useState<ModelEvaluationResult | null>(null);
  const [evaluationLoading, setEvaluationLoading] = useState(false);
  const [evaluationError, setEvaluationError] = useState<string | null>(null);
  const [evalTaskType, setEvalTaskType] = useState("tutor");
  const [evalDataset, setEvalDataset] = useState("retrieval_eval");
  const [evalMetric, setEvalMetric] = useState("retrieval_quality");

  // --- M41: Unified search ---
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searchTotal, setSearchTotal] = useState(0);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [searchMode, setSearchMode] = useState<"lexical" | "semantic" | "hybrid" | null>(null);
  const [searchFallbackReason, setSearchFallbackReason] = useState<string | null>(null);
  const [searchByType, setSearchByType] = useState<Record<string, number>>({});

  // --- Private beta feedback state ---
  const [feedbackSubmitting, setFeedbackSubmitting] = useState(false);
  const [feedbackSubmitted, setFeedbackSubmitted] = useState(false);
  const [feedbackError, setFeedbackError] = useState<string | null>(null);
  const [feedbackCategory, setFeedbackCategory] = useState<FeedbackCategory>("bug");
  const [feedbackTitle, setFeedbackTitle] = useState("");
  const [feedbackDescription, setFeedbackDescription] = useState("");
  const [myFeedback, setMyFeedback] = useState<FeedbackItem[]>([]);
  const [myFeedbackLoading, setMyFeedbackLoading] = useState(false);
  const [editingFeedbackId, setEditingFeedbackId] = useState<string | null>(null);
  const [editFeedbackCategory, setEditFeedbackCategory] = useState<FeedbackCategory>("bug");
  const [editFeedbackTitle, setEditFeedbackTitle] = useState("");
  const [editFeedbackDescription, setEditFeedbackDescription] = useState("");
  const [adminFeedback, setAdminFeedback] = useState<FeedbackItem[]>([]);
  const [adminFeedbackLoading, setAdminFeedbackLoading] = useState(false);
  const [adminFeedbackError, setAdminFeedbackError] = useState<string | null>(null);

  // --- M32: Feedback analysis & improvement loop state ---
  const [feedbackFilterCategory, setFeedbackFilterCategory] = useState<FeedbackCategory | "">("");
  const [feedbackFilterStatus, setFeedbackFilterStatus] = useState<FeedbackStatus | "">("");
  const [feedbackSortBy, setFeedbackSortBy] = useState<"recency" | "created">("recency");
  const [feedbackSortOrder, setFeedbackSortOrder] = useState<"asc" | "desc">("desc");
  const [feedbackDateFrom, setFeedbackDateFrom] = useState("");
  const [feedbackDateTo, setFeedbackDateTo] = useState("");

  const [improvementTasks, setImprovementTasks] = useState<ImprovementTask[]>([]);
  const [improvementTasksLoading, setImprovementTasksLoading] = useState(false);
  const [improvementTasksError, setImprovementTasksError] = useState<string | null>(null);
  const [creatingTask, setCreatingTask] = useState(false);
  const [taskForm, setTaskForm] = useState<{
    title: string;
    description: string;
    status: "proposed" | "in_progress" | "resolved" | "declined";
    severity: number;
    impact: number;
    reproducibility: number;
    workflowFrequency: number;
    implementationEffort: number;
    regressionTest: string;
    feedbackIds: string;
  }>({
    title: "",
    description: "",
    status: "proposed",
    severity: 3,
    impact: 3,
    reproducibility: 3,
    workflowFrequency: 3,
    implementationEffort: 3,
    regressionTest: "",
    feedbackIds: "",
  });
  const [editingTaskId, setEditingTaskId] = useState<string | null>(null);
  const [linkTaskFeedbackId, setLinkTaskFeedbackId] = useState<string | null>(null);
  const [selectedLinkTaskId, setSelectedLinkTaskId] = useState<string | null>(null);

  // --- M33: Onboarding state ---
  const [onboardingStep, setOnboardingStep] = useState<number>(0);
  const [onboardingCompleted, setOnboardingCompleted] = useState<boolean>(false);
  const [onboardingSkipped, setOnboardingSkipped] = useState<boolean>(false);

  const ONBOARDING_STEPS = [
    {
      title: "What WonderLearn does",
      body:
        "WonderLearn is a local-first visual learning assistant. You upload PDFs, choose a concept from a curated diagram, and find evidence in your own materials. Answers always cite the exact passage they came from — nothing is invented.",
    },
    {
      title: "Your data stays on this device",
      body:
        "PDFs, images, and learning records are stored in a local SQLite database on your device. Camera frames are never saved. No documents, tutor conversations, or camera frames are sent to external services. Feedback and analytics are private to your account and administrators.",
    },
    {
      title: "Upload your first document",
      body:
        "Upload a PDF through 'Add a learning PDF.' Once indexed, you can choose a diagram region to find supporting passages in your material.",
    },
    {
      title: "Ask a grounded question",
      body:
        "After uploading, ask a question about your materials. Answers are built only from retrieved evidence, with citations showing the source document, page, and exact passage.",
    },
    {
      title: "Try a quiz and plan study",
      body:
        "Generate an evidence-backed quiz from a topic, record your answers, and start a study session. Local analytics track your progress and schedule reviews.",
    },
  ];
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
      .then((result) => {
        if (active) {
          setProductAnalytics(result);
          setProductAnalyticsError(null);
        }
      })
      .catch((error: unknown) => {
        if (active) {
          setProductAnalytics(null);
          setProductAnalyticsError(error instanceof Error ? error.message : "Could not load product analytics.");
        }
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
    void loadMyFeedback();
    void loadWorkspaces();
    void loadContentResources();
    void loadConversations();
    if (isAdmin) {
      void loadAdminFeedback();
      void loadImprovementTasks();
      void refreshObservability();
    }
    // Load onboarding state for the current user from localStorage.
    if (username) {
      try {
        const stored = localStorage.getItem(`wl-onboarding-${username}-v1`);
        if (stored) {
          const parsed = JSON.parse(stored);
          setOnboardingCompleted(parsed.completed ?? false);
          setOnboardingSkipped(parsed.skipped ?? false);
          setOnboardingStep(parsed.step ?? 0);
        }
      } catch {
        // Ignore localStorage parse errors; onboarding will show by default.
      }
    }
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
      void recordProductAnalyticsEvent("document_upload_completed", "documents", "success");
    } catch (error) {
      void recordProductAnalyticsEvent("document_upload_failed", "documents", "failure");
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
      void recordProductAnalyticsEvent("image_upload_completed", "images", "success");
    } catch (error) {
      void recordProductAnalyticsEvent("image_upload_failed", "images", "failure");
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
      const result = await readJson<ImageTutorResult>(response);
      setImageTutorResult(result);
      void recordProductAnalyticsEvent(
        result.status === "answered" ? "tutor_request_completed" : "tutor_request_abstained",
        "tutor",
        result.status === "answered" ? "success" : "abstained",
      );
      await loadLearningImage(selectedLearningImage.imageId);
      setSelectedImageRegionId(selectedImageRegionId);
    } catch (error) {
      void recordProductAnalyticsEvent("tutor_request_failed", "tutor", "failure");
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
      const result = await readJson<TutorResult>(response);
      setTutorResult(result);
      void recordProductAnalyticsEvent(
        result.status === "answered" ? "tutor_request_completed" : "tutor_request_abstained",
        "tutor",
        result.status === "answered" ? "success" : "abstained",
      );
    } catch (error) {
      void recordProductAnalyticsEvent("tutor_request_failed", "tutor", "failure");
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
      if (action === "accept") {
        void recordProductAnalyticsEvent(
          "study_coach_recommendation_accepted",
          "study_coach",
          "accepted",
        );
      } else if (action === "dismiss") {
        void recordProductAnalyticsEvent(
          "study_coach_recommendation_dismissed",
          "study_coach",
          "dismissed",
        );
      }
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

  // --- Private beta feedback ---
  async function submitFeedback(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFeedbackSubmitting(true);
    setFeedbackError(null);
    setFeedbackSubmitted(false);
    try {
      const response = await fetch("/api/v1/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category: feedbackCategory, title: feedbackTitle.trim(), description: feedbackDescription.trim() }),
      });
      await readJson<{ feedback: FeedbackItem; message: string }>(response);
      setFeedbackSubmitted(true);
      setFeedbackTitle("");
      setFeedbackDescription("");
      await loadMyFeedback();
    } catch (error) {
      setFeedbackError(error instanceof Error ? error.message : "Could not send your feedback.");
    } finally { setFeedbackSubmitting(false); }
  }

  async function loadMyFeedback() {
    setMyFeedbackLoading(true);
    setFeedbackError(null);
    try {
      const response = await fetch("/api/v1/feedback");
      const result = await readJson<{ feedback: FeedbackItem[] }>(response);
      setMyFeedback(result.feedback);
    } catch (error) {
      setFeedbackError(error instanceof Error ? error.message : "Could not load your feedback.");
    } finally { setMyFeedbackLoading(false); }
  }

  // --- M36: Shared study spaces ---
  async function loadWorkspaces() {
    setWorkspacesLoading(true);
    setWorkspacesError(null);
    try {
      const response = await fetch("/api/v1/workspaces");
      const result = await readJson<{ spaces: Workspace[] }>(response);
      setWorkspaces(result.spaces);
    } catch (error) {
      setWorkspacesError(error instanceof Error ? error.message : "Could not load workspaces.");
    } finally { setWorkspacesLoading(false); }
  }

  async function loadWorkspace(spaceId: string) {
    try {
      const response = await fetch(`/api/v1/workspaces/${encodeURIComponent(spaceId)}`);
      const result = await readJson<Workspace>(response);
      setSelectedSpace(result);
      setSelectedSpaceId(spaceId);
      await loadWorkspaceMembers(spaceId);
      await loadWorkspaceDocuments(spaceId);
      await loadWorkspaceQuizzes(spaceId);
      await loadWorkspaceDiscussions(spaceId);
    } catch (error) {
      setWorkspacesError(error instanceof Error ? error.message : "Could not load workspace.");
    }
  }

  async function loadWorkspaceMembers(spaceId: string) {
    setWorkspaceMembersLoading(true);
    try {
      const response = await fetch(`/api/v1/workspaces/${encodeURIComponent(spaceId)}/members`);
      const result = await readJson<WorkspaceMember[]>(response);
      setWorkspaceMembers(result);
    } catch (error) {
      setWorkspacesError(error instanceof Error ? error.message : "Could not load members.");
    } finally { setWorkspaceMembersLoading(false); }
  }

  async function loadWorkspaceDocuments(spaceId: string) {
    try {
      const response = await fetch(`/api/v1/workspaces/${encodeURIComponent(spaceId)}/documents`);
      const result = await readJson<{ documents: WorkspaceDocument[] }>(response);
      setWorkspaceDocuments(result.documents);
    } catch (error) {
      setWorkspacesError(error instanceof Error ? error.message : "Could not load shared documents.");
    }
  }

  async function loadWorkspaceQuizzes(spaceId: string) {
    try {
      const response = await fetch(`/api/v1/workspaces/${encodeURIComponent(spaceId)}/quizzes`);
      const result = await readJson<{ quizzes: WorkspaceQuiz[] }>(response);
      setWorkspaceQuizzes(result.quizzes);
    } catch (error) {
      setWorkspacesError(error instanceof Error ? error.message : "Could not load shared quizzes.");
    }
  }

  async function loadWorkspaceDiscussions(spaceId: string) {
    try {
      const response = await fetch(`/api/v1/workspaces/${encodeURIComponent(spaceId)}/discussions`);
      const result = await readJson<{ discussions: WorkspaceDiscussion[] }>(response);
      setWorkspaceDiscussions(result.discussions);
    } catch (error) {
      setWorkspacesError(error instanceof Error ? error.message : "Could not load discussions.");
    }
  }

  async function loadWorkspaceDiscussionReplies(discussionId: string) {
    if (!selectedSpaceId) return;
    try {
      const response = await fetch(
        `/api/v1/workspaces/${encodeURIComponent(selectedSpaceId)}/discussions/${encodeURIComponent(discussionId)}/replies`,
      );
      const result = await readJson<{ replies: WorkspaceDiscussionReply[] }>(response);
      setWorkspaceDiscussionReplies(result.replies);
      setSelectedDiscussionId(discussionId);
    } catch (error) {
      setWorkspacesError(error instanceof Error ? error.message : "Could not load replies.");
    }
  }

  async function createWorkspace(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCreatingWorkspace(true);
    setWorkspacesError(null);
    try {
      const response = await fetch("/api/v1/workspaces", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: newWorkspaceTitle.trim(),
          description: newWorkspaceDescription.trim(),
          ...(newWorkspaceObjective.trim() ? { learningObjective: newWorkspaceObjective.trim() } : {}),
        }),
      });
      const result = await readJson<Workspace>(response);
      setWorkspaces((current) => [result, ...current]);
      setNewWorkspaceTitle("");
      setNewWorkspaceDescription("");
      setNewWorkspaceObjective("");
      await loadWorkspaces();
    } catch (error) {
      setWorkspacesError(error instanceof Error ? error.message : "Could not create workspace.");
    } finally { setCreatingWorkspace(false); }
  }

  async function updateWorkspace(spaceId: string, event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setWorkspacesError(null);
    const form = new FormData(event.currentTarget);
    const title = String(form.get("title")).trim();
    const description = String(form.get("description")).trim();
    const learningObjective = String(form.get("learningObjective")).trim();
    const status = String(form.get("status"));
    const updates: Record<string, any> = {};
    if (title) updates.title = title;
    if (description) updates.description = description;
    if (learningObjective) updates.learningObjective = learningObjective;
    if (status) updates.status = status;
    try {
      const response = await fetch(`/api/v1/workspaces/${encodeURIComponent(spaceId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updates),
      });
      const result = await readJson<Workspace>(response);
      setWorkspaces((current) => current.map((item) => (item.spaceId === result.spaceId ? result : item)));
      if (selectedSpaceId === spaceId) setSelectedSpace(result);
      await loadWorkspaces();
    } catch (error) {
      setWorkspacesError(error instanceof Error ? error.message : "Could not update workspace.");
    }
  }

  async function archiveWorkspace(spaceId: string) {
    try {
      const response = await fetch(`/api/v1/workspaces/${encodeURIComponent(spaceId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "archived" }),
      });
      await readJson<Workspace>(response);
      setWorkspaces((current) => current.map((item) => (item.spaceId === spaceId ? { ...item, status: "archived" } : item)));
      if (selectedSpaceId === spaceId) {
        setSelectedSpace(null);
        setSelectedSpaceId(null);
      }
      await loadWorkspaces();
    } catch (error) {
      setWorkspacesError(error instanceof Error ? error.message : "Could not archive workspace.");
    }
  }

  async function deleteWorkspace(spaceId: string) {
    if (!confirm("Deleting a workspace removes all shared memberships, documents, quizzes, and discussions. Continue?")) return;
    try {
      await fetch(`/api/v1/workspaces/${encodeURIComponent(spaceId)}`, { method: "DELETE" });
      setWorkspaces((current) => current.filter((item) => item.spaceId !== spaceId));
      if (selectedSpaceId === spaceId) {
        setSelectedSpace(null);
        setSelectedSpaceId(null);
      }
      await loadWorkspaces();
    } catch (error) {
      setWorkspacesError(error instanceof Error ? error.message : "Could not delete workspace.");
    }
  }

  async function inviteMember(spaceId: string) {
    setInvitingMember(true);
    setWorkspacesError(null);
    try {
      const response = await fetch(`/api/v1/workspaces/${encodeURIComponent(spaceId)}/members`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: inviteUsername.trim() }),
      });
      await readJson<WorkspaceMember>(response);
      setInviteUsername("");
      await loadWorkspaceMembers(spaceId);
      await loadWorkspaces();
    } catch (error) {
      setWorkspacesError(error instanceof Error ? error.message : "Could not invite member.");
    } finally { setInvitingMember(false); }
  }

  async function removeMember(spaceId: string, userId: string) {
    if (!confirm("Remove this member from the workspace?")) return;
    try {
      await fetch(`/api/v1/workspaces/${encodeURIComponent(spaceId)}/members/${encodeURIComponent(userId)}`, { method: "DELETE" });
      await loadWorkspaceMembers(spaceId);
      await loadWorkspaces();
    } catch (error) {
      setWorkspacesError(error instanceof Error ? error.message : "Could not remove member.");
    }
  }

  async function shareDocument(spaceId: string) {
    setWorkspacesError(null);
    try {
      const response = await fetch(`/api/v1/workspaces/${encodeURIComponent(spaceId)}/documents`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ documentId: newDocumentShare.trim() }),
      });
      await readJson<WorkspaceDocument>(response);
      setNewDocumentShare("");
      await loadWorkspaceDocuments(spaceId);
    } catch (error) {
      setWorkspacesError(error instanceof Error ? error.message : "Could not share document.");
    }
  }

  async function unshareDocument(spaceId: string, documentId: string) {
    try {
      await fetch(`/api/v1/workspaces/${encodeURIComponent(spaceId)}/documents/${encodeURIComponent(documentId)}`, { method: "DELETE" });
      await loadWorkspaceDocuments(spaceId);
    } catch (error) {
      setWorkspacesError(error instanceof Error ? error.message : "Could not unshare document.");
    }
  }

  async function shareQuiz(spaceId: string) {
    setWorkspacesError(null);
    try {
      const response = await fetch(`/api/v1/workspaces/${encodeURIComponent(spaceId)}/quizzes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ quizId: newQuizShare.trim() }),
      });
      await readJson<WorkspaceQuiz>(response);
      setNewQuizShare("");
      await loadWorkspaceQuizzes(spaceId);
    } catch (error) {
      setWorkspacesError(error instanceof Error ? error.message : "Could not share quiz.");
    }
  }

  async function unshareQuiz(spaceId: string, quizId: string) {
    try {
      await fetch(`/api/v1/workspaces/${encodeURIComponent(spaceId)}/quizzes/${encodeURIComponent(quizId)}`, { method: "DELETE" });
      await loadWorkspaceQuizzes(spaceId);
    } catch (error) {
      setWorkspacesError(error instanceof Error ? error.message : "Could not unshare quiz.");
    }
  }

  async function createDiscussion(spaceId: string, event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCreatingDiscussion(true);
    setWorkspacesError(null);
    try {
      const response = await fetch(`/api/v1/workspaces/${encodeURIComponent(spaceId)}/discussions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: newDiscussionContent.trim() }),
      });
      await readJson<WorkspaceDiscussion>(response);
      setNewDiscussionContent("");
      await loadWorkspaceDiscussions(spaceId);
    } catch (error) {
      setWorkspacesError(error instanceof Error ? error.message : "Could not create discussion.");
    } finally { setCreatingDiscussion(false); }
  }

  async function createReply(spaceId: string, discussionId: string, event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setWorkspacesError(null);
    try {
      const response = await fetch(`/api/v1/workspaces/${encodeURIComponent(spaceId)}/discussions/${encodeURIComponent(discussionId)}/replies`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: newReplyContent.trim() }),
      });
      await readJson<WorkspaceDiscussionReply>(response);
      setNewReplyContent("");
      await loadWorkspaceDiscussionReplies(discussionId);
    } catch (error) {
      setWorkspacesError(error instanceof Error ? error.message : "Could not post reply.");
    }
  }

  // --- M38: Content studio API ---
  async function loadContentResources() {
    setContentResourcesLoading(true);
    setContentResourcesError(null);
    try {
      const response = await fetch("/api/v1/content/resources");
      const result = await readJson<{ resources: ContentResource[] }>(response);
      setContentResources(result.resources);
    } catch (error) {
      setContentResourcesError(error instanceof Error ? error.message : "Could not load content resources.");
    } finally { setContentResourcesLoading(false); }
  }

  async function loadContentResource(resourceId: string) {
    try {
      const response = await fetch(`/api/v1/content/resources/${encodeURIComponent(resourceId)}`);
      const result = await readJson<ContentResource>(response);
      setSelectedResource(result);
      setSelectedResourceId(resourceId);
    } catch (error) {
      setContentResourcesError(error instanceof Error ? error.message : "Could not load content resource.");
    }
  }

  async function createContentResource(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCreatingContent(true);
    setContentResourcesError(null);
    try {
      const payload: ContentResourceCreateRequest = {
        title: newContentTitle.trim(),
        description: newContentDescription.trim() || undefined,
        format: newContentFormat,
        detailLevel: newContentDetail,
        topic: newContentTopic.trim(),
      };
      const response = await fetch("/api/v1/content/resources", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await readJson<ContentResource>(response);
      setContentResources((current) => [result, ...current]);
      setNewContentTitle("");
      setNewContentDescription("");
      setNewContentTopic("");
      setSelectedResource(result);
      setSelectedResourceId(result.resourceId);
    } catch (error) {
      setContentResourcesError(error instanceof Error ? error.message : "Could not create content resource.");
    } finally { setCreatingContent(false); }
  }

  async function updateContentResourceMetadata(resourceId: string, event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setContentResourcesError(null);
    const form = new FormData(event.currentTarget);
    const title = String(form.get("title")).trim();
    const description = String(form.get("description")).trim();
    const status = String(form.get("status"));
    const updates: Record<string, any> = {};
    if (title) updates.title = title;
    if (description) updates.description = description;
    if (status) updates.status = status;
    try {
      const response = await fetch(`/api/v1/content/resources/${encodeURIComponent(resourceId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updates),
      });
      const result = await readJson<ContentResource>(response);
      setContentResources((current) => current.map((item) => item.resourceId === result.resourceId ? result : item));
      if (selectedResourceId === resourceId) setSelectedResource(result);
    } catch (error) {
      setContentResourcesError(error instanceof Error ? error.message : "Could not update content resource.");
    }
  }

  async function deleteContentResource(resourceId: string) {
    if (!confirm("Delete this content resource? This cannot be undone.")) return;
    try {
      await fetch(`/api/v1/content/resources/${encodeURIComponent(resourceId)}`, { method: "DELETE" });
      setContentResources((current) => current.filter((item) => item.resourceId !== resourceId));
      if (selectedResourceId === resourceId) {
        setSelectedResource(null);
        setSelectedResourceId(null);
        setContentValidation(null);
      }
    } catch (error) {
      setContentResourcesError(error instanceof Error ? error.message : "Could not delete content resource.");
    }
  }

  // --- M39: Content validation ---
  async function validateContentResource(resourceId: string) {
    setContentValidationLoading(true);
    setContentValidationError(null);
    setContentValidation(null);
    try {
      const response = await fetch(`/api/v1/content/resources/${encodeURIComponent(resourceId)}/validate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      const result = await readJson<ContentResourceValidation>(response);
      setContentValidation(result);
      // Refresh the resource to get updated verification statuses.
      await loadContentResource(resourceId);
    } catch (error) {
      setContentValidationError(error instanceof Error ? error.message : "Could not validate this content resource.");
    } finally { setContentValidationLoading(false); }
  }

  async function verifyContentSection(resourceId: string, sectionId: string, status: "verified" | "needs_review" | "insufficient_evidence", notes?: string) {
    setVerifyingSectionId(sectionId);
    try {
      const response = await fetch(`/api/v1/content/resources/${encodeURIComponent(resourceId)}/sections/${encodeURIComponent(sectionId)}/verify`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ verificationStatus: status, verificationNotes: notes || undefined }),
      });
      const result = await readJson<any>(response);
      // Update the section in the selected resource.
      if (selectedResource) {
        setSelectedResource({
          ...selectedResource,
          sections: selectedResource.sections.map((section) =>
            section.sectionId === sectionId
              ? {
                  ...section,
                  verificationStatus: result.verificationStatus,
                  verificationMethod: result.verificationMethod,
                  verificationDetails: result.verificationDetails,
                  verifiedAt: result.verifiedAt,
                  verifiedBy: result.verifiedBy,
                }
              : section
          ),
        });
      }
      // Refresh validation to reflect updated statuses.
      if (contentValidation?.resourceId === resourceId) {
        await validateContentResource(resourceId);
      }
    } catch (error) {
      setContentValidationError(error instanceof Error ? error.message : "Could not record the review decision.");
    } finally { setVerifyingSectionId(null); }
  }

  async function regenerateContentSection(resourceId: string, sectionId: string, content: string, citations: any[], sectionType?: string, sectionTitle?: string) {
    setRegeneratingSectionId(sectionId);
    try {
      const response = await fetch(`/api/v1/content/resources/${encodeURIComponent(resourceId)}/sections/${encodeURIComponent(sectionId)}/regenerate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, citations, ...(sectionType ? { sectionType } : {}), ...(sectionTitle ? { sectionTitle } : {}) }),
      });
      const result = await readJson<any>(response);
      if (selectedResource) {
        setSelectedResource({
          ...selectedResource,
          sections: selectedResource.sections.map((section) =>
            section.sectionId === sectionId ? { ...result } : section
          ),
        });
      }
      setContentValidation(null);
    } catch (error) {
      setContentValidationError(error instanceof Error ? error.message : "Could not regenerate this section.");
    } finally { setRegeneratingSectionId(null); }
  }

  async function linkContentResourceToQuiz(resourceId: string, quizId: string) {
    try {
      const response = await fetch(`/api/v1/content/resources/${encodeURIComponent(resourceId)}/quizzes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ quizId }),
      });
      await readJson<any>(response);
      await loadContentResource(resourceId);
      await loadContentResources();
    } catch (error) {
      setContentValidationError(error instanceof Error ? error.message : "Could not link this resource to a quiz.");
    }
  }
  // --- M42: Revision queue API ---  async function runRevisionQueue(limit: number = 20, offset: number = 0) {    setRevisionLoading(true);    setRevisionError(null);    try {      const params = new URLSearchParams();      params.set("limit", String(limit));      params.set("offset", String(offset));      const response = await fetch(`/api/v1/revision/queue?${params.toString()}`, {        method: "GET",        headers: { "Content-Type": "application/json" },      });      const result = await readJson<RevisionQueueResponse>(response);      setRevisionQueue(result.items);      setRevisionTotal(result.total);    } catch (error) {      setRevisionError(error instanceof Error ? error.message : "Could not load revision queue.");      setRevisionQueue([]);      setRevisionTotal(0);    } finally { setRevisionLoading(false); }  }  async function submitSectionReview(itemType: "flashcard" | "quiz", itemId: string, result: "remembered" | "partially" | "forgotten", confidenceRating: number | null = null) {    setRevisionError(null);    try {      const response = await fetch(`/api/v1/revision/review/${item_type}/${encodeURIComponent(itemId)}`, {        method: "POST",        headers: { "Content-Type": "application/json" },        body: JSON.stringify({ result, confidenceRating }),      });      const updated = await readJson<any>(response);      // Refresh the revision queue to reflect updated due dates      await runRevisionQueue();      return updated;    } catch (error) {

  // --- M43: Personal AI Learning Companion API ---
  async function loadConversations(limit: number = 20, offset: number = 0) {
    setConversationLoading(true);
    setConversationError(null);
    try {
      const params = new URLSearchParams();
      params.set("limit", String(limit));
      params.set("offset", String(offset));
      const response = await fetch(`/api/v1/companion/conversations?${params.toString()}`, {
        method: "GET",
        headers: { "Content-Type": "application/json" },
      });
      const result = await readJson<CompanionConversationListResponse>(response);
      setConversations(result.conversations);
      setConversationTotal(result.total);
    } catch (error) {
      setConversationError(error instanceof Error ? error.message : "Could not load conversations.");
      setConversations([]);
      setConversationTotal(0);
    } finally { setConversationLoading(false); }
  }

  async function createConversation(title: string | null = null) {
    setConversationError(null);
    try {
      const response = await fetch("/api/v1/companion/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      });
      const conversation = await readJson<CompanionConversation>(response);
      setSelectedConversationId(conversation.conversationId);
      setMessages([]);
      await loadConversations();
      return conversation;
    } catch (error) {
      setConversationError(error instanceof Error ? error.message : "Could not create conversation.");
      throw error;
    }
  }

  async function deleteConversation(conversationId: string) {
    if (!window.confirm("Delete this conversation and all its messages? This cannot be undone.")) return;
    setConversationError(null);
    try {
      await fetch(`/api/v1/companion/conversations/${encodeURIComponent(conversationId)}`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
      });
      setConversations(prev => prev.filter(c => c.conversationId !== conversationId));
      setConversationTotal(Math.max(0, conversationTotal - 1));
      if (selectedConversationId === conversationId) {
        setSelectedConversationId(null);
        setMessages([]);
      }
    } catch (error) {
      setConversationError(error instanceof Error ? error.message : "Could not delete conversation.");
      throw error;
    }
  }

  async function loadMessages(conversationId: string, limit: number = 50, offset: number = 0) {
    setMessagesLoading(true);
    setMessagesError(null);
    try {
      const params = new URLSearchParams();
      params.set("limit", String(limit));
      params.set("offset", String(offset));
      const response = await fetch(`/api/v1/companion/conversations/${encodeURIComponent(conversationId)}/messages?${params.toString()}`, {
        method: "GET",
        headers: { "Content-Type": "application/json" },
      });
      const result = await readJson<CompanionMessagesResponse>(response);
      setMessages(result.messages);
    } catch (error) {
      setMessagesError(error instanceof Error ? error.message : "Could not load messages.");
      setMessages([]);
    } finally { setMessagesLoading(false); }
  }

  async function sendCompanionMessage(message: string, conversationId: string, mode: CompanionMessageRequest["mode"] | null = null, confidenceRating: number | null = null) {
    setMessageError(null);
    try {
      const response = await fetch("/api/v1/companion/message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, conversationId, mode }),
      });
      const result = await readJson<CompanionMessageResponse>(response);
      setMessages(prev => [...prev, {
        messageId: result.messageId,
        conversationId: result.conversationId,
        role: result.role,
        content: result.content,
        citations: result.citations,
        status: result.status,
        createdAt: result.createdAt,
      }]);
      if (result.action && result.action.requiresConfirmation) {
        setConfirmAction(result.action);
        setConfirmPayload(result.action.payload);
      }
      return result;
    } catch (error) {
      setMessageError(error instanceof Error ? error.message : "Could not send message.");
      throw error;
    }
  }

  async function confirmCompanionAction() {
    if (!confirmAction) return;
    setMessageError(null);
    try {
      const response = await fetch("/api/v1/companion/message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: `Confirm: ${confirmAction.description}`,
          conversationId: selectedConversationId,
          mode: confirmAction.action === "start_quiz" ? "quiz" : confirmAction.action === "start_revision" ? "revision" : "ask",
          ...confirmPayload,
        }),
      });
      const result = await readJson<CompanionMessageResponse>(response);
      setMessages(prev => [...prev, {
        messageId: result.messageId,
        conversationId: result.conversationId,
        role: result.role,
        content: result.content,
        citations: result.citations,
        status: result.status,
        createdAt: result.createdAt,
      }]);
      setConfirmAction(null);
      setConfirmPayload({});
      return result;
    } catch (error) {
      setMessageError(error instanceof Error ? error.message : "Could not confirm action.");
      throw error;
    } finally {
      setConfirmAction(null);
      setConfirmPayload({});
    }
  }

  // --- M41: Unified search ---
  async function runSearch(query: string, mode: "lexical" | "semantic" | "hybrid" | null = null) {
    if (!query.trim()) {
      setSearchResults([]);
      setSearchTotal(0);
      setSearchMode(null);
      setSearchFallbackReason(null);
      setSearchByType({});
      return;
    }
    setSearchLoading(true);
    setSearchError(null);
    setSearchQuery(query);
    try {
      const params = new URLSearchParams();
      params.set("query", query.trim());
      params.set("limit", "20");
      params.set("offset", "0");
      if (mode) params.set("mode", mode);
      const response = await fetch(`/api/v1/search?${params.toString()}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      const result = await readJson<any>(response);
      setSearchResults(result.results || []);
      setSearchTotal(result.total || 0);
      setSearchMode(result.retrievalMode || null);
      setSearchFallbackReason(result.fallbackReason || null);
      setSearchByType(result.by_type || {});
    } catch (error) {
      setSearchError(error instanceof Error ? error.message : "Search failed.");
      setSearchResults([]);
      setSearchTotal(0);
    } finally { setSearchLoading(false); }
  }

  async function updateMyFeedback(feedbackId: string, event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFeedbackError(null);
    try {
      const response = await fetch(`/api/v1/feedback/${encodeURIComponent(feedbackId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category: editFeedbackCategory, title: editFeedbackTitle.trim(), description: editFeedbackDescription.trim() }),
      });
      const result = await readJson<{ feedback: FeedbackItem }>(response);
      setMyFeedback((current) => current.map((item) => item.feedbackId === result.feedback.feedbackId ? result.feedback : item));
      setEditingFeedbackId(null);
    } catch (error) {
      setFeedbackError(error instanceof Error ? error.message : "Could not update your feedback.");
    }
  }

  async function withdrawFeedback(feedbackId: string) {
    setFeedbackError(null);
    try {
      const response = await fetch(`/api/v1/feedback/${encodeURIComponent(feedbackId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "withdrawn" }),
      });
      const result = await readJson<{ feedback: FeedbackItem }>(response);
      setMyFeedback((current) => current.map((item) => item.feedbackId === result.feedback.feedbackId ? result.feedback : item));
    } catch (error) {
      setFeedbackError(error instanceof Error ? error.message : "Could not withdraw your feedback.");
    }
  }

  async function loadAdminFeedback() {
    if (!isAdmin) return;
    setAdminFeedbackLoading(true);
    setAdminFeedbackError(null);
    try {
      const params = new URLSearchParams();
      if (feedbackFilterCategory) params.set("category", feedbackFilterCategory);
      if (feedbackFilterStatus) params.set("status", feedbackFilterStatus);
      if (feedbackDateFrom) params.set("dateFrom", feedbackDateFrom);
      if (feedbackDateTo) params.set("dateTo", feedbackDateTo);
      params.set("sortBy", feedbackSortBy);
      params.set("sortOrder", feedbackSortOrder);
      const query = params.toString();
      const response = await fetch(`/api/v1/feedback/admin${query ? `?${query}` : ""}`);
      const result = await readJson<{ feedback: FeedbackItem[] }>(response);
      setAdminFeedback(result.feedback);
    } catch (error) {
      setAdminFeedbackError(error instanceof Error ? error.message : "Could not load feedback for review.");
    } finally { setAdminFeedbackLoading(false); }
  }

  async function updateAdminFeedback(feedbackId: string, event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAdminFeedbackError(null);
    const form = new FormData(event.currentTarget);
    const resolutionNotes = String(form.get("resolutionNotes"));
    const status = String(form.get("status"));
    try {
      const response = await fetch(`/api/v1/feedback/admin/${encodeURIComponent(feedbackId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resolution_notes: resolutionNotes || undefined, status: status || undefined }),
      });
      const result = await readJson<{ feedback: FeedbackItem }>(response);
      setAdminFeedback((current) => current.map((item) => item.feedbackId === result.feedback.feedbackId ? result.feedback : item));
      setMyFeedback((current) => current.map((item) => item.feedbackId === result.feedback.feedbackId ? result.feedback : item));
    } catch (error) {
      setAdminFeedbackError(error instanceof Error ? error.message : "Could not update feedback.");
    }
  }

  async function updateAdminFeedbackStatus(feedbackId: string, status: FeedbackStatus) {
    setAdminFeedbackError(null);
    try {
      const response = await fetch(`/api/v1/feedback/${encodeURIComponent(feedbackId)}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      const result = await readJson<{ feedback: FeedbackItem }>(response);
      setAdminFeedback((current) => current.map((item) => item.feedbackId === result.feedback.feedbackId ? result.feedback : item));
    } catch (error) {
      setAdminFeedbackError(error instanceof Error ? error.message : "Could not update feedback status.");
    }
  }

  // --- M32: Improvement tasks ---
  async function loadImprovementTasks() {
    if (!isAdmin) return;
    setImprovementTasksLoading(true);
    setImprovementTasksError(null);
    try {
      const params = new URLSearchParams();
      params.set("sortBy", "priority");
      params.set("sortOrder", "desc");
      const response = await fetch(`/api/v1/feedback/admin/improvement-tasks?${params.toString()}`);
      const result = await readJson<{ tasks: ImprovementTask[] }>(response);
      setImprovementTasks(result.tasks);
    } catch (error) {
      setImprovementTasksError(error instanceof Error ? error.message : "Could not load improvement tasks.");
    } finally { setImprovementTasksLoading(false); }
  }

  async function refreshObservability() {
    if (!isAdmin) return;
    setObservabilityLoading(true);
    setObservabilityError(null);
    try {
      const snapshot = await fetchModelObservabilitySnapshot();
      setObservabilitySnapshot(snapshot);
    } catch (error) {
      setObservabilityError(error instanceof Error ? error.message : "Could not load model observability snapshot.");
    } finally { setObservabilityLoading(false); }
  }

  async function runEvaluation() {
    setEvaluationLoading(true);
    setEvaluationError(null);
    setEvaluationResult(null);
    try {
      const result = await runModelEvaluation({
        taskType: evalTaskType,
        dataset: evalDataset,
        metric: evalMetric,
      });
      setEvaluationResult(result);
    } catch (error) {
      setEvaluationError(error instanceof Error ? error.message : "Could not run model evaluation.");
    } finally { setEvaluationLoading(false); }
  }

  async function createImprovementTask(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setImprovementTasksError(null);
    setCreatingTask(true);
    try {
      const feedbackIds = taskForm.feedbackIds.split(",").map((id) => id.trim()).filter(Boolean);
      const response = await fetch("/api/v1/feedback/admin/improvement-tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: taskForm.title.trim(),
          description: taskForm.description.trim(),
          status: taskForm.status,
          severity: taskForm.severity,
          impact: taskForm.impact,
          reproducibility: taskForm.reproducibility,
          workflowFrequency: taskForm.workflowFrequency,
          implementationEffort: taskForm.implementationEffort,
          regressionTest: taskForm.regressionTest.trim() || undefined,
          feedbackIds: feedbackIds.length > 0 ? feedbackIds : undefined,
        }),
      });
      const result = await readJson<ImprovementTask>(response);
      setImprovementTasks((current) => [result, ...current]);
      setTaskForm({
        title: "", description: "", status: "proposed", severity: 3, impact: 3,
        reproducibility: 3, workflowFrequency: 3, implementationEffort: 3,
        regressionTest: "", feedbackIds: "",
      });
      await loadAdminFeedback();
    } catch (error) {
      setImprovementTasksError(error instanceof Error ? error.message : "Could not create improvement task.");
    } finally { setCreatingTask(false); }
  }

  async function updateImprovementTask(taskId: string, event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setImprovementTasksError(null);
    const form = new FormData(event.currentTarget);
    const updates: Record<string, any> = {};
    const title = String(form.get("title")).trim();
    const description = String(form.get("description")).trim();
    const status = String(form.get("status"));
    const severity = String(form.get("severity"));
    const impact = String(form.get("impact"));
    const reproducibility = String(form.get("reproducibility"));
    const workflowFrequency = String(form.get("workflowFrequency"));
    const implementationEffort = String(form.get("implementationEffort"));
    const regressionTest = String(form.get("regressionTest")).trim();
    const implemented = form.get("implemented") === "on";
    if (title) updates.title = title;
    if (description) updates.description = description;
    if (status) updates.status = status;
    if (severity) updates.severity = parseInt(severity, 10);
    if (impact) updates.impact = parseInt(impact, 10);
    if (reproducibility) updates.reproducibility = parseInt(reproducibility, 10);
    if (workflowFrequency) updates.workflowFrequency = parseInt(workflowFrequency, 10);
    if (implementationEffort) updates.implementationEffort = parseInt(implementationEffort, 10);
    if (regressionTest) updates.regressionTest = regressionTest;
    updates.implemented = implemented;
    try {
      const response = await fetch(`/api/v1/feedback/admin/improvement-tasks/${encodeURIComponent(taskId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updates),
      });
      const result = await readJson<ImprovementTask>(response);
      setImprovementTasks((current) => current.map((item) => item.taskId === result.taskId ? result : item));
      setEditingTaskId(null);
      await loadAdminFeedback();
    } catch (error) {
      setImprovementTasksError(error instanceof Error ? error.message : "Could not update improvement task.");
    }
  }

  async function linkFeedbackToTask(taskId: string, feedbackId: string) {
    setImprovementTasksError(null);
    try {
      const response = await fetch(`/api/v1/feedback/admin/improvement-tasks/${encodeURIComponent(taskId)}/feedback/${encodeURIComponent(feedbackId)}`, {
        method: "POST",
      });
      const result = await readJson<ImprovementTask>(response);
      setImprovementTasks((current) => current.map((item) => item.taskId === result.taskId ? result : item));
      setLinkTaskFeedbackId(null);
      await loadAdminFeedback();
    } catch (error) {
      setImprovementTasksError(error instanceof Error ? error.message : "Could not link feedback to task.");
    }
  }

  async function unlinkFeedbackFromTask(taskId: string, feedbackId: string) {
    setImprovementTasksError(null);
    try {
      const response = await fetch(`/api/v1/feedback/admin/improvement-tasks/${encodeURIComponent(taskId)}/feedback/${encodeURIComponent(feedbackId)}`, {
        method: "DELETE",
      });
      const result = await readJson<ImprovementTask>(response);
      setImprovementTasks((current) => current.map((item) => item.taskId === result.taskId ? result : item));
      await loadAdminFeedback();
    } catch (error) {
      setImprovementTasksError(error instanceof Error ? error.message : "Could not unlink feedback from task.");
    }
  }

  // --- M33: Onboarding helpers ---

  function saveOnboardingState() {
    if (!username) return;
    try {
      const state = {
        step: onboardingStep,
        completed: onboardingCompleted,
        skipped: onboardingSkipped,
      };
      localStorage.setItem(`wl-onboarding-${username}-v1`, JSON.stringify(state));
    } catch {
      // Ignore localStorage write errors.
    }
  }

  function computeChecklist() {
    const documentUploaded = (documents ?? []).length > 0;
    const questionsAnswered =
      (progress?.totalAttempts ?? 0) > 0 ||
      evidence?.status === "evidence_found" ||
      tutorResult?.status === "answered";
    const quizCompleted =
      quiz?.questions.some((question) => question.latestOutcome !== null) ?? false;
    const sessionCompleted = (analytics?.totals.completedStudySessions ?? 0) > 0;

    return [
      {
        id: "account",
        label: "Account ready",
        done: true,
        hint: "Signed in as " + username,
      },
      {
        id: "document",
        label: "First document uploaded and processed",
        done: documentUploaded,
        hint: documentUploaded ? "Indexed and ready for evidence lookup" : "Upload a PDF to get started",
      },
      {
        id: "evidence",
        label: "First grounded question answered",
        done: questionsAnswered,
        hint: questionsAnswered
          ? "Evidence-backed answer received"
          : "Ask a question about your uploaded materials",
      },
      {
        id: "quiz",
        label: "First quiz completed",
        done: quizCompleted,
        hint: quizCompleted ? "Quiz answers recorded" : "Generate and complete a quiz",
      },
      {
        id: "session",
        label: "First study session completed",
        done: sessionCompleted,
        hint: sessionCompleted ? "Study session recorded" : "Start and complete a study session",
      },
    ];
  }

  const samplePdfContent = `%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R>>endobj
4 0 obj<</Length 98>>stream
BT/F1 12 Tf 72 712 Td(Photosynthesis is the process by which green plants,Tj ET
BT/F1 12 Tf 72 700 Td(algae, and some bacteria convert light energy,Tj ET
BT/F1 12 Tf 72 688 Td(into chemical energy stored in glucose.)Tj ET
BT/F1 12 Tf 72 676 Td(Carbon dioxide and water are the inputs; oxygen,Tj ET
BT/F1 12 Tf 72 664 Td(is released as a byproduct.)Tj ET
endstream endobj
xref 0 5
0000000000 65535 f 
0000000010 00000 n 
0000000058 00000 n 
0000000113 00000 n 
0000000196 00000 n 
trailer<</Size 5/Root 1 0 R>>startxref 328 %%EOF`;

  function downloadSamplePdf() {
    const blob = new Blob([samplePdfContent], { type: "application/pdf" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "wonderlearn-sample-photosynthesis.pdf";
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
  }

  function nextOnboardingStep() {
    if (onboardingStep < ONBOARDING_STEPS.length - 1) {
      setOnboardingStep((current) => current + 1);
      saveOnboardingState();
    } else {
      setOnboardingCompleted(true);
      setOnboardingStep(ONBOARDING_STEPS.length - 1);
      saveOnboardingState();
    }
  }

  function skipOnboarding() {
    setOnboardingSkipped(true);
    setOnboardingCompleted(true);
    saveOnboardingState();
  }

  function restartOnboarding() {
    setOnboardingCompleted(false);
    setOnboardingSkipped(false);
    setOnboardingStep(0);
    saveOnboardingState();
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
      void recordProductAnalyticsEvent("study_session_completed", "study_sessions", "completed");
      await refreshAnalytics();
    } catch (error) {
      void recordProductAnalyticsEvent("study_session_failed", "study_sessions", "failure");
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
      const result = await readJson<QuizSet>(response);
      setQuiz(result);
      if (result.status === "ready") {
        void recordProductAnalyticsEvent("quiz_started", "quizzes", "started");
      }
    } catch (error) {
      void recordProductAnalyticsEvent("quiz_failed", "quizzes", "failure");
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
      const otherQuestionsComplete = question.latestOutcome === null && quiz.questions
        .filter((item) => item.questionId !== question.questionId)
        .every((item) => item.latestOutcome !== null);
      if (otherQuestionsComplete) {
        void recordProductAnalyticsEvent("quiz_completed", "quizzes", "completed");
      }
      await refreshProgress();
    } catch (error) {
      void recordProductAnalyticsEvent("quiz_failed", "quizzes", "failure");
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
          {(onboardingCompleted || onboardingSkipped) && (
            <button type="button" className="text-action" onClick={restartOnboarding}>
              Show onboarding again
            </button>
          )}
          <button type="button" onClick={onLogout}>Sign out</button>
        </div>
        <p className="eyebrow">WonderLearn · curated diagram</p>
        <h1>Explore a concept</h1>
        <p className="description">
          Choose a labeled region to find evidence in your indexed study materials. Select a region
          with a pointer or use Tab and Enter.
        </p>
      </header>

      {offline && (
        <section className="offline-banner" aria-labelledby="offline-title">
          <h2 id="offline-title">Offline</h2>
          <p>
            You are currently offline. Your study materials and cached responses
            are still available. Any new actions (quiz answers, study sessions,
            feedback) will be queued and synced automatically when you reconnect.
          </p>
          {syncing && <p className="muted">Syncing with server…</p>}
          {syncStatus && (
            <p className="muted">
              Synced {syncStatus.synced} item{syncStatus.synced !== 1 ? "s" : ""}
              {syncStatus.failed > 0 ? `, ${syncStatus.failed} failed` : ""}
            </p>
          )}
          <button type="button" className="camera-action camera-action--primary" onClick={flushQueue} disabled={syncing}>
            {syncing ? "Syncing…" : "Retry now"}
          </button>
        </section>
      )}

      {!onboardingCompleted && !onboardingSkipped && (
        <section className="onboarding" aria-labelledby="onboarding-title">
          <div className="onboarding-card">
            <div className="onboarding-heading">
              <p className="eyebrow">Welcome to WonderLearn</p>
              <h2 id="onboarding-title">Get started in {ONBOARDING_STEPS.length} steps</h2>
              <p className="muted">
                Step {onboardingStep + 1} of {ONBOARDING_STEPS.length}
              </p>
            </div>
            <div className="onboarding-step">
              <h3>{ONBOARDING_STEPS[onboardingStep].title}</h3>
              <p>{ONBOARDING_STEPS[onboardingStep].body}</p>
            </div>
            <div className="onboarding-actions">
              {onboardingStep > 0 && (
                <button type="button" className="camera-action" onClick={() => setOnboardingStep((current) => current - 1)}>
                  Back
                </button>
              )}
              {onboardingStep < ONBOARDING_STEPS.length - 1 ? (
                <>
                  <button type="button" className="text-action" onClick={skipOnboarding}>
                    Skip tour
                  </button>
                  {onboardingStep === 2 && (
                    <button type="button" className="camera-action" onClick={downloadSamplePdf}>
                      Download sample PDF
                    </button>
                  )}
                  <button type="button" className="camera-action camera-action--primary" onClick={nextOnboardingStep}>
                    Next
                  </button>
                </>
              ) : (
                <>
                  <button type="button" className="text-action" onClick={skipOnboarding}>
                    Skip tour
                  </button>
                  <button type="button" className="camera-action camera-action--primary" onClick={nextOnboardingStep}>
                    Finish
                  </button>
                </>
              )}
            </div>
            <button type="button" className="camera-action camera-action--quiet" onClick={restartOnboarding}>
              Restart tour
            </button>
          </div>
        </section>
      )}

      <section className="onboarding-checklist" aria-labelledby="checklist-title">
        <h2 id="checklist-title">Getting started checklist</h2>
        <p className="muted">
          Progress is derived from your actual saved records — nothing is marked complete unless
          you have done it.
        </p>
        <ul className="checklist" role="none">
          {computeChecklist().map((item) => (
            <li key={item.id} role="none" className={`checklist-item${item.done ? " checklist-item--done" : ""}`}>
              <span aria-hidden="true">{item.done ? "☑" : "☐"}</span>
              <span>
                <strong>{item.label}</strong>
                {item.done ? <span className="muted"> — {item.hint}</span> : <span className="muted"> — {item.hint}</span>}
              </span>
            </li>
          ))}
        </ul>
        {!onboardingCompleted && !onboardingSkipped && (
          <p className="notice" role="status">
            New to WonderLearn? The onboarding tour above walks you through each step. You can
            restart it anytime using "Restart tour."
          </p>
        )}
        {onboardingSkipped && (
          <p className="notice" role="status">
            You skipped the onboarding tour. Use "Restart tour" below to view it again.
          </p>
        )}
      </section>

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
      {(productAnalytics || productAnalyticsError) && (
        <section className="analytics-panel" aria-labelledby="product-analytics-title">
          <div className="analytics-heading">
            <div>
              <p className="eyebrow">Privacy-safe feature usage</p>
              <h2 id="product-analytics-title">Product analytics snapshot</h2>
            </div>
          </div>
          {productAnalyticsError && <p className="notice notice--error" role="alert">{productAnalyticsError}</p>}
          {productAnalytics && !productAnalytics.enabled && <p className="muted" role="status">Product analytics are disabled by configuration. No usage events are being recorded.</p>}
          {productAnalytics?.enabled && productAnalytics.totals.eventCount === 0 && <p className="muted" role="status">No product activity is recorded in the last {productAnalytics.windowDays} days.</p>}
          {productAnalytics?.enabled && productAnalytics.totals.eventCount > 0 && (
            <>
              <p className="muted">{productAnalytics.totals.eventCount} product events tracked over the last {productAnalytics.windowDays} days.</p>
              <ul className="progress-topics">
                {productAnalytics.features.slice(0, 5).map((feature) => (
                  <li key={feature.feature}>
                    <span><strong>{feature.feature}</strong> · {feature.eventCount} events</span>
                    <span>{feature.positiveOutcomeRate === null
                      ? "No outcome data"
                      : `${Math.round(feature.positiveOutcomeRate * 100)}% positive outcomes`}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
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

      <section className="feedback-panel" aria-labelledby="feedback-title">
        <div className="feedback-heading">
          <div>
            <p className="eyebrow">Private beta · help us improve</p>
            <h2 id="feedback-title">Send feedback</h2>
            <p className="muted">Your report is stored only on this device and is visible to you and authorized administrators. No documents, tutor conversations, or camera frames are attached.</p>
          </div>
        </div>

        <form className="feedback-form" onSubmit={(event) => void submitFeedback(event)}>
          <fieldset>
            <legend>Category</legend>
            <label>
              <input type="radio" name="feedbackCategory" value="bug" checked={feedbackCategory === "bug"} onChange={() => setFeedbackCategory("bug")} />
              Bug report
            </label>
            <label>
              <input type="radio" name="feedbackCategory" value="usability" checked={feedbackCategory === "usability"} onChange={() => setFeedbackCategory("usability")} />
              Usability
            </label>
            <label>
              <input type="radio" name="feedbackCategory" value="feature_request" checked={feedbackCategory === "feature_request"} onChange={() => setFeedbackCategory("feature_request")} />
              Feature request
            </label>
            <label>
              <input type="radio" name="feedbackCategory" value="other" checked={feedbackCategory === "other"} onChange={() => setFeedbackCategory("other")} />
              Other
            </label>
          </fieldset>

          <label className="document-control" htmlFor="feedback-title">
            Title
            <input
              id="feedback-title"
              maxLength={200}
              minLength={1}
              required
              value={feedbackTitle}
              onChange={(event) => setFeedbackTitle(event.currentTarget.value)}
              placeholder="What did you notice?"
              aria-invalid={feedbackError ? true : undefined}
              aria-describedby={feedbackError ? "feedback-error" : undefined}
            />
          </label>

          <label className="document-control" htmlFor="feedback-description">
            Description
            <textarea
              id="feedback-description"
              maxLength={4000}
              minLength={1}
              required
              rows={5}
              value={feedbackDescription}
              onChange={(event) => setFeedbackDescription(event.currentTarget.value)}
              placeholder="Describe what happened, what you expected, and how to reproduce it."
              aria-invalid={feedbackError ? true : undefined}
              aria-describedby={feedbackError ? "feedback-error" : undefined}
            />
          </label>

          {feedbackError && <p id="feedback-error" className="notice notice--error" role="alert">{feedbackError}</p>}

          <button
            className="camera-action camera-action--primary"
            type="submit"
            disabled={feedbackSubmitting}
          >
            {feedbackSubmitting ? "Sending…" : "Send feedback"}
          </button>
        </form>

        {feedbackSubmitted && (
          <p className="notice" role="status">Your feedback was saved. Thank you for helping improve WonderLearn.</p>
        )}

        <section className="feedback-list" aria-labelledby="my-feedback-title">
          <h3 id="my-feedback-title">Your feedback</h3>
          {myFeedbackLoading && <p className="muted" role="status">Loading your feedback…</p>}
          {myFeedback.length === 0 && !myFeedbackLoading && <p className="muted">You have not submitted any feedback yet.</p>}
          {myFeedback.length > 0 && (
            <ul className="feedback-items">
              {myFeedback.map((item) => (
                <li key={item.feedbackId} className={`feedback-item feedback-item--${item.status}`}>
                  <div className="feedback-item-header">
                    <span className="feedback-item-category">{item.category.replaceAll("_", " ")}</span>
                    <span className={`feedback-item-status feedback-item-status--${item.status}`}>{item.status}</span>
                  </div>
                  <h4>{item.title}</h4>
                  <p>{item.description}</p>
                  <p className="feedback-item-meta">
                    <time dateTime={item.createdAt}>{new Date(item.createdAt).toLocaleString()}</time>{" "}
                    {item.updatedAt !== item.createdAt && <span> · updated {new Date(item.updatedAt).toLocaleString()}</span>}
                  </p>
                  {item.status !== "withdrawn" && (
                    <div className="feedback-item-actions">
                      {editingFeedbackId === item.feedbackId ? (
                        <form className="feedback-edit-form" onSubmit={(event) => void updateMyFeedback(item.feedbackId, event)}>
                          <label>
                            Category
                            <select value={editFeedbackCategory} onChange={(event) => setEditFeedbackCategory(event.currentTarget.value as FeedbackCategory)}>
                              <option value="bug">Bug report</option>
                              <option value="usability">Usability</option>
                              <option value="feature_request">Feature request</option>
                              <option value="other">Other</option>
                            </select>
                          </label>
                          <label>
                            Title
                            <input maxLength={200} minLength={1} required value={editFeedbackTitle} onChange={(event) => setEditFeedbackTitle(event.currentTarget.value)} />
                          </label>
                          <label>
                            Description
                            <textarea maxLength={4000} minLength={1} required rows={4} value={editFeedbackDescription} onChange={(event) => setEditFeedbackDescription(event.currentTarget.value)} />
                          </label>
                          <button type="submit" className="camera-action">Save changes</button>
                          <button type="button" className="camera-action" onClick={() => setEditingFeedbackId(null)}>Cancel</button>
                        </form>
                      ) : (
                        <>
                          <button type="button" className="text-action" onClick={() => {
                            setEditingFeedbackId(item.feedbackId);
                            setEditFeedbackCategory(item.category);
                            setEditFeedbackTitle(item.title);
                            setEditFeedbackDescription(item.description);
                          }}>Edit</button>
                          <button type="button" className="camera-action" onClick={() => void withdrawFeedback(item.feedbackId)}>Withdraw</button>
                        </>
                      )}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        {isAdmin && (
          <section className="feedback-admin" aria-labelledby="admin-feedback-title">
            <h3 id="admin-feedback-title">Admin review</h3>
            <p className="muted">List all beta feedback and update review status. Only administrators can access this view.</p>

            <div className="feedback-admin-filters">
              <label>
                Category
                <select
                  value={feedbackFilterCategory}
                  onChange={(event) => setFeedbackFilterCategory(event.currentTarget.value as FeedbackCategory | "")}
                >
                  <option value="">All categories</option>
                  <option value="bug">Bug report</option>
                  <option value="usability">Usability</option>
                  <option value="feature_request">Feature request</option>
                  <option value="other">Other</option>
                </select>
              </label>
              <label>
                Status
                <select
                  value={feedbackFilterStatus}
                  onChange={(event) => setFeedbackFilterStatus(event.currentTarget.value as FeedbackStatus | "")}
                >
                  <option value="">All statuses</option>
                  <option value="new">New</option>
                  <option value="reviewing">Reviewing</option>
                  <option value="planned">Planned</option>
                  <option value="in_progress">In progress</option>
                  <option value="resolved">Resolved</option>
                  <option value="withdrawn">Withdrawn</option>
                  <option value="declined">Declined</option>
                </select>
              </label>
              <label>
                From date
                <input type="date" value={feedbackDateFrom} onChange={(event) => setFeedbackDateFrom(event.currentTarget.value)} />
              </label>
              <label>
                To date
                <input type="date" value={feedbackDateTo} onChange={(event) => setFeedbackDateTo(event.currentTarget.value)} />
              </label>
              <label>
                Sort by
                <select value={feedbackSortBy} onChange={(event) => setFeedbackSortBy(event.currentTarget.value as "recency" | "created")}>
                  <option value="recency">Last updated</option>
                  <option value="created">Date created</option>
                </select>
              </label>
              <label>
                Order
                <select value={feedbackSortOrder} onChange={(event) => setFeedbackSortOrder(event.currentTarget.value as "asc" | "desc")}>
                  <option value="desc">Descending</option>
                  <option value="asc">Ascending</option>
                </select>
              </label>
              <button type="button" className="camera-action camera-action--primary" onClick={() => void loadAdminFeedback()}>Apply filters</button>
            </div>

            {adminFeedbackLoading && <p className="muted" role="status">Loading feedback for review…</p>}
            {adminFeedbackError && <p className="notice notice--error" role="alert">{adminFeedbackError}</p>}
            {adminFeedback.length === 0 && !adminFeedbackLoading && <p className="muted">No feedback has been submitted yet.</p>}
            {adminFeedback.length > 0 && (
              <table className="feedback-admin-table">
                <thead>
                  <tr>
                    <th>Title</th>
                    <th>Submitted by</th>
                    <th>Category</th>
                    <th>Status</th>
                    <th>Updated</th>
                    <th>Task</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {adminFeedback.map((item) => (
                    <tr key={item.feedbackId}>
                      <td>{item.title}</td>
                      <td>{item.username ?? item.userId}</td>
                      <td>{item.category.replaceAll("_", " ")}</td>
                      <td>
                        <select
                          value={item.status}
                          onChange={(event) => void updateAdminFeedbackStatus(item.feedbackId, event.currentTarget.value as FeedbackStatus)}
                        >
                          <option value="new">New</option>
                          <option value="reviewing">Reviewing</option>
                          <option value="planned">Planned</option>
                          <option value="in_progress">In progress</option>
                          <option value="resolved">Resolved</option>
                          <option value="withdrawn">Withdrawn</option>
                          <option value="declined">Declined</option>
                        </select>
                      </td>
                      <td><time dateTime={item.updatedAt}>{new Date(item.updatedAt).toLocaleString()}</time></td>
                      <td>{item.improvementTaskId ?? <span className="muted">—</span>}</td>
                      <td>
                        <details>
                          <summary>View description</summary>
                          <blockquote>{item.description}</blockquote>
                          {item.resolutionNotes && (
                            <div className="resolution-notes">
                              <strong>Resolution notes:</strong> {item.resolutionNotes}
                            </div>
                          )}
                        </details>
                        <form className="feedback-admin-update" onSubmit={(event) => void updateAdminFeedback(item.feedbackId, event)}>
                          <label>
                            Resolution notes
                            <textarea
                              defaultValue={item.resolutionNotes ?? ""}
                              placeholder="Add resolution notes…"
                              maxLength={4000}
                              rows={2}
                            />
                          </label>
                          <button type="submit" className="camera-action">Save notes</button>
                        </form>
                        {item.improvementTaskId ? (
                          <button
                            type="button"
                            className="text-action"
                            onClick={() => {
                              if (item.improvementTaskId) {
                                void unlinkFeedbackFromTask(item.improvementTaskId, item.feedbackId);
                              }
                            }}
                          >
                            Unlink task
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="text-action"
                            onClick={() => void setLinkTaskFeedbackId(item.feedbackId)}
                          >
                            Link to task
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            {linkTaskFeedbackId !== null && (
              <div className="feedback-admin-link">
                <p className="muted">Link feedback to an improvement task:</p>
                <select
                  value={selectedLinkTaskId ?? ""}
                  onChange={(event) => setSelectedLinkTaskId(event.currentTarget.value || null)}
                >
                  <option value="">Select a task</option>
                  {(improvementTasks ?? []).map((task) => (
                    <option key={task.taskId} value={task.taskId}>{task.title}</option>
                  ))}
                </select>
                <button
                  type="button"
                  className="camera-action"
                  disabled={!selectedLinkTaskId}
                  onClick={() => {
                    if (linkTaskFeedbackId !== null && selectedLinkTaskId !== null) {
                      void linkFeedbackToTask(selectedLinkTaskId, linkTaskFeedbackId);
                    }
                  }}
                >
                  Link
                </button>
                <button type="button" className="camera-action" onClick={() => setLinkTaskFeedbackId(null)}>
                  Cancel
                </button>
              </div>
            )}
          </section>
        )}
        {isAdmin && (
          <section className="feedback-improvement-tasks" aria-labelledby="improvement-tasks-title">
            <h3 id="improvement-tasks-title">Improvement tasks</h3>
            <p className="muted">Turn feedback into tracked improvement tasks. Priority factors marked as estimated are administrator judgments, not observed facts. Report count is measured from linked feedback.</p>
            {improvementTasksError && <p className="notice notice--error" role="alert">{improvementTasksError}</p>}
            {(improvementTasks ?? []).length === 0 && !improvementTasksLoading && <p className="muted">No improvement tasks yet. Create one from feedback above.</p>}
            {(improvementTasks ?? []).length > 0 && (
              <table className="feedback-admin-table">
                <thead>
                  <tr>
                    <th>Title</th>
                    <th>Status</th>
                    <th>Priority</th>
                    <th>Reports</th>
                    <th>Severity</th>
                    <th>Effort</th>
                    <th>Regression test</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {(improvementTasks ?? []).map((task) => (
                    <tr key={task.taskId} className={`task-row task-row--${task.status}`}>
                      <td>{task.title}</td>
                      <td>{task.status}</td>
                      <td>{task.priorityScore.toFixed(1)}</td>
                      <td>{task.reportCount} <span className="muted">(measured)</span></td>
                      <td>{task.severity} <span className="muted">(est.)</span></td>
                      <td>{task.implementationEffort} <span className="muted">(est.)</span></td>
                      <td>{task.regressionTest ?? <span className="muted">—</span>}</td>
                      <td>
                        {editingTaskId === task.taskId ? (
                          <form className="feedback-edit-form" onSubmit={(event) => void updateImprovementTask(task.taskId, event)}>
                            <label>
                              Title<input defaultValue={task.title} minLength={1} maxLength={200} required />
                            </label>
                            <label>
                              Description<textarea defaultValue={task.description} minLength={1} maxLength={4000} required rows={3} /></label>
                            <label>
                              Status
                              <select defaultValue={task.status}>
                                <option value="proposed">Proposed</option>
                                <option value="in_progress">In progress</option>
                                <option value="resolved">Resolved</option>
                                <option value="declined">Declined</option>
                              </select>
                            </label>
                            <label>
                              Severity (1-5)<input type="number" defaultValue={task.severity} min={1} max={5} required />
                            </label>
                            <label>
                              Impact (1-5)<input type="number" defaultValue={task.impact} min={1} max={5} required />
                            </label>
                            <label>
                              Reproducibility (1-5)<input type="number" defaultValue={task.reproducibility} min={1} max={5} required />
                            </label>
                            <label>
                              Workflow frequency (1-5)<input type="number" defaultValue={task.workflowFrequency} min={1} max={5} required />
                            </label>
                            <label>
                              Implementation effort (1-5)<input type="number" defaultValue={task.implementationEffort} min={1} max={5} required />
                            </label>
                            <label>
                              Regression test<input defaultValue={task.regressionTest ?? ""} maxLength={200} />
                            </label>
                            <label>
                              Implemented<input type="checkbox" defaultChecked={task.implemented} />
                            </label>
                            <button type="submit" className="camera-action">Save</button>
                            <button type="button" className="camera-action" onClick={() => setEditingTaskId(null)}>Cancel</button>
                          </form>
                        ) : (
                          <>
                            {task.implemented && <span className="feedback-item-status feedback-item-status--resolved">Implemented</span>}
                            <button type="button" className="text-action" onClick={() => setEditingTaskId(task.taskId)}>Edit</button>
                            {task.feedbackIds.length > 0 && (
                              <button
                                type="button"
                                className="text-action"
                                onClick={() => void unlinkFeedbackFromTask(task.taskId, task.feedbackIds[0])}
                              >
                                Unlink first
                              </button>
                            )}
                          </>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            <details className="feedback-admin-filters">
              <summary>Prioritization method</summary>
              <p>
                Priority score = severity×20 + impact×15 + reportCount×10 + reproducibility×10 +
                workflowFrequency×10 − implementationEffort×5.
              </p>
              <p>
                <strong>Measured:</strong> report count (number of linked feedback items).{" "}
                <strong>Estimated:</strong> severity, impact, reproducibility, workflow frequency,
                implementation effort (administrator judgments, 1–5 scale). Higher score = higher
                priority. Implementation effort reduces the score.
              </p>
            </details>

            <h4>Create improvement task</h4>
            <form className="feedback-form" onSubmit={(event) => void createImprovementTask(event)}>
              <label>
                Title<input
                  value={taskForm.title}
                  onChange={(event) => setTaskForm((current) => ({ ...current, title: event.currentTarget.value }))}
                  minLength={1} maxLength={200} required
                />
              </label>
              <label>
                Description<textarea
                  value={taskForm.description}
                  onChange={(event) => setTaskForm((current) => ({ ...current, description: event.currentTarget.value }))}
                  minLength={1} maxLength={4000} required rows={3}
                /></label>
              <label>
                Status
                <select
                  value={taskForm.status}
                  onChange={(event) => setTaskForm((current) => ({ ...current, status: event.currentTarget.value as "proposed" | "in_progress" | "resolved" | "declined" }))}
                >
                  <option value="proposed">Proposed</option>
                  <option value="in_progress">In progress</option>
                  <option value="resolved">Resolved</option>
                  <option value="declined">Declined</option>
                </select>
              </label>
              <label>
                Severity (1-5, est.)<input
                  type="number" value={taskForm.severity}
                  onChange={(event) => setTaskForm((current) => ({ ...current, severity: parseInt(event.currentTarget.value, 10) || 3 }))}
                  min={1} max={5} required
                />
              </label>
              <label>
                Impact (1-5, est.)<input
                  type="number" value={taskForm.impact}
                  onChange={(event) => setTaskForm((current) => ({ ...current, impact: parseInt(event.currentTarget.value, 10) || 3 }))}
                  min={1} max={5} required
                />
              </label>
              <label>
                Reproducibility (1-5, est.)<input
                  type="number" value={taskForm.reproducibility}
                  onChange={(event) => setTaskForm((current) => ({ ...current, reproducibility: parseInt(event.currentTarget.value, 10) || 3 }))}
                  min={1} max={5} required
                />
              </label>
              <label>
                Workflow frequency (1-5, est.)<input
                  type="number" value={taskForm.workflowFrequency}
                  onChange={(event) => setTaskForm((current) => ({ ...current, workflowFrequency: parseInt(event.currentTarget.value, 10) || 3 }))}
                  min={1} max={5} required
                />
              </label>
              <label>
                Implementation effort (1-5, est.)<input
                  type="number" value={taskForm.implementationEffort}
                  onChange={(event) => setTaskForm((current) => ({ ...current, implementationEffort: parseInt(event.currentTarget.value, 10) || 3 }))}
                  min={1} max={5} required
                />
              </label>
              <label>
                Regression test<input
                  value={taskForm.regressionTest}
                  onChange={(event) => setTaskForm((current) => ({ ...current, regressionTest: event.currentTarget.value }))}
                  maxLength={200}
                  placeholder="Which regression test verifies the fix?"
                />
              </label>
              <label>
                Feedback IDs (comma-separated)<input
                  value={taskForm.feedbackIds}
                  onChange={(event) => setTaskForm((current) => ({ ...current, feedbackIds: event.currentTarget.value }))}
                  placeholder="feedback_abc, feedback_def"
                />
              </label>
              {creatingTask && <p className="muted" role="status">Creating task…</p>}
              <button type="submit" className="camera-action camera-action--primary" disabled={creatingTask}>
                {creatingTask ? "Creating…" : "Create task"}
              </button>
            </form>
          </section>
        )}
      </section>
      <section className="workspace-panel" aria-labelledby="workspace-title">
        <div className="workspace-heading">
          <div>
            <p className="eyebrow">Shared study spaces</p>
            <h2 id="workspace-panel-title">Study spaces</h2>
            <p className="muted">Shared spaces are private to their members. Your personal documents, quiz history, and analytics are never shared unless you explicitly add them to a space.</p>
          </div>
        </div>

        {workspacesLoading && <p className="muted" role="status">Loading workspaces…</p>}
        {workspacesError && <p className="notice notice--error" role="alert">{workspacesError}</p>}

        {!selectedSpaceId && (
          <>
            <form className="workspace-form" onSubmit={(event) => void createWorkspace(event)}>
              <h3>Create a study space</h3>
              <label className="document-control" htmlFor="workspace-title">
                Title
                <input
                  id="workspace-title"
                  value={newWorkspaceTitle}
                  maxLength={200}
                  minLength={1}
                  required
                  onChange={(event) => setNewWorkspaceTitle(event.currentTarget.value)}
                  placeholder="For example, Biology study group"
                />
              </label>
              <label className="document-control" htmlFor="workspace-description">
                Description
                <textarea
                  id="workspace-description"
                  value={newWorkspaceDescription}
                  maxLength={4000}
                  minLength={1}
                  required
                  rows={3}
                  onChange={(event) => setNewWorkspaceDescription(event.currentTarget.value)}
                  placeholder="What is this space for?"
                />
              </label>
              <label className="document-control" htmlFor="workspace-objective">
                Learning objective (optional)
                <input
                  id="workspace-objective"
                  value={newWorkspaceObjective}
                  maxLength={1000}
                  onChange={(event) => setNewWorkspaceObjective(event.currentTarget.value)}
                  placeholder="For example, Master photosynthesis by reading chapter 3"
                />
              </label>
              <button type="submit" className="camera-action camera-action--primary" disabled={creatingWorkspace}>
                {creatingWorkspace ? "Creating…" : "Create space"}
              </button>
            </form>

            {workspaces.length === 0 && <p className="muted">You have no study spaces yet. Create one above to collaborate with others.</p>}
            {workspaces.length > 0 && (
              <>
                <h3>Your spaces</h3>
                <ul className="workspace-list" aria-label="Your study spaces" role="none">
          {workspaces.map((space) => (
            <li key={space.spaceId} className={`workspace-item ${space.userRole === "owner" ? "workspace-item--owner" : ""}`} role="none">
                      <div className="workspace-item-info">
                        <strong>{space.title}</strong>
                        <span>{space.description}</span>
                        {space.learningObjective && <p className="muted"><strong>Objective:</strong> {space.learningObjective}</p>}
                        <p className="workspace-item-meta">
                          {space.memberCount} member{space.memberCount !== 1 ? "s" : ""} · {space.status}
                          {space.userRole === "owner" && <span> · owner</span>}
                        </p>
                      </div>
                      <div className="workspace-item-actions">
                        <button type="button" className="text-action" onClick={() => void loadWorkspace(space.spaceId)}>Open</button>
                        {space.status === "active" && space.userRole === "owner" && (
                          <button type="button" className="text-action" onClick={() => void archiveWorkspace(space.spaceId)}>Archive</button>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}

        {selectedSpace && (
          <>
            <button type="button" className="text-action" onClick={() => { setSelectedSpace(null); setSelectedSpaceId(null); }}>← Back to spaces</button>
            <div className="workspace-detail">
              <h3>{selectedSpace.title}</h3>
              <p>{selectedSpace.description}</p>
              {selectedSpace.learningObjective && <p className="muted"><strong>Learning objective:</strong> {selectedSpace.learningObjective}</p>}
              <p className="workspace-detail-meta">
                {selectedSpace.memberCount} member{selectedSpace.memberCount !== 1 ? "s" : ""} · {selectedSpace.status} · you are <strong>{selectedSpace.userRole}</strong>
              </p>
              {selectedSpace.userRole === "owner" && selectedSpace.status === "active" && (
                <>
                  <div className="workspace-actions">
                    <button type="button" className="camera-action" onClick={() => void archiveWorkspace(selectedSpace.spaceId)}>Archive space</button>
                    <button type="button" className="camera-action" onClick={() => void deleteWorkspace(selectedSpace.spaceId)}>Delete space</button>
                  </div>
                  <details className="workspace-edit-form"><summary>Edit space details</summary>
                    <form className="workspace-form" onSubmit={(event) => void updateWorkspace(selectedSpace.spaceId, event)}>
                      <label className="document-control" htmlFor="edit-workspace-title">
                        Title
                        <input id="edit-workspace-title" name="title" defaultValue={selectedSpace.title} minLength={1} maxLength={200} required />
                      </label>
                      <label className="document-control" htmlFor="edit-workspace-description">
                        Description
                        <textarea name="description" defaultValue={selectedSpace.description} minLength={1} maxLength={4000} rows={3} required />
                      </label>
                      <label className="document-control" htmlFor="edit-workspace-objective">
                        Learning objective
                        <input name="learningObjective" defaultValue={selectedSpace.learningObjective ?? ""} maxLength={1000} />
                      </label>
                      <label className="document-control" htmlFor="edit-workspace-status">
                        Status
                        <select name="status">
                          <option value="active">Active</option>
                          <option value="archived">Archived</option>
                        </select>
                      </label>
                      <button type="submit" className="camera-action">Save changes</button>
                    </form>
                  </details>
                </>
              )}
            </div>

            <div className="workspace-grid">
              <section className="workspace-section" aria-labelledby="workspace-members-title">
                <h3 id="workspace-members-title">Members</h3>
                {workspaceMembersLoading && <p className="muted">Loading members…</p>}
                {workspaceMembers.length === 0 && <p className="muted">No members.</p>}
                {workspaceMembers.length > 0 && (
                  <ul className="member-list" role="none">
                    {workspaceMembers.map((member) => (
                      <li key={member.userId} className={`member-item ${member.role === "owner" ? "member-item--owner" : ""}`} role="none">
                        <span>{member.username ?? member.userId}</span>
                        <span className={`member-role member-role--${member.role}`}>{member.role}</span>
                        {selectedSpace?.userRole === "owner" && member.role === "member" && (
                          <button type="button" className="camera-action" onClick={() => void removeMember(selectedSpace.spaceId, member.userId)}>Remove</button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                {selectedSpace?.userRole === "owner" && (
                  <form className="workspace-form" onSubmit={(event) => { event.preventDefault(); void inviteMember(selectedSpace!.spaceId); }}>
                    <label className="document-control" htmlFor="invite-username">
                      Invite member by username
                      <input
                        id="invite-username"
                        value={inviteUsername}
                        maxLength={64}
                        minLength={3}
                        required
                        onChange={(event) => setInviteUsername(event.currentTarget.value)}
                        placeholder="Username"
                      />
                    </label>
                    <button type="submit" className="camera-action camera-action--primary" disabled={invitingMember}>
                      {invitingMember ? "Inviting…" : "Invite"}
                    </button>
                  </form>
                )}
              </section>

              <section className="workspace-section" aria-labelledby="workspace-shared-docs-title">
                <h3 id="workspace-shared-docs-title">Shared documents</h3>
                {workspaceDocuments.length === 0 && <p className="muted">No documents shared yet.</p>}
                {workspaceDocuments.map((doc) => (
                  <article key={doc.documentId} className="workspace-doc-item">
                    <strong>{doc.displayFilename}</strong>
                    <span>{doc.pageCount} pages · shared {new Date(doc.sharedAt).toLocaleDateString()}</span>
                    {selectedSpace?.userRole === "owner" || doc.sharedBy === (workspaces.find((w) => w.spaceId === selectedSpaceId)?.ownerId ?? "") && (
                      <button type="button" className="text-action" onClick={() => void unshareDocument(selectedSpace!.spaceId, doc.documentId)}>Unshare</button>
                    )}
                  </article>
                ))}
                <form className="workspace-form" onSubmit={(event) => { event.preventDefault(); void shareDocument(selectedSpace!.spaceId); }}>
                  <label className="document-control" htmlFor="share-document-id">
                    Share a document by ID
                    <input
                      id="share-document-id"
                      value={newDocumentShare}
                      maxLength={80}
                      minLength={1}
                      required
                      onChange={(event) => setNewDocumentShare(event.currentTarget.value)}
                      placeholder="Document ID from your documents list"
                    />
                  </label>
                  <button type="submit" className="camera-action camera-action--primary">Share document</button>
                </form>
              </section>

              <section className="workspace-section" aria-labelledby="workspace-shared-quizzes-title">
                <h3 id="workspace-shared-quizzes-title">Shared quizzes</h3>
                {workspaceQuizzes.length === 0 && <p className="muted">No quizzes shared yet.</p>}
                {workspaceQuizzes.map((quiz) => (
                  <article key={quiz.quizId} className="workspace-quiz-item">
                    <strong>{quiz.topic}</strong>
                    <span>shared {new Date(quiz.sharedAt).toLocaleDateString()}</span>
                    {selectedSpace?.userRole === "owner" || quiz.sharedBy === (workspaces.find((w) => w.spaceId === selectedSpaceId)?.ownerId ?? "") && (
                      <button type="button" className="text-action" onClick={() => void unshareQuiz(selectedSpace!.spaceId, quiz.quizId)}>Unshare</button>
                    )}
                  </article>
                ))}
                <form className="workspace-form" onSubmit={(event) => { event.preventDefault(); void shareQuiz(selectedSpace!.spaceId); }}>
                  <label className="document-control" htmlFor="share-quiz-id">
                    Share a quiz by ID
                    <input
                      id="share-quiz-id"
                      value={newQuizShare}
                      maxLength={80}
                      minLength={1}
                      required
                      onChange={(event) => setNewQuizShare(event.currentTarget.value)}
                      placeholder="Quiz ID from your quizzes"
                    />
                  </label>
                  <button type="submit" className="camera-action camera-action--primary">Share quiz</button>
                </form>
              </section>

              <section className="workspace-section" aria-labelledby="workspace-discussions-title">
                <h3 id="workspace-discussions-title">Discussions</h3>
                {workspaceDiscussions.length === 0 && <p className="muted">No discussions yet.</p>}
                {workspaceDiscussions.map((discussion) => (
                  <article key={discussion.discussionId} className="workspace-discussion-item">
                    <strong>{discussion.username ?? discussion.userId}</strong>
                    <p>{discussion.content}</p>
                    <span className="muted">{new Date(discussion.createdAt).toLocaleString()}</span>
                    <button type="button" className="text-action" onClick={() => void loadWorkspaceDiscussionReplies(discussion.discussionId)}>
                      Replies
                    </button>
                  </article>
                ))}
                <form className="workspace-form" onSubmit={(event) => void createDiscussion(selectedSpace!.spaceId, event)}>
                  <label className="document-control" htmlFor="discussion-content">
                    Start a discussion
                    <textarea
                      id="discussion-content"
                      value={newDiscussionContent}
                      maxLength={4000}
                      minLength={1}
                      required
                      rows={3}
                      onChange={(event) => setNewDiscussionContent(event.currentTarget.value)}
                      placeholder="What would you like to discuss?"
                    />
                  </label>
                  <button type="submit" className="camera-action camera-action--primary" disabled={creatingDiscussion}>
                    {creatingDiscussion ? "Posting…" : "Post"}
                  </button>
                </form>
                {selectedDiscussionId && workspaceDiscussionReplies.length > 0 && (
                  <div className="workspace-replies">
                    <h4>Replies</h4>
                    <ul className="reply-list" role="none">
                      {workspaceDiscussionReplies.map((reply) => (
                        <li key={reply.replyId} className="reply-item" role="none">
                          <strong>{reply.username ?? reply.userId}</strong>
                          <p>{reply.content}</p>
                          <span className="muted">{new Date(reply.createdAt).toLocaleString()}</span>
                        </li>
                      ))}
                    </ul>
                    <form className="workspace-form" onSubmit={(event) => void createReply(selectedSpace!.spaceId, selectedDiscussionId!, event)}>
                      <label className="document-control" htmlFor="reply-content">
                        Reply
                        <textarea
                          id="reply-content"
                          value={newReplyContent}
                          maxLength={4000}
                          minLength={1}
                          required
                          rows={2}
                          onChange={(event) => setNewReplyContent(event.currentTarget.value)}
                          placeholder="Write a reply…"
                        />
                      </label>
                      <button type="submit" className="camera-action">Post reply</button>
                    </form>
                  </div>
                )}
              </section>
            </div>
          </>
        )}
      </section>
      <section className="content-studio-panel" aria-labelledby="content-studio-title">
        <div className="workspace-heading">
          <div>
            <p className="eyebrow">AI content studio</p>
            <h2 id="content-studio-title">Content studio</h2>
            <p className="muted">Turn your documents or shared study-space materials into structured learning resources. All generated content cites its sources and is labelled unverified until you review it.</p>
          </div>
        </div>

        {contentResourcesLoading && <p className="muted" role="status">Loading content resources…</p>}
        {contentResourcesError && <p className="notice notice--error" role="alert">{contentResourcesError}</p>}

        {!selectedResourceId && (
          <>
            <form className="workspace-form" onSubmit={(event) => void createContentResource(event)}>
              <h3>Create a content resource</h3>
              <label className="document-control" htmlFor="content-title">
                Title
                <input
                  id="content-title"
                  value={newContentTitle}
                  maxLength={200}
                  minLength={1}
                  required
                  onChange={(event) => setNewContentTitle(event.currentTarget.value)}
                  placeholder="For example, Photosynthesis study notes"
                />
              </label>
              <label className="document-control" htmlFor="content-description">
                Description (optional)
                <textarea
                  id="content-description"
                  value={newContentDescription}
                  maxLength={4000}
                  rows={3}
                  onChange={(event) => setNewContentDescription(event.currentTarget.value)}
                  placeholder="What is this resource about?"
                />
              </label>
              <label className="document-control" htmlFor="content-topic">
                Topic
                <input
                  id="content-topic"
                  value={newContentTopic}
                  maxLength={160}
                  minLength={2}
                  required
                  onChange={(event) => setNewContentTopic(event.currentTarget.value)}
                  placeholder="For example, photosynthesis"
                />
              </label>
              <label className="document-control" htmlFor="content-format">
                Output format
                <select
                  id="content-format"
                  value={newContentFormat}
                  onChange={(event) => setNewContentFormat(event.currentTarget.value as "notes" | "summary" | "flashcards" | "questions" | "revision_sheet" | "concept_outline")}
                >
                  <option value="notes">Study notes</option>
                  <option value="summary">Topic summary</option>
                  <option value="flashcards">Flashcards</option>
                  <option value="questions">Practice questions</option>
                  <option value="revision_sheet">Revision sheet</option>
                  <option value="concept_outline">Concept outline</option>
                </select>
              </label>
              <label className="document-control" htmlFor="content-detail">
                Level of detail
                <select
                  id="content-detail"
                  value={newContentDetail}
                  onChange={(event) => setNewContentDetail(event.currentTarget.value as "brief" | "standard" | "comprehensive")}
                >
                  <option value="brief">Brief</option>
                  <option value="standard">Standard</option>
                  <option value="comprehensive">Comprehensive</option>
                </select>
              </label>
              <button type="submit" className="camera-action camera-action--primary" disabled={creatingContent}>
                {creatingContent ? "Generating…" : "Generate resource"}
              </button>
            </form>

            {contentResources.length === 0 && <p className="muted">You have no content resources yet. Create one above to get started.</p>}
            {contentResources.length > 0 && (
              <>
                <h3>Your content resources</h3>
                <ul className="workspace-list" role="none">
                    {contentResources.map((resource) => (
                    <li key={resource.resourceId} className={`workspace-item`} role="none">
                      <div className="workspace-item-info">
                        <strong>{resource.title}</strong>
                        <span>{resource.description}</span>
                        <p className="workspace-item-meta">
                          {resource.format} · {resource.detailLevel} · {resource.status}
                        </p>
                      </div>
                      <div className="workspace-item-actions">
                        <button type="button" className="text-action" onClick={() => void loadContentResource(resource.resourceId)}>Open</button>
                      </div>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}

        {selectedResource && (
          <>
            <button type="button" className="text-action" onClick={() => { setSelectedResource(null); setSelectedResourceId(null); }}>← Back to resources</button>
            <div className="workspace-detail">
              <h3>{selectedResource.title}</h3>
              <p>{selectedResource.description}</p>
              <p className="workspace-detail-meta">
                {selectedResource.format} · {selectedResource.detailLevel} · {selectedResource.status}
              </p>
              {selectedResource.status === "draft" && (
                <div className="workspace-actions">
                  <button type="button" className="camera-action" onClick={() => void updateContentResourceMetadata(selectedResource.resourceId, {} as any)}>Mark as reviewed</button>
                  <button type="button" className="camera-action" onClick={() => void deleteContentResource(selectedResource.resourceId)}>Delete resource</button>
                </div>
              )}
              {!contentValidationLoading && !contentValidation && (
                <button type="button" className="camera-action camera-action--primary" onClick={() => void validateContentResource(selectedResource.resourceId)}>
                  Validate content
                </button>
              )}
              {contentValidationLoading && <p className="muted" role="status">Validating content against sources…</p>}
              {contentValidationError && <p className="notice notice--error" role="alert">{contentValidationError}</p>}
              {contentValidation && (
                <details className="validation-results" open>
                  <summary>Validation results</summary>
                  <div className="validation-summary">
                    <span><strong>Status:</strong> {contentValidation.status}</span>
                    <span><strong>Citations valid:</strong> {contentValidation.citationValidity ? "Yes" : "No"}</span>
                    <span><strong>Schema valid:</strong> {contentValidation.schemaValidity ? "Yes" : "No"}</span>
                    <span><strong>Unsupported claims:</strong> {contentValidation.unsupportedClaimCount}</span>
                    <span><strong>Evidence coverage:</strong> {(contentValidation.evidenceCoverage * 100).toFixed(0)}%</span>
                  </div>
                  {contentValidation.details.invalidCitations.length > 0 && (
                    <div>
                      <h4>Invalid citations</h4>
                      <ul>{contentValidation.details.invalidCitations.map((c) => <li key={c.citationId ?? ""}>{c.citationId ?? "unknown"} — {c.reason}</li>)}</ul>
                    </div>
                  )}
                  {contentValidation.details.sectionResults.length > 0 && (
                    <div>
                      <h4>Section results</h4>
                      <ul>
                        {contentValidation.details.sectionResults.map((sr) => (
                          <li key={sr.sectionId}>
                            <strong>{sr.sectionId}</strong> · schema {sr.schemaValidity ? "ok" : "fail"} · coverage {(sr.evidenceCoverage * 100).toFixed(0)}% · unsupported {sr.unsupportedClaimCount}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </details>
              )}
            </div>

            <div className="workspace-grid">
              <section className="workspace-section" aria-labelledby="content-sections-title">
                <h3 id="content-sections-title">Sections</h3>
                {selectedResource.sections.length === 0 && <p className="muted">No sections yet.</p>}
                {selectedResource.sections.map((section) => (
                  <article key={section.sectionId} className="workspace-discussion-item" role="none">
                    <h4>{section.sectionTitle}</h4>
                    <span className={`section-type section-type--${section.sectionType}`}>{section.sectionType}</span>
                    {section.verificationStatus && (
                      <span className={`verification-badge verification--${section.verificationStatus}`}>{section.verificationStatus.replaceAll("_", " ")}</span>
                    )}
                    <p>{section.content}</p>
                    {section.citations.length > 0 && (
                      <details className="topic-citations"><summary>Source passages ({section.citations.length})</summary>
                        <ul>{section.citations.map((citation) => <li key={citation.citationId}><blockquote>{citation.text}</blockquote><span><strong>{citation.displayFilename}</strong> · page {citation.pageNumber} · chunk <code>{citation.citationId}</code></span></li>)}</ul>
                      </details>
                    )}
                    <div className="section-actions">
                      {!section.verificationStatus && (
                        <>
                          <button type="button" className="camera-action" disabled={verifyingSectionId === section.sectionId || regeneratingSectionId === section.sectionId} onClick={() => void verifyContentSection(selectedResource.resourceId, section.sectionId, "verified")}>Mark verified</button>
                          <button type="button" className="camera-action" disabled={verifyingSectionId === section.sectionId || regeneratingSectionId === section.sectionId} onClick={() => void verifyContentSection(selectedResource.resourceId, section.sectionId, "needs_review")}>Mark needs review</button>
                          <button type="button" className="camera-action" disabled={verifyingSectionId === section.sectionId || regeneratingSectionId === section.sectionId} onClick={() => void verifyContentSection(selectedResource.resourceId, section.sectionId, "insufficient_evidence")}>Mark insufficient evidence</button>
                        </>
                      )}
                      {section.verificationStatus === "verified" && (
                        <span className="muted">Reviewed by {section.verifiedBy}</span>
                      )}
                      <button type="button" className="camera-action" disabled={verifyingSectionId === section.sectionId || regeneratingSectionId === section.sectionId} onClick={() => {
                        const newContent = window.prompt("New content for this section:", section.content);
                        if (newContent) void regenerateContentSection(selectedResource.resourceId, section.sectionId, newContent, section.citations, section.sectionType, section.sectionTitle);
                      }}>Regenerate</button>
                    </div>
                  </article>
                ))}
              </section>
            </div>
          </>
        )}
      </section>
      <section className="search-panel" aria-labelledby="search-title">
        <div className="workspace-heading">
          <div>
            <p className="eyebrow">Unified search</p>
            <h2 id="search-title">Search across your materials</h2>
            <p className="muted">Search documents, notes, flashcards, quizzes, learning objectives, and knowledge-graph concepts. Results are ranked by relevance and cite their sources.</p>
          </div>
        </div>

        {searchLoading && <p className="muted" role="status">Searching…</p>}
        {searchError && <p className="notice notice--error" role="alert">{searchError}</p>}

        <form className="workspace-form" onSubmit={(event) => { event.preventDefault(); void runSearch(searchQuery, searchMode); }}>
          <label className="document-control" htmlFor="search-query">
            Search your study materials
            <input
              id="search-query"
              value={searchQuery}
              maxLength={2_000}
              minLength={1}
              required
              onChange={(event) => setSearchQuery(event.currentTarget.value)}
              placeholder="For example, photosynthesis, chlorophyll, light energy"
              aria-invalid={searchError ? true : undefined}
              aria-describedby={searchError ? "search-query-error" : undefined}
            />
          </label>
          <div className="search-mode-row">
            <label>
              <input
                type="radio"
                name="searchMode"
                value="lexical"
                checked={searchMode === null || searchMode === "lexical"}
                onChange={() => void setSearchMode("lexical")}
              />
              Keyword
            </label>
            <label>
              <input
                type="radio"
                name="searchMode"
                value="semantic"
                checked={searchMode === "semantic"}
                onChange={() => void setSearchMode("semantic")}
              />
              Semantic
            </label>
            <label>
              <input
                type="radio"
                name="searchMode"
                value="hybrid"
                checked={searchMode === "hybrid"}
                onChange={() => void setSearchMode("hybrid")}
              />
              Hybrid
            </label>
          </div>
          <button type="submit" className="camera-action camera-action--primary" disabled={searchLoading}>
            {searchLoading ? "Searching…" : "Search"}
          </button>
        </form>

        {searchQuery && <p className="muted" role="status">
          Searching for <strong>"{searchQuery}"</strong>{searchMode ? ` (${searchMode})` : ""}
          {searchFallbackReason && ` · ${searchFallbackReason.replaceAll("_", " ")}`}
        </p>}

        {searchResults.length === 0 && searchQuery && !searchLoading && !searchError && (
          <p className="notice" role="status">No results found for "{searchQuery}". Try different terms or upload more material.</p>
        )}

        {searchResults.length > 0 && (
          <>
            <p className="muted" role="status">
              {searchTotal} result{searchTotal !== 1 ? "s" : ""} across {
                Object.entries(searchByType).map(([type, count]) => `${count} ${type}`).join(", ")
              }.
            </p>
            <div className="search-results-list">
              {searchResults.map((result) => (
                <article key={result.id} className="search-result-item" role="none">
                  <div className="search-result-heading">
                    <span className={`search-result-type search-result-type--${result.sourceType}`}>{result.resultType.replaceAll("_", " ")}</span>
                    <span className={`search-result-score`}>{Math.round(result.score * 100)}% match</span>
                  </div>
                  <h4>{result.title}</h4>
                  {result.sourceTitle && <p className="search-result-source"><strong>Source:</strong> {result.sourceTitle}</p>}
                  {result.pageNumber != null && <p className="search-result-page"><strong>Page:</strong> {result.pageNumber}</p>}
                  <p className="search-result-snippet">{result.snippet}</p>
                  {result.citations.length > 0 && (
                    <details className="search-result-citations"><summary>Source passages ({result.citations.length})</summary>
                      <ul>{result.citations.map((citation) => <li key={citation.citationId}><blockquote>{citation.text}</blockquote><span><strong>{citation.displayFilename}</strong> · page {citation.pageNumber} · chunk <code>{citation.citationId}</code></span></li>)}</ul>
                    </details>
                  )}
                </article>
              ))}
            </div>
          </>
        )}
      </section>
      <section className="revision-queue-panel" aria-labelledby="revision-queue-title">
        <div className="workspace-heading">
          <div>
            <p className="eyebrow">Smart Revision & Memory</p>
            <h2 id="revision-queue-title">Revision Queue</h2>
            <p className="muted">Review items due for practice based on your learning history. Spaced repetition helps you retain what you've learned.</p>
          </div>
        </div>

        {revisionLoading && <p className="muted" role="status">Loading revision queue…</p>}
        {revisionError && <p className="notice notice--error" role="alert">{revisionError}</p>}

        {!revisionLoading && revisionTotal === 0 && (
          <p className="notice" role="status">No items are due for review. Check back later or keep studying to build your review queue.</p>
        )}

        {!revisionLoading && revisionTotal > 0 && (
          <>
            <p className="muted" role="status">
              {revisionTotal} item{revisionTotal !== 1 ? "s" : ""} due for review
            </p>
            <div className="revision-queue-list">
              {revisionQueue.map((item) => (
                <article key={item.itemId} className="revision-item" role="none">
                  <div className="revision-item-header">
                    <span className={`revision-item-type revision-item-type--${item.itemType}`}>{item.itemType}</span>
                    <span className="revision-item-due">Due: {new Date(item.dueAt).toLocaleDateString()}</span>
                  </div>
                  <h4>{item.title}</h4>
                  {item.prompt && <p>{item.prompt}</p>}
                  {item.explanation && (
                    <details className="revision-explanation"><summary>Explanation</summary><p>{item.explanation}</p></details>
                  )}
                  {item.citations.length > 0 && (
                    <details className="revision-citations"><summary>Source passages ({item.citations.length})</summary>
                      <ul>{item.citations.map((citation) => <li key={citation.citationId}><blockquote>{citation.text}</blockquote><span><strong>{citation.displayFilename}</strong> · page {citation.pageNumber}</span></li>)}</ul>
                    </details>
                  )}
                  <div className="revision-actions">
                    <button type="button" className="camera-action camera-action--primary" onClick={() => void submitSectionReview(item.itemType, item.itemId, "remembered")}>
                      Remembered
                    </button>
                    <button type="button" className="camera-action" onClick={() => void submitSectionReview(item.itemType, item.itemId, "partially")}>
                      Partially
                    </button>
                    <button type="button" className="camera-action" onClick={() => void submitSectionReview(item.itemType, item.itemId, "forgotten")}>
                      Forgotten
                    </button>
                  </div>
                </article>
              ))}
            </div>
          </>
        )}
      </section>

      {/* --- M43: Personal AI Learning Companion Panel --- */}
      <section className="companion-panel" aria-labelledby="companion-title">
        <div className="workspace-heading">
          <div>
            <p className="eyebrow">Personal AI Learning Companion</p>
            <h3 id="companion-title">Companion Chat</h3>
            <p className="muted">Ask questions about your learning materials, start quizzes, review due items, and get study recommendations.</p>
          </div>
        </div>

        {conversationLoading && <p className="muted" role="status">Loading conversations…</p>}
        {conversationError && <p className="notice notice--error" role="alert">{conversationError}</p>}

        {!conversationLoading && conversations.length === 0 && (
          <p className="notice" role="status">No conversations yet. Send a message to start a conversation.</p>
        )}

        {!conversationLoading && conversations.length > 0 && (
          <div className="conversation-list">
            {conversations.map((conv) => (
              <button
                key={conv.conversationId}
                type="button"
                className={`conversation-item ${selectedConversationId === conv.conversationId ? 'active' : ''}`}
                onClick={() => {
                  setSelectedConversationId(conv.conversationId);
                  void loadMessages(conv.conversationId);
                }}
              >
                <div className="conversation-title">{conv.title}</div>
                <div className="conversation-updated">{new Date(conv.updatedAt).toLocaleDateString()}</div>
              </button>
            ))}
            <button type="button" className="camera-action camera-action--primary" onClick={() => void createConversation()}>
              New conversation
            </button>
          </div>
        )}

        {selectedConversationId && (
          <div className="chat-area">
            {messagesLoading && <p className="muted" role="status">Loading messages…</p>}
            {messagesError && <p className="notice notice--error" role="alert">{messagesError}</p>}

            {!messagesLoading && messages.length === 0 && (
              <p className="notice" role="status">No messages yet. Send a message to start chatting.</p>
            )}

            {!messagesLoading && messages.length > 0 && (
              <div className="chat-messages" role="log" aria-label="Conversation messages">
                {messages.map((msg) => (
                  <div key={msg.messageId} className={`chat-message ${msg.role}`}>
                    <div className="chat-message-content">{msg.content}</div>
                    {msg.citations.length > 0 && (
                      <details className="chat-citations"><summary>Sources ({msg.citations.length})</summary>
                        <ul>{msg.citations.map((citation) => <li key={citation.citationId}><blockquote>{citation.text}</blockquote><span><strong>{citation.displayFilename}</strong> page {citation.pageNumber}</span></li>)}</ul>
                      </details>
                    )}
                    <div className="chat-message-meta">{msg.status}</div>
                  </div>
                ))}
              </div>
            )}
            {confirmAction && (
              <div className="confirm-action">
                <p>Confirm: {confirmAction.description}</p>
                <button type="button" className="camera-action camera-action--primary" onClick={() => void confirmCompanionAction()}>
                  Confirm
                </button>
                <button type="button" className="camera-action" onClick={() => { setConfirmAction(null); setConfirmPayload({}); }}>
                  Cancel
                </button>
              </div>
            )}
            <div className="chat-input-area">
              <input
                type="text"
                placeholder="Ask a question, start a quiz, or ask for study recommendations…"
                value={composingMessage}
                onChange={(event) => setComposingMessage(event.currentTarget.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void sendCompanionMessage(composingMessage, selectedConversationId!);
                    setComposingMessage("");
                  }
                }}
                disabled={sendingMessage || !selectedConversationId}
              />
              <button
                type="button"
                className="camera-action camera-action--primary"
                onClick={() => {
                  if (!composingMessage.trim() || !selectedConversationId) return;
                  void sendCompanionMessage(composingMessage, selectedConversationId);
                  setComposingMessage("");
                }}
                disabled={sendingMessage || !composingMessage.trim() || !selectedConversationId}
              >
                Send
              </button>
            </div>
          </div>
        )}
      </section>
      {/* --- End M43 Companion Panel --- */}

      {isAdmin && (
        <section className="model-observability-panel" aria-labelledby="model-observability-title">
          <div className="workspace-heading">
            <div>
              <p className="eyebrow">AI model observability (admin only)</p>
              <h2 id="model-observability-title">Model execution dashboard</h2>
              <p className="muted">Telemetry is privacy-safe: no user IDs, document IDs, prompts, secrets, or raw model outputs are stored.</p>
            </div>
          </div>

          {observabilityLoading && <p className="muted" role="status">Loading observability snapshot…</p>}
          {observabilityError && <p className="notice notice--error" role="alert">{observabilityError}</p>}

          <section className="model-observability-snapshot">
            <h3>Snapshot</h3>
            {!observabilitySnapshot && <p className="muted">Loading snapshot…</p>}
            {observabilitySnapshot && <>
              <div className="analytics-stats">
                <div><dt>Total observations</dt><dd>{observabilitySnapshot.totalObservations}</dd></div>
                <div><dt>Success rate</dt><dd>{observabilitySnapshot.successRate === null ? "n/a" : `${Math.round(observabilitySnapshot.successRate * 100)}%`}</dd></div>
                <div><dt>Failure rate</dt><dd>{observabilitySnapshot.failureRate === null ? "n/a" : `${Math.round(observabilitySnapshot.failureRate * 100)}%`}</dd></div>
                <div><dt>Fallbacks</dt><dd>{observabilitySnapshot.fallbackCount}</dd></div>
              </div>
              <div className="model-observability-breakdown">
                <h4>Task types</h4>
                <ul>
                  {Object.entries(observabilitySnapshot.taskCounts).map(([task, count]) => (
                    <li key={task}><strong>{task}</strong> · {count}</li>
                  ))}
                  {Object.keys(observabilitySnapshot.taskCounts).length === 0 && <li className="muted">No observations yet.</li>}
                </ul>
                <h4>Outcomes</h4>
                <ul>
                  {Object.entries(observabilitySnapshot.outcomeCounts).map(([outcome, count]) => (
                    <li key={outcome}><strong>{outcome}</strong> · {count}</li>
                  ))}
                </ul>
              </div>
              <div className="model-observability-latency">
                <h4>Latency (ms)</h4>
                <ul>
                  <li><strong>p50:</strong> {observabilitySnapshot.latencyMs.p50Ms} ms</li>
                  <li><strong>p95:</strong> {observabilitySnapshot.latencyMs.p95Ms} ms</li>
                  <li><strong>max:</strong> {observabilitySnapshot.latencyMs.maxMs} ms</li>
                </ul>
              </div>
              {observabilitySnapshot.recentFailures.length > 0 && (
                <details>
                  <summary>Recent failures</summary>
                  <ul className="evidence-list">
                    {observabilitySnapshot.recentFailures.slice(0, 10).map((failure, index) => (
                      <li key={index}>
                        <span>{failure.timestamp}</span>
                        <span>{failure.taskType} · {failure.outcome}</span>
                        <span>{failure.latencyMs.toFixed(1)} ms · fallback: {String(failure.fallbackOccurred)}</span>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </>}
          </section>

          <section className="model-evaluation-panel">
            <h3>Run quality evaluation</h3>
            <p className="muted">Runs offline against bundled datasets. No external model calls.</p>
            <form className="workspace-form" onSubmit={(event) => { event.preventDefault(); void runEvaluation(); }}>
              <label className="document-control" htmlFor="eval-task-type">
                Task type
                <select
                  id="eval-task-type"
                  value={evalTaskType}
                  onChange={(event) => setEvalTaskType(event.currentTarget.value as "tutor" | "quiz" | "flashcard" | "progress" | "recommend" | "manage")}
                >
                  <option value="tutor">Tutor</option>
                  <option value="quiz">Quiz</option>
                  <option value="flashcard">Flashcard</option>
                  <option value="progress">Progress</option>
                  <option value="recommend">Recommend</option>
                  <option value="manage">Manage</option>
                </select>
              </label>
              <label className="document-control" htmlFor="eval-dataset">
                Dataset
                <select
                  id="eval-dataset"
                  value={evalDataset}
                  onChange={(event) => setEvalDataset(event.currentTarget.value as "retrieval_eval" | "generated" | "quality_v2" | "observability")}
                >
                  <option value="retrieval_eval">Retrieval eval</option>
                  <option value="quality_v2">Quality v2</option>
                  <option value="observability">Observability (runtime telemetry)</option>
                </select>
              </label>
              <label className="document-control" htmlFor="eval-metric">
                Metric
                <select
                  id="eval-metric"
                  value={evalMetric}
                  onChange={(event) => setEvalMetric(event.currentTarget.value as any)}
                >
                  <option value="retrieval_quality">Retrieval quality</option>
                  <option value="citation_validity">Citation validity</option>
                  <option value="evidence_coverage">Evidence coverage</option>
                  <option value="quiz_schema_validity">Quiz schema validity</option>
                  <option value="model_failure_rate">Model failure rate</option>
                  <option value="model_timeout_rate">Model timeout rate</option>
                </select>
              </label>
              {evaluationLoading && <p className="muted" role="status">Running evaluation…</p>}
              {evaluationError && <p className="notice notice--error" role="alert">{evaluationError}</p>}
              {evaluationResult && (
                <details className="validation-results" open>
                  <summary>Evaluation result</summary>
                  <div className="validation-summary">
                    <span><strong>Task:</strong> {evaluationResult.taskType}</span>
                    <span><strong>Dataset:</strong> {evaluationResult.dataset}</span>
                    <span><strong>Metric:</strong> {evaluationResult.metric}</span>
                    <span><strong>Value:</strong> {evaluationResult.value === null ? "n/a" : String(evaluationResult.value)}</span>
                    <span><strong>Evaluated at:</strong> {evaluationResult.evaluatedAt ?? "n/a"}</span>
                  </div>
                  {evaluationResult.details && (
                    <details><summary>Details</summary><pre>{JSON.stringify(evaluationResult.details, null, 2)}</pre></details>
                  )}
                </details>
              )}
              <button type="submit" className="camera-action camera-action--primary" disabled={evaluationLoading}>
                {evaluationLoading ? "Evaluating…" : "Run evaluation"}
              </button>
            </form>
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
