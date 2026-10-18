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

function installFetchMock(configured = true) {
  const mock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/v1/diagrams") return jsonResponse(diagramResponse);
    if (url === "/api/v1/documents") {
      return jsonResponse({ documents: [{ documentId: "doc_stored_003", displayFilename: "biology-notes.pdf" }] });
    }
    if (url === "/api/v1/tutor/status") {
      return jsonResponse({ configured, modelId: configured ? "local-model" : null });
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
});
