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

function installFetchMock(configured = true) {
  let progressResult = emptyProgress;
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/v1/diagrams") return jsonResponse(diagramResponse);
    if (url === "/api/v1/documents") {
      return jsonResponse({ documents: [{ documentId: "doc_stored_003", displayFilename: "biology-notes.pdf" }] });
    }
    if (url === "/api/v1/tutor/status") {
      return jsonResponse({ configured, modelId: configured ? "local-model" : null });
    }
    if (url === "/api/v1/progress") return jsonResponse(progressResult);
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
});
