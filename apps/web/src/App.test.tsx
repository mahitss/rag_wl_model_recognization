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
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce(jsonResponse(diagramResponse)).mockResolvedValueOnce(
        jsonResponse(evidenceResponse),
      ),
    );
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
    expect(screen.getByText("biology-notes.pdf")).not.toBeNull();
    expect(screen.getByText("doc_stored_003")).not.toBeNull();
    expect(screen.getByText("chk_stored_007")).not.toBeNull();
    expect(screen.getByText(/page 4/)).not.toBeNull();

    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toBe(
      "/api/v1/diagrams/photosynthesis-cycle/regions/chlorophyll/evidence",
    );
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: "POST" });
  });
});
