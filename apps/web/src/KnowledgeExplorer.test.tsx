/**
 * Deterministic tests for the M54 KnowledgeExplorer component.
 *
 * The IndexedDB-backed offline layer is not involved here; only the SVG
 * graph component and its fetch-based detail loading are exercised.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import KnowledgeExplorer from "./KnowledgeExplorer";

type ConceptNode = {
  conceptId: string;
  name: string;
  aliases: string[];
  evidenceCount: number;
  relationshipCount: number;
};

type ConceptDetail = {
  conceptId: string;
  name: string;
  aliases: string[];
  evidence: Array<{
    chunkId: string;
    documentId: string;
    displayFilename: string;
    pageNumber: number;
    supportingText: string;
  }>;
  relationships: Array<{
    relationshipId: string;
    sourceConceptId: string;
    sourceName: string;
    targetConceptId: string;
    targetName: string;
    relation: string;
    status: "candidate" | "human_verified";
    chunkId: string;
    documentId: string;
    displayFilename: string;
    pageNumber: number;
    supportingText: string;
  }>;
};

const concepts: ConceptNode[] = [
  {
    conceptId: "c1",
    name: "Photosynthesis",
    aliases: [],
    evidenceCount: 2,
    relationshipCount: 1,
  },
  {
    conceptId: "c2",
    name: "Chlorophyll",
    aliases: [],
    evidenceCount: 1,
    relationshipCount: 1,
  },
];

const detail: ConceptDetail = {
  conceptId: "c1",
  name: "Photosynthesis",
  aliases: [],
  evidence: [
    {
      chunkId: "chk1",
      documentId: "doc1",
      displayFilename: "biology.pdf",
      pageNumber: 1,
      supportingText: "Photosynthesis converts light energy into chemical energy.",
    },
  ],
  relationships: [
    {
      relationshipId: "r1",
      sourceConceptId: "c1",
      sourceName: "Photosynthesis",
      targetConceptId: "c2",
      targetName: "Chlorophyll",
      relation: "uses",
      chunkId: "chk1",
      documentId: "doc1",
      displayFilename: "biology.pdf",
      pageNumber: 1,
      supportingText: "Chlorophyll absorbs light.",
      status: "candidate",
    },
  ],
};

const fetchMock = vi.fn(async (_input: RequestInfo | URL) => {
  const url = String(_input);
  if (url.includes("/knowledge-graph/concepts/")) {
    return { ok: true, status: 200, json: async () => detail } as Response;
  }
  return { ok: false, status: 404, json: async () => ({}) } as Response;
});

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockClear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("KnowledgeExplorer", () => {
  it("renders concept nodes", () => {
    render(<KnowledgeExplorer concepts={concepts} />);
    expect(screen.getByText("Photosynthesis")).toBeDefined();
    expect(screen.getByText("Chlorophyll")).toBeDefined();
  });

  it("shows an empty state when there are no concepts", () => {
    render(<KnowledgeExplorer concepts={[]} />);
    expect(screen.getByText(/Select a concept from the graph/)).toBeDefined();
  });

  it("selects a concept and shows its evidence detail", async () => {
    render(<KnowledgeExplorer concepts={concepts} />);
    fireEvent.click(screen.getByText("Photosynthesis"));
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/knowledge-graph/concepts/c1"),
    );
    await screen.findByText("Chlorophyll absorbs light.");
    expect(screen.getByText("Evidence")).toBeDefined();
    expect(screen.getByText("Chlorophyll absorbs light.")).toBeDefined();
  });

  it("filters concepts by search query", () => {
    render(<KnowledgeExplorer concepts={concepts} />);
    const input = screen.getByPlaceholderText("Search concepts…");
    fireEvent.change(input, { target: { value: "Chlorophyll" } });
    expect(screen.getByText("Chlorophyll")).toBeDefined();
    expect(screen.queryByText("Photosynthesis")).toBeNull();
  });

  it("shows relationship status and verification option", async () => {
    render(<KnowledgeExplorer concepts={concepts} />);
    fireEvent.click(screen.getByText("Photosynthesis"));
    await screen.findByText("Relationships");
    expect(screen.getByText("Candidate")).toBeDefined();
    expect(screen.getByText("Mark as human-verified")).toBeDefined();
  });

  it("offers tutor, quiz and notes actions", async () => {
    render(<KnowledgeExplorer concepts={concepts} />);
    fireEvent.click(screen.getByText("Photosynthesis"));
    await screen.findByText("Ask tutor about this concept");
    expect(screen.getByText("Generate quiz for this concept")).toBeDefined();
    expect(screen.getByText("Generate notes for this concept")).toBeDefined();
  });
});
