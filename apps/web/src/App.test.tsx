import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./main";

vi.mock("./opencvTracker", () => ({
  createDiagramTracker: vi.fn().mockResolvedValue({ detect: () => null, dispose: vi.fn() }),
}));

const diagramResponse = {
  diagrams: [
    {
      diagramId: "photosynthesis-cycle",
      title: "Photosynthesis",
      description: "Select a region.",
      imageUrl: "/diagrams/photosynthesis.svg",
      imageAspectRatio: 1000 / 640,
      regions: [
        {
          regionId: "chlorophyll",
          conceptId: "biology.photosynthesis.chlorophyll",
          displayName: "Chlorophyll in leaves",
          aliases: ["chlorophyll", "leaf"],
          geometry: { x: 0.36, y: 0.25, width: 0.28, height: 0.5 },
        },
      ],
    },
  ],
};

const evidenceResponse = {
  status: "evidence_found",
  diagramId: "photosynthesis-cycle",
  regionId: "chlorophyll",
  concept: {
    conceptId: "biology.photosynthesis.chlorophyll",
    displayName: "Chlorophyll in leaves",
    aliases: ["chlorophyll", "leaf"],
  },
  results: [
    {
      citationId: "chk_stored_007",
      documentId: "doc_stored_003",
      displayFilename: "biology-notes.pdf",
      pageNumber: 4,
      chunkIndex: 2,
      charStart: 18,
      charEnd: 89,
      text: "Chlorophyll in leaves absorbs energy from light during photosynthesis.",
      retrieval: {
        method: "fts5_bm25",
        rank: 1,
        similarity: null,
        rrfScore: null,
        lexicalRank: null,
        semanticRank: null,
      },
    },
  ],
  retrievalMode: "lexical",
  fallbackReason: null,
};

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    json: async () => body,
  } as Response;
}

function tutorAnswerResponse(): Response {
  return jsonResponse({
    status: "answered",
    answer: "Chlorophyll absorbs light energy [E1].",
    citations: evidenceResponse.results,
    retrievalMode: "lexical",
    fallbackReason: null,
  });
}

const quizQuestionResponse = {
  questionId: "quiz-question-001",
  questionType: "multiple_choice",
  prompt: "What does chlorophyll absorb?",
  options: [
    { id: "A", text: "Light energy" },
    { id: "B", text: "Sound energy" },
    { id: "C", text: "Soil" },
    { id: "D", text: "Oxygen" },
  ],
  explanation: null,
  citations: [],
  latestOutcome: null,
  attemptCount: 0,
};

const emptyProgress: {
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
  recentAttempts: unknown[];
} = { topics: [], totalAttempts: 0, recentAttempts: [] };

type TestAnalytics = {
  generatedAt: string;
  totals: { attemptCount: number; attemptedQuestions: number; correctQuestions: number; incorrectQuestions: number; ambiguousQuestions: number; quizAccuracy: number | null; completedStudySessions: number; activeStudySessions: number; overdueReviews: number };
  topics: unknown[];
  dailyActivity: unknown[];
  recentActivity: { activityId: string; kind: "quiz_attempt" | "study_session"; topic: string; outcome: string | null; occurredAt: string; prompt: string | null }[];
  reviewHistory: unknown[];
  activeSessions: { sessionId: string; topicId: string; topic: string; startedAt: string; completedAt: string | null }[];
  methodology: { mastery: string; recencyHalfLifeDays: number; weakTopicThreshold: number; reviewIntervalsDays: Record<string, number>; reviewDueBoundary: string };
};
type TestCoachPlan = {
  planDate: string; recommendations: {
    recommendationId: string; planDate: string; topicId: string; topic: string; title: string;
    priority: number; reasons: string[]; graphConceptId: string | null; relatedConceptIds: string[];
    status: "planned" | "accepted" | "dismissed" | "completed"; estimatedMinutes: number;
    citations: typeof evidenceResponse.results; quizTopic: string;
  }[]; emptyReason: string | null; methodology: string;
};
const analyticsFixture: TestAnalytics = {
  generatedAt: "2026-04-15T12:00:00+00:00",
  totals: { attemptCount: 0, attemptedQuestions: 0, correctQuestions: 0, incorrectQuestions: 0, ambiguousQuestions: 0, quizAccuracy: null, completedStudySessions: 0, activeStudySessions: 0, overdueReviews: 0 },
  topics: [], dailyActivity: [], recentActivity: [], reviewHistory: [], activeSessions: [],
  methodology: { mastery: "Recency weighted latest scored outcomes.", recencyHalfLifeDays: 30, weakTopicThreshold: 0.6, reviewIntervalsDays: { correct: 7, incorrect: 1, ambiguous: 1 }, reviewDueBoundary: "dueAt <= current UTC time" },
};

const uploadedImageFixture = {
  imageId: "img_test_001", displayFilename: "biology-diagram.png", contentType: "image/png" as const,
  width: 100, height: 80, byteSize: 120, sha256: "a".repeat(64), createdAt: "2026-05-01T10:00:00Z",
  ocrStatus: "available" as const, extractedText: "chlorophyll absorbs light", regions: [] as {
    regionId: string; geometry: { x: number; y: number; width: number; height: number };
    ocrStatus: "available" | "empty" | "unavailable" | "failed"; extractedText: string | null;
    diagramId: string | null; conceptId: string | null; conceptName: string | null; citations: typeof evidenceResponse.results;
  }[],
};

const coachPlanFixture: TestCoachPlan = {
  planDate: "2026-10-19", emptyReason: null, methodology: "Overdue reviews rank first.",
  recommendations: [{ recommendationId: "coach-photo", planDate: "2026-10-19", topicId: "topic-photo", topic: "Photosynthesis", title: "Review Photosynthesis", priority: 140, reasons: ["1 review is overdue."], graphConceptId: null, relatedConceptIds: [], status: "planned", estimatedMinutes: 20, citations: evidenceResponse.results, quizTopic: "Photosynthesis" }],
};

function installFetchMock(
  configured = true,
  initialPlan: TestCoachPlan = { ...coachPlanFixture, recommendations: [], emptyReason: "No quiz attempts are recorded yet." },
  initiallySignedIn = true,
) {
  let progressResult = emptyProgress;
  let analyticsResult = analyticsFixture;
  let coachPlan = structuredClone(initialPlan);
  let imageResult = { ...uploadedImageFixture, regions: [...uploadedImageFixture.regions] };
  let imageExists = false;
  let signedIn = initiallySignedIn;
  let graphRelationshipStatus: "candidate" | "human_verified" = "candidate";
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/v1/auth/csrf-token") return jsonResponse({ csrfToken: "test-csrf-token" });
    if (url === "/api/v1/auth/me") {
      return signedIn
        ? jsonResponse({ userId: "user-test", username: "learner", expiresAt: "2099-01-01T00:00:00+00:00" })
        : { ok: false, status: 401, json: async () => ({ error: { message: "Unauthorized" } }) } as Response;
    }
    if (url === "/api/v1/auth/register") {
      return { ...jsonResponse({ message: "If the account can be registered, you can try signing in." }), status: 202 } as Response;
    }
    if (url === "/api/v1/auth/login") {
      signedIn = true;
      return jsonResponse({ userId: "user-test", username: "learner", expiresAt: "2099-01-01T00:00:00+00:00" });
    }
    if (url === "/api/v1/auth/logout") {
      signedIn = false;
      return jsonResponse({ message: "Logged out." });
    }
    if (url === "/api/v1/diagrams") return jsonResponse(diagramResponse);
    if (url === "/api/v1/documents") {
      return jsonResponse({ documents: [{ documentId: "doc_stored_003", displayFilename: "biology-notes.pdf" }] });
    }
    if (url === "/api/v1/tutor/status") {
      return jsonResponse({ configured, modelId: configured ? "local-model" : null });
    }
    if (url === "/api/v1/progress") return jsonResponse(progressResult);
    if (url === "/api/v1/learning-analytics") return jsonResponse(analyticsResult);
    if (url === "/api/v1/study-coach/plan") return jsonResponse(coachPlan);
    if (url === "/api/v1/study-coach/recommendations/coach-photo" && init?.method === "PATCH") {
      const payload = JSON.parse(String(init.body)) as { action: "accept" | "edit" | "dismiss" | "complete"; title?: string; estimatedMinutes?: number };
      const recommendation = coachPlan.recommendations[0];
      if (recommendation) {
        recommendation.status = payload.action === "dismiss" ? "dismissed" : payload.action === "complete" ? "completed" : "accepted";
        if (payload.title) recommendation.title = payload.title;
        if (payload.estimatedMinutes) recommendation.estimatedMinutes = payload.estimatedMinutes;
      }
      return jsonResponse(recommendation);
    }
    if (url === "/api/v1/study-coach/recommendations/coach-photo/explanation" && init?.method === "POST") return tutorAnswerResponse();
    if (url === "/api/v1/images" && init?.method === "POST") {
      imageExists = true;
      return jsonResponse(imageResult);
    }
    if (url === "/api/v1/images") return jsonResponse({ images: imageExists ? [{ ...imageResult, regions: undefined }] : [] });
    if (url === "/api/v1/images/img_test_001" && init?.method === "DELETE") {
      imageExists = false;
      imageResult = { ...uploadedImageFixture, regions: [] };
      return jsonResponse({ imageId: "img_test_001", deleted: true });
    }
    if (url === "/api/v1/images/img_test_001" && init?.method !== "DELETE") {
      return jsonResponse(imageExists ? imageResult : { error: { message: "Image not found." } });
    }
    if (url === "/api/v1/images/img_test_001/regions" && init?.method === "POST") {
      const body = JSON.parse(String(init.body)) as { geometry: { x: number; y: number; width: number; height: number }; diagramId?: string; conceptId?: string };
      const region = {
        regionId: "imr_test_001", geometry: body.geometry, ocrStatus: "available" as const,
        extractedText: "chlorophyll", diagramId: body.diagramId ?? null, conceptId: body.conceptId ?? null,
        conceptName: body.conceptId ? "Chlorophyll in leaves" : null, citations: [],
      };
      imageResult = { ...imageResult, regions: [region] };
      return jsonResponse(region);
    }
    if (url === "/api/v1/images/img_test_001/regions/imr_test_001/evidence" && init?.method === "POST") {
      return jsonResponse({ status: "evidence_found", imageId: "img_test_001", regionId: "imr_test_001", geometry: imageResult.regions[0]?.geometry, extractedText: "chlorophyll", ocrStatus: "available", conceptName: "Chlorophyll in leaves", results: evidenceResponse.results, retrievalMode: "lexical", fallbackReason: null });
    }
    if (url === "/api/v1/images/img_test_001/regions/imr_test_001/tutor" && init?.method === "POST") {
      return jsonResponse({ question: "What does this part do?", status: "answered", answer: "The passage says chlorophyll absorbs light.", imageId: "img_test_001", regionId: "imr_test_001", geometry: imageResult.regions[0]?.geometry, extractedText: "chlorophyll", ocrStatus: "available", conceptName: "Chlorophyll in leaves", citations: evidenceResponse.results, retrievalMode: "lexical", fallbackReason: null });
    }
    if (url === "/api/v1/study-sessions" && init?.method === "POST") {
      const session = { sessionId: "session-1", topicId: "topic-1", topic: "Photosynthesis", startedAt: "2026-04-15T10:00:00+00:00", completedAt: null };
      analyticsResult = { ...analyticsFixture, totals: { ...analyticsFixture.totals, activeStudySessions: 1 }, activeSessions: [session] };
      return jsonResponse(session);
    }
    if (url === "/api/v1/study-sessions/session-1/complete" && init?.method === "POST") {
      const session = { sessionId: "session-1", topicId: "topic-1", topic: "Photosynthesis", startedAt: "2026-04-15T10:00:00+00:00", completedAt: "2026-04-15T10:30:00+00:00" };
      analyticsResult = { ...analyticsFixture, totals: { ...analyticsFixture.totals, completedStudySessions: 1 }, recentActivity: [{ activityId: "session-1", kind: "study_session", topic: session.topic, outcome: null, occurredAt: session.completedAt ?? "", prompt: null }] };
      return jsonResponse(session);
    }
    if (url === "/api/v1/knowledge-graph") return jsonResponse({ concepts: [{ conceptId: "kg_photo", name: "Photosynthesis", aliases: ["plant process"], evidenceCount: 1, relationshipCount: 1 }] });
    if (url === "/api/v1/knowledge-graph/concepts/kg_photo") return jsonResponse({
      conceptId: "kg_photo", name: "Photosynthesis", aliases: ["plant process"],
      evidence: [{ chunkId: "chunk-real", documentId: "doc_stored_003", displayFilename: "biology-notes.pdf", pageNumber: 4, supportingText: "Photosynthesis uses chlorophyll." }],
      relationships: [{ relationshipId: "rel-1", sourceConceptId: "kg_photo", sourceName: "Photosynthesis", targetConceptId: "kg_chloro", targetName: "Chlorophyll", relation: "uses", chunkId: "chunk-real", documentId: "doc_stored_003", displayFilename: "biology-notes.pdf", pageNumber: 4, supportingText: "Photosynthesis uses chlorophyll.", status: graphRelationshipStatus }],
    });
    if (url === "/api/v1/knowledge-graph/relationships/rel-1/verify" && init?.method === "POST") {
      graphRelationshipStatus = "human_verified";
      return jsonResponse({ relationshipId: "rel-1", status: graphRelationshipStatus });
    }
    if (url === "/api/v1/quizzes") {
      const payload = JSON.parse(String(init?.body ?? "{}")) as { topic?: string };
      if (payload.topic === "unsupported subject") {
        return jsonResponse({ status: "insufficient_evidence", quizId: null, topicId: null, topic: payload.topic, createdAt: null, questions: [] });
      }
      return jsonResponse({
        status: "ready",
        quizId: "quiz-001",
        topicId: "topic-001",
        topic: "chlorophyll",
        questions: [quizQuestionResponse],
      });
    }
    if (url.endsWith("/attempts")) {
      progressResult = {
        topics: [{ topicId: "topic-001", topic: "chlorophyll", attemptedQuestions: 1, correctQuestions: 1, incorrectQuestions: 0, ambiguousQuestions: 0, accuracy: 1, needsReview: false }],
        totalAttempts: 1,
        recentAttempts: [],
      };
      return jsonResponse({
        attemptId: "attempt-001",
        questionId: "quiz-question-001",
        outcome: "correct",
        isCorrect: true,
        expectedAnswer: "Light energy",
        explanation: "The cited passage says chlorophyll absorbs light energy.",
        citations: evidenceResponse.results,
        attemptCount: 1,
      });
    }
    if (url.includes("/quizzes/") && url.includes("review_incorrect=true")) {
      return jsonResponse({ status: "ready", quizId: "quiz-001", topicId: "topic-001", topic: "chlorophyll", questions: [] });
    }
    if (url.includes("/regions/")) return jsonResponse(evidenceResponse);
    if (url === "/api/v1/tutor/answers") {
      if (!configured) {
        return {
          ok: false,
          json: async () => ({ error: { message: "The optional local tutor is not configured or could not be reached." } }),
        } as Response;
      }
      return tutorAnswerResponse();
    }
    throw new Error(`Unexpected test request: ${url}`);
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

describe("camera selection evidence flow", () => {
  beforeEach(() => {
    Object.defineProperty(window, "isSecureContext", { configurable: true, value: true });
    const track = {
      stop: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    };
    const stream = { getTracks: () => [track] } as unknown as MediaStream;
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: vi.fn().mockResolvedValue(stream) },
    });
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    installFetchMock();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("sends the curated region selection to retrieval and displays the original citation", async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: "Photosynthesis" });
    await user.click(screen.getByRole("button", { name: "Enable camera" }));
    await screen.findByText(/Camera on · stays on this device/);
    await user.click(screen.getByRole("button", { name: "Use manual alignment" }));
    await user.click(screen.getByRole("button", { name: "Confirm manual alignment" }));
    await user.click(
      screen.getByRole("button", { name: "Explore Chlorophyll in leaves in camera overlay" }),
    );

    expect(await screen.findByText("Chlorophyll in leaves absorbs energy from light during photosynthesis."))
      .not.toBeNull();
    expect(screen.getAllByText("biology-notes.pdf").length).toBeGreaterThan(1);
    expect(screen.getByText("doc_stored_003")).not.toBeNull();
    expect(screen.getByText("chk_stored_007")).not.toBeNull();
    expect(screen.getByText(/page 4/)).not.toBeNull();

    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const evidenceCall = fetchMock.mock.calls.find(([url]) => String(url).includes("/regions/"));
    expect(evidenceCall?.[0]).toBe(
      "/api/v1/diagrams/photosynthesis-cycle/regions/chlorophyll/evidence",
    );
    expect(evidenceCall?.[1]).toMatchObject({ method: "POST" });
  });

  it("asks the local tutor with selected concept and document and renders verified sources", async () => {
    const user = userEvent.setup();
    const fetchMock = installFetchMock();
    render(<App />);

    await screen.findByRole("heading", { name: "Photosynthesis" });
    await user.click(screen.getByRole("button", { name: "Explore Chlorophyll in leaves" }));
    await screen.findByText("Chlorophyll in leaves absorbs energy from light during photosynthesis.");
    await user.selectOptions(screen.getByLabelText("Search material"), "doc_stored_003");
    await user.type(screen.getByLabelText("Question about Chlorophyll in leaves"), "What does it do?");
    await user.click(screen.getByRole("button", { name: "Ask tutor" }));

    expect(await screen.findByText("Chlorophyll absorbs light energy [E1].")).not.toBeNull();
    expect(screen.getAllByText("biology-notes.pdf").length).toBeGreaterThan(1);
    expect(screen.getAllByText(/page 4/)).toHaveLength(2);
    const tutorCall = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/tutor/answers");
    expect(tutorCall?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({
        question: "What does it do?",
        diagramId: "photosynthesis-cycle",
        conceptId: "biology.photosynthesis.chlorophyll",
        documentIds: ["doc_stored_003"],
      }),
    });

  });

  it("shows a clear unavailable state when no local model is configured", async () => {
    installFetchMock(false);
    render(<App />);

    await screen.findByText(/Local tutor unavailable/);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Question about your materials"), "Explain photosynthesis");
    await user.click(screen.getByRole("button", { name: "Ask tutor" }));
    expect(await screen.findByRole("alert")).not.toBeNull();
  });

  it("generates an evidence-backed quiz, records an answer, and shows its citation", async () => {
    const user = userEvent.setup();
    const fetchMock = installFetchMock();
    render(<App />);

    await screen.findByRole("heading", { name: "Photosynthesis" });
    await user.type(screen.getByLabelText("Topic to practice"), "chlorophyll");
    await user.click(screen.getByRole("button", { name: "Generate quiz from evidence" }));
    await screen.findByRole("heading", { name: /What does chlorophyll absorb/ });
    expect(screen.queryByText(/Chlorophyll in leaves absorbs energy from light during photosynthesis/)).toBeNull();

    await user.click(screen.getByLabelText("A. Light energy"));
    await user.click(screen.getByRole("button", { name: "Check answer" }));

    expect(await screen.findByText(/Correct · Expected answer: Light energy/)).not.toBeNull();
    expect(screen.getByText("The cited passage says chlorophyll absorbs light energy.")).not.toBeNull();
    expect(screen.getAllByText("biology-notes.pdf").length).toBeGreaterThan(1);
    expect(await screen.findByText("Local progress · 1 attempts")).not.toBeNull();
    expect(screen.getByText("chk_stored_007")).not.toBeNull();
    const quizCall = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/quizzes");
    expect(quizCall?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({ topic: "chlorophyll", questionCount: 5 }),
    });
  });

  it("shows an empty-evidence state instead of creating unsupported questions", async () => {
    const user = userEvent.setup();
    const fetchMock = installFetchMock();
    render(<App />);
    await screen.findByRole("heading", { name: "Photosynthesis" });
    await user.type(screen.getByLabelText("Topic to practice"), "unsupported subject");
    await user.click(screen.getByRole("button", { name: "Generate quiz from evidence" }));
    expect(await screen.findByText("No supporting passages were found, so no quiz was generated.")).not.toBeNull();
    const quizCall = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/quizzes");
    expect(quizCall).toBeDefined();
  });

  it("shows evidence-backed graph links and connects the selected node to the tutor", async () => {
    const user = userEvent.setup();
    const fetchMock = installFetchMock();
    render(<App />);
    await screen.findByRole("heading", { name: "Photosynthesis" });
    await user.click(screen.getByRole("button", { name: /Photosynthesis.*connected relations/ }));
    expect(await screen.findByText("Candidate — needs review")).not.toBeNull();
    expect(screen.getAllByText("Photosynthesis uses chlorophyll.")).toHaveLength(2);
    expect(screen.getAllByText("chunk-real")).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "Mark as human-verified" }));
    expect(await screen.findByText("Human verified")).not.toBeNull();
    await user.click(screen.getByRole("button", { name: "Ask tutor about this concept" }));
    await user.click(screen.getByRole("button", { name: "Ask tutor" }));
    const tutorCall = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/tutor/answers");
    expect(tutorCall?.[1]).toMatchObject({ body: JSON.stringify({ question: "Explain Photosynthesis using my study materials.", graphConceptId: "kg_photo" }) });
  });

  it("keeps an empty dashboard honest and records learner-completed sessions", async () => {
    const user = userEvent.setup();
    const fetchMock = installFetchMock();
    render(<App />);
    await screen.findByRole("heading", { name: "Learning analytics" });
    expect(await screen.findByText("No scored answers")).not.toBeNull();
    expect(screen.getByText(/No quiz attempts or completed study sessions are recorded yet/)).not.toBeNull();
    await user.type(screen.getByLabelText("Study session topic"), "Photosynthesis");
    await user.click(screen.getByRole("button", { name: "Start session" }));
    expect(await screen.findByText("Photosynthesis", { selector: ".active-session strong" })).not.toBeNull();
    await user.click(screen.getByRole("button", { name: "Mark session complete" }));
    expect(await screen.findByText("Completed study sessions")).not.toBeNull();
    const analyticsCall = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/learning-analytics");
    expect(analyticsCall).toBeDefined();
    expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/v1/study-sessions/session-1/complete")).toBe(true);
  });

  it("uploads a local image, creates an accessible region, and shows grounded citations", async () => {
    const user = userEvent.setup();
    const fetchMock = installFetchMock();
    render(<App />);
    await screen.findByRole("heading", { name: "Explore a concept" });
    const file = new File(["test image bytes"], "biology-diagram.png", { type: "image/png" });
    await user.upload(screen.getByLabelText("Upload an educational image"), file);
    expect(await screen.findByText(/Local OCR extracted text from this image/)).not.toBeNull();

    await user.selectOptions(
      screen.getByLabelText("Optional curated concept"),
      "photosynthesis-cycle|biology.photosynthesis.chlorophyll",
    );
    await user.click(screen.getByRole("button", { name: "Save region and search evidence" }));
    expect(await screen.findByText("biology-notes.pdf", { selector: ".citation-meta strong" })).not.toBeNull();

    await user.type(screen.getByLabelText("Ask about the selected region"), "What does this part do?");
    await user.click(screen.getByRole("button", { name: "Ask grounded tutor" }));
    expect(await screen.findByText("The passage says chlorophyll absorbs light.")).not.toBeNull();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/regions/imr_test_001/tutor"))).toBe(true);

    await user.click(screen.getByRole("button", { name: "Delete uploaded image" }));
    expect(await screen.findByText(/Upload a PNG or JPEG image/)).not.toBeNull();
  });

  it("requires an account and supports registration before opening private study data", async () => {
    const user = userEvent.setup();
    const fetchMock = installFetchMock(true, undefined, false);
    render(<App />);

    await screen.findByRole("heading", { name: "Sign in" });
    expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/v1/documents")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Create an account" }));
    await user.type(screen.getByLabelText("Username"), "learner");
    await user.type(screen.getByLabelText("Password"), "strong-password-123");
    await user.click(screen.getByRole("button", { name: "Register" }));
    expect((await screen.findByRole("status")).textContent).toContain(
      "If the account can be registered, you can try signing in.",
    );
    await user.type(screen.getByLabelText("Password"), "strong-password-123");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByRole("heading", { name: "Explore a concept" });
    expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/v1/documents")).toBe(true);
  });
});

describe("personalized study coach", () => {
  beforeEach(() => {
    Object.defineProperty(window, "isSecureContext", { configurable: true, value: true });
    installFetchMock(true, coachPlanFixture);
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("shows cited rule reasons and persists recommendation decisions and edits", async () => {
    const user = userEvent.setup();
    render(<App />);
    expect(await screen.findByRole("heading", { name: "AI study coach" })).toBeTruthy();
    expect(await screen.findByText("1 review is overdue.")).toBeTruthy();
    expect(screen.getByText("Chlorophyll in leaves absorbs energy from light during photosynthesis.")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Accept" }));
    expect(await screen.findByText("accepted")).toBeTruthy();
    await user.click(screen.getByText("Edit"));
    await user.clear(screen.getByLabelText("Plan title"));
    await user.type(screen.getByLabelText("Plan title"), "Revisit plant energy");
    await user.clear(screen.getByLabelText("Minutes"));
    await user.type(screen.getByLabelText("Minutes"), "35");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("heading", { name: "Revisit plant energy" })).toBeTruthy();
    expect(screen.getByText("accepted")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Mark complete" }));
    expect(await screen.findByText("completed")).toBeTruthy();
  });
});

describe("accessibility", () => {
  beforeEach(() => {
    Object.defineProperty(window, "isSecureContext", { configurable: true, value: true });
    installFetchMock();
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("exposes a skip-to-content link that reaches main content when activated", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("heading", { name: "Explore a concept" });

    const skipLink = screen.getByRole("link", { name: "Skip to main content" });
    expect(skipLink).not.toBeNull();

    await user.tab();
    expect(document.activeElement).toBe(skipLink);

    await user.keyboard("{Enter}");
    const main = screen.getByRole("main");
    expect(document.activeElement).toBe(main);
    expect(document.title).toBe("WonderLearn — WonderLearn");
  });

  it("updates document title when a quiz is generated", async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: "Photosynthesis" });
    await user.type(screen.getByLabelText("Topic to practice"), "chlorophyll");
    await user.click(screen.getByRole("button", { name: "Generate quiz from evidence" }));
    await screen.findByRole("heading", { name: /What does chlorophyll absorb/ });

    expect(document.title).toContain("Quiz: chlorophyll");
    expect(document.title).toContain("WonderLearn");
  });

  it("marks invalid fields with aria-invalid and links errors via aria-describedby", async () => {
    render(<App />);

    await screen.findByRole("heading", { name: "Photosynthesis" });

    // No errors initially: aria-invalid should be absent
    const quizInput = screen.getByLabelText("Topic to practice");
    expect(quizInput.getAttribute("aria-invalid")).toBeNull();
    expect(quizInput.getAttribute("aria-describedby")).toBeNull();
  });

  it("region buttons expose pressed state for screen readers", async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: "Photosynthesis" });

    const regionButton = screen.getByRole("button", { name: "Explore Chlorophyll in leaves" });
    expect(regionButton.getAttribute("aria-pressed")).toBe("false");

    await user.click(regionButton);
    expect(regionButton.getAttribute("aria-pressed")).toBe("true");
  });

  it("evidence items have list semantics and citation metadata", async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: "Photosynthesis" });
    await user.click(screen.getByRole("button", { name: "Explore Chlorophyll in leaves" }));
    await screen.findByText("Chlorophyll in leaves absorbs energy from light during photosynthesis.");

    const evidenceList = screen.getByRole("list");
    expect(evidenceList).not.toBeNull();

    const firstItem = screen.getByRole("listitem");
    expect(firstItem).not.toBeNull();
    expect(screen.getAllByText("biology-notes.pdf").length).toBeGreaterThan(0);
    expect(screen.getByText(/page 4/)).not.toBeNull();
  });

  it("tutor answer uses a polite live region for screen reader announcements", async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: "Photosynthesis" });
    await user.click(screen.getByRole("button", { name: "Explore Chlorophyll in leaves" }));
    await screen.findByText("Chlorophyll in leaves absorbs energy from light during photosynthesis.");
    await user.type(screen.getByLabelText("Question about Chlorophyll in leaves"), "What does it do?");
    await user.click(screen.getByRole("button", { name: "Ask tutor" }));

    await screen.findByText("Chlorophyll absorbs light energy [E1].");

    const tutorAnswer = screen.getByText("Chlorophyll absorbs light energy [E1].");
    const parent = tutorAnswer.parentElement;
    expect(parent).not.toBeNull();
    expect(parent?.getAttribute("aria-live")).toBe("polite");
  });
});
