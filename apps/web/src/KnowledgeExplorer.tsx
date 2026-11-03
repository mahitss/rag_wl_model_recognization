/**
 * Interactive visual knowledge explorer.
 *
 * Renders learner concepts and their evidence-backed relationships as an
 * interactive force-directed SVG graph. Every concept and relationship comes
 * from the WonderLearn knowledge-graph API (already user-scoped and
 * evidence-grounded); this component only visualizes and navigates it.
 *
 * No external visualization libraries are used: layout, zoom/pan and
 * rendering are plain React + SVG so the component works with the repo's
 * existing dependency set.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type GraphConceptNode = {
  conceptId: string;
  name: string;
  aliases: string[];
  evidenceCount: number;
  relationshipCount: number;
};

type GraphRelationship = {
  relationshipId: string;
  sourceConceptId: string;
  sourceName: string;
  targetConceptId: string;
  targetName: string;
  relation: string;
  chunkId: string;
  documentId: string;
  displayFilename: string;
  pageNumber: number;
  supportingText: string;
  status: "candidate" | "human_verified";
};

interface ConceptDetail {
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
  relationships: GraphRelationship[];
}

interface KnowledgeExplorerProps {
  concepts: GraphConceptNode[];
  onConceptSelect?: (conceptId: string) => void;
  onAskTutor?: (conceptId: string, name: string) => void;
  onGenerateQuiz?: (topic: string) => void;
  onGenerateNotes?: (topic: string) => void;
  onVerifyRelationship?: (relationshipId: string) => void;
  onOpenDocument?: (documentId: string) => void;
}

type Transform = { x: number; y: number; k: number };
type Point = { x: number; y: number };

const NODE_RADIUS = 22;
const MIN_SCALE = 0.4;
const MAX_SCALE = 2;
const FORCE_ITERATIONS = 40;
const REPEL_STRENGTH = 400;
const ATTRACT_STRENGTH = 0.06;
const CENTER_STRENGTH = 0.02;

function distance(a: Point, b: Point) {
  const dx = a.x - b.x;
  const dy = a.y - b.y;
  return Math.sqrt(dx * dx + dy * dy) || 1;
}

export default function KnowledgeExplorer({
  concepts,
  onConceptSelect,
  onAskTutor,
  onGenerateQuiz,
  onGenerateNotes,
  onVerifyRelationship,
  onOpenDocument,
}: KnowledgeExplorerProps) {
  const [selectedConceptId, setSelectedConceptId] = useState<string | null>(null);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [details, setDetails] = useState<Map<string, ConceptDetail>>(new Map());
  const [searchQuery, setSearchQuery] = useState("");
  const [transform, setTransform] = useState<Transform>({ x: 0, y: 0, k: 1 });
  const [positions, setPositions] = useState<Map<string, Point>>(new Map());
  const [detailError, setDetailError] = useState<string | null>(null);

  const svgRef = useRef<SVGSVGElement>(null);
  const isDraggingRef = useRef(false);
  const dragStartRef = useRef({ x: 0, y: 0 });
  const transformStartRef = useRef({ x: 0, y: 0 });

  // Layout: deterministic circular seed + repulsion/attraction force passes.
  useEffect(() => {
    const next = new Map<string, Point>();
    const n = concepts.length;
    const radius = Math.max(260, Math.min(700, n * 55));
    concepts.forEach((concept, index) => {
      const angle = (index / Math.max(n, 1)) * 2 * Math.PI;
      next.set(concept.conceptId, {
        x: radius * Math.cos(angle),
        y: radius * Math.sin(angle),
      });
    });

    const edges: Array<{ source: string; target: string }> = [];
    const expanded = Array.from(expandedIds);
    for (const id of expanded) {
      const detail = details.get(id);
      if (!detail) continue;
      for (const rel of detail.relationships) {
        edges.push({ source: id, target: rel.targetConceptId });
        edges.push({ source: id, target: rel.sourceConceptId });
      }
    }

    let positions = new Map(next);
    for (let iter = 0; iter < FORCE_ITERATIONS; iter++) {
      const pos = new Map(positions);
      const ids = Array.from(positions.keys());
      for (let i = 0; i < ids.length; i++) {
        for (let j = i + 1; j < ids.length; j++) {
          const a = pos.get(ids[i])!;
          const b = pos.get(ids[j])!;
          const d = distance(a, b);
          const force = Math.min(REPEL_STRENGTH / d, 0.5);
          const dx = ((a.x - b.x) / d) * force;
          const dy = ((a.y - b.y) / d) * force;
          pos.set(ids[i], { x: a.x + dx, y: a.y + dy });
          pos.set(ids[j], { x: b.x - dx, y: b.y - dy });
        }
      }
      for (const edge of edges) {
        const a = pos.get(edge.source);
        const b = pos.get(edge.target);
        if (!a || !b) continue;
        const d = distance(a, b);
        const force = (d * ATTRACT_STRENGTH) / 2;
        const dx = ((b.x - a.x) / d) * force;
        const dy = ((b.y - a.y) / d) * force;
        pos.set(edge.source, { x: a.x + dx, y: a.y + dy });
        pos.set(edge.target, { x: b.x - dx, y: b.y - dy });
      }
      for (const [id, p] of pos) {
        pos.set(id, { x: p.x * CENTER_STRENGTH, y: p.y * CENTER_STRENGTH });
      }
      positions = pos;
    }
    setPositions(positions);
  }, [concepts, expandedIds, details]);

  const loadConceptDetail = useCallback(
    async (conceptId: string) => {
      if (expandedIds.has(conceptId)) {
        setSelectedConceptId(conceptId);
        onConceptSelect?.(conceptId);
        return;
      }
      setDetailError(null);
      try {
        const response = await fetch(
          `/api/v1/knowledge-graph/concepts/${encodeURIComponent(conceptId)}`,
        );
        const detail = (await response.json()) as ConceptDetail;
        if (!response.ok) {
          throw new Error(
            (detail as { error?: { message?: string } })?.error?.message ||
              `Request failed (${response.status}).`,
          );
        }
        setDetails((prev) => new Map(prev).set(conceptId, detail));
        setExpandedIds((prev) => new Set(prev).add(conceptId));
        setSelectedConceptId(conceptId);
        onConceptSelect?.(conceptId);
      } catch (error) {
        setDetailError(error instanceof Error ? error.message : "Could not load concept detail.");
      }
    },
    [expandedIds, onConceptSelect],
  );

  const matchingConcepts = useMemo(
    () =>
      concepts.filter(
        (c) =>
          c.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
          c.aliases.some((a) => a.toLowerCase().includes(searchQuery.toLowerCase())),
      ),
    [concepts, searchQuery],
  );

  const selectedDetail = selectedConceptId ? details.get(selectedConceptId) : null;

  const onWheel = useCallback(
    (event: React.WheelEvent<SVGSVGElement>) => {
      event.preventDefault();
      const svg = svgRef.current;
      if (!svg) return;
      const ctm = svg.getScreenCTM();
      if (!ctm) return;
      const point = svg.createSVGPoint();
      point.x = event.clientX;
      point.y = event.clientY;
      const svgPoint = point.matrixTransform(ctm.inverse());
      const delta = event.deltaY > 0 ? 0.9 : 1.1;
      const nextK = Math.min(MAX_SCALE, Math.max(MIN_SCALE, transform.k * delta));
      setTransform((prev) => ({
        x: svgPoint.x - (svgPoint.x - prev.x) * (nextK / prev.k),
        y: svgPoint.y - (svgPoint.y - prev.y) * (nextK / prev.k),
        k: nextK,
      }));
    },
    [transform],
  );

  const onMouseDown = useCallback(
    (event: React.MouseEvent<SVGSVGElement>) => {
      if (event.target !== svgRef.current) return;
      isDraggingRef.current = true;
      dragStartRef.current = { x: event.clientX, y: event.clientY };
      transformStartRef.current = { x: transform.x, y: transform.y };
    },
    [transform],
  );

  const onMouseMove = useCallback(
    (event: React.MouseEvent) => {
      if (!isDraggingRef.current) return;
      setTransform((prev) => ({
        x: transformStartRef.current.x + (event.clientX - dragStartRef.current.x),
        y: transformStartRef.current.y + (event.clientY - dragStartRef.current.y),
        k: prev.k,
      }));
    },
    [],
  );

  const onMouseUp = useCallback(() => {
    isDraggingRef.current = false;
  }, []);

  const resetView = useCallback(() => {
    setTransform({ x: 0, y: 0, k: 1 });
  }, []);

  const clearSelection = useCallback(() => {
    setSelectedConceptId(null);
    setDetailError(null);
  }, []);

  return (
    <section className="knowledge-explorer" aria-labelledby="knowledge-explorer-title">
      <div className="explorer-header">
        <div>
          <p className="eyebrow">Interactive knowledge graph</p>
          <h2 id="knowledge-explorer-title">Knowledge explorer</h2>
          <p className="muted">
            Concepts and evidence-backed relationships extracted from your materials.{" "}
            {selectedConceptId ? "Select a concept to inspect its evidence and relations." : ""}
          </p>
        </div>
        <div className="explorer-controls">
          <label className="document-control" htmlFor="kg-search">
            Search concepts
            <input
              id="kg-search"
              type="search"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.currentTarget.value)}
              placeholder="Search concepts…"
            />
          </label>
          <button type="button" className="camera-action camera-action--quiet" onClick={resetView}>
            Reset view
          </button>
          {matchingConcepts.length === 0 && concepts.length > 0 && (
            <span className="notice notice--error" role="alert">
              No concepts match “{searchQuery}”.
            </span>
          )}
        </div>
      </div>

      <div className="explorer-body">
        <svg
          ref={svgRef}
          className="kg-svg"
          onWheel={onWheel}
          onMouseDown={onMouseDown}
          onMouseMove={onMouseMove}
          onMouseUp={onMouseUp}
          onDoubleClick={resetView}
          style={{ width: "100%", height: "420px", background: "#fbfaf7", borderRadius: "0.6rem" }}
          tabIndex={0}
        >
          <defs>
            <marker
              id="kg-arrow"
              markerWidth="8"
              markerHeight="8"
              refX="7"
              refY="3"
              orient="auto"
            >
              <path d="M0,0 L0,6 L6,3 z" fill="#64748b" />
            </marker>
          </defs>
          {Array.from(expandedIds).flatMap((id) => {
            const detail = details.get(id);
            if (!detail) return [];
            return detail.relationships.map((rel) => {
              const source = positions.get(rel.sourceConceptId);
              const target = positions.get(rel.targetConceptId);
              if (!source || !target) return null;
              return (
                <line
                  key={rel.relationshipId}
                  x1={source.x}
                  y1={source.y}
                  x2={target.x}
                  y2={target.y}
                  stroke={rel.status === "human_verified" ? "#226544" : "#94a3b8"}
                  strokeWidth={rel.status === "human_verified" ? 2.5 : 1.5}
                  strokeOpacity={rel.status === "human_verified" ? 0.9 : 0.5}
                  markerEnd="url(#kg-arrow)"
                  pointerEvents="none"
                />
              );
            });
          })}
          {matchingConcepts.map((concept) => {
            const pos = positions.get(concept.conceptId);
            if (!pos) return null;
            const isSelected = selectedConceptId === concept.conceptId;
            const isExpanded = expandedIds.has(concept.conceptId);
            const isMatch =
              searchQuery &&
              (concept.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
                concept.aliases.some((a) =>
                  a.toLowerCase().includes(searchQuery.toLowerCase()),
                ));
            return (
              <g
                key={concept.conceptId}
                onClick={(event) => {
                  event.stopPropagation();
                  void loadConceptDetail(concept.conceptId);
                }}
                role="button"
                tabIndex={0}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    void loadConceptDetail(concept.conceptId);
                  }
                }}
              >
                <circle
                  cx={pos.x}
                  cy={pos.y}
                  r={NODE_RADIUS + (isMatch ? 6 : 0) + (isSelected ? 4 : 0)}
                  fill={isSelected ? "#0f766e" : isExpanded ? "#226544" : "#64748b"}
                  stroke="#ffffff"
                  strokeWidth={2.5}
                  strokeOpacity={isSelected ? 1 : 0.9}
                />
                <text
                  x={pos.x}
                  y={pos.y + 4}
                  textAnchor="middle"
                  fill="#ffffff"
                  fontSize={11}
                  fontWeight="600"
                  pointerEvents="none"
                  data-testid="kg-node-name"
                >
                  {concept.name.length > 14 ? concept.name.slice(0, 13) + "…" : concept.name}
                </text>
                {isExpanded && (
                  <circle
                    cx={pos.x}
                    cy={pos.y - NODE_RADIUS - 6}
                    r={6}
                    fill="#0f766e"
                    stroke="#ffffff"
                    strokeWidth={1.5}
                  />
                )}
                {isMatch && (
                  <circle
                    cx={pos.x + NODE_RADIUS - 5}
                    cy={pos.y - NODE_RADIUS + 5}
                    r={7}
                    fill="#f59e0b"
                    stroke="#ffffff"
                    strokeWidth={2}
                  />
                )}
              </g>
            );
          })}
        </svg>

        <aside className="kg-detail">
          {selectedConceptId && selectedDetail ? (
            <>
              <button
                type="button"
                className="camera-action camera-action--quiet"
                onClick={clearSelection}
              >
                ← Close
              </button>
              <h3>{selectedDetail.name}</h3>
              {!!selectedDetail.aliases.length && (
                <p className="muted">Aliases: {selectedDetail.aliases.join(", ")}</p>
              )}
              <p className="muted">
                {selectedDetail.evidence.length} supporting passage(s) ·
                {selectedDetail.relationships.length} relationship(s)
              </p>

              <h4>Evidence</h4>
              <ul className="evidence-list">
                {selectedDetail.evidence.map((item) => (
                  <li key={`${item.chunkId}-${item.supportingText}`}>
                    <blockquote>{item.supportingText}</blockquote>
                    <p className="citation-meta">
                      <strong>{item.displayFilename}</strong> · page {item.pageNumber} · chunk{" "}
                      <code>{item.chunkId}</code> · document <code>{item.documentId}</code>
                    </p>
                    <button
                      type="button"
                      className="text-action"
                      onClick={() => {
                        onOpenDocument?.(item.documentId);
                      }}
                    >
                      Open source document
                    </button>
                  </li>
                ))}
              </ul>

              <h4>Relationships</h4>
              {selectedDetail.relationships.length === 0 && (
                <p className="muted">No relationships extracted for this concept.</p>
              )}
              {selectedDetail.relationships.map((rel) => {
                const otherId =
                  rel.sourceConceptId === selectedConceptId
                    ? rel.targetConceptId
                    : rel.sourceConceptId;
                const otherName =
                  rel.sourceConceptId === selectedConceptId
                    ? rel.targetName
                    : rel.sourceName;
                const isExpandedOther = expandedIds.has(otherId);
                return (
                  <li key={rel.relationshipId}>
                    <div className="relation-row">
                      <button
                        type="button"
                        className="text-action"
                        onClick={() => void loadConceptDetail(otherId)}
                      >
                        {otherName}
                      </button>
                      <span>· {rel.relation}</span>
                      <span className="relation-status">
                        {rel.status === "human_verified" ? "Verified" : "Candidate"}
                      </span>
                    </div>
                    <blockquote>{rel.supportingText}</blockquote>
                    <p className="citation-meta">
                      <strong>{rel.displayFilename}</strong> · page {rel.pageNumber} · chunk{" "}
                      <code>{rel.chunkId}</code>
                    </p>
                    {rel.status === "candidate" && (
                      <>
                        <button
                          type="button"
                          className="text-action"
                          disabled={detailError !== null}
                          onClick={() => {
                            onVerifyRelationship?.(rel.relationshipId);
                          }}
                        >
                          Mark as human-verified
                        </button>
                        {!isExpandedOther && (
                          <button
                            type="button"
                            className="text-action"
                            onClick={() => void loadConceptDetail(otherId)}
                          >
                            Expand “{otherName}”
                          </button>
                        )}
                      </>
                    )}
                    {isExpandedOther && (
                      <button
                        type="button"
                        className="text-action"
                        onClick={() => void loadConceptDetail(otherId)}
                      >
                        Reload “{otherName}”
                      </button>
                    )}
                  </li>
                );
              })}
              <div className="explorer-actions">
                <button
                  type="button"
                  className="camera-action camera-action--primary"
                  onClick={() => {
                    onAskTutor?.(selectedConceptId, selectedDetail.name);
                  }}
                >
                  Ask tutor about this concept
                </button>
                <button
                  type="button"
                  className="camera-action"
                  onClick={() => {
                    onGenerateQuiz?.(selectedDetail.name);
                  }}
                >
                  Generate quiz for this concept
                </button>
                <button
                  type="button"
                  className="camera-action"
                  onClick={() => {
                    onGenerateNotes?.(selectedDetail.name);
                  }}
                >
                  Generate notes for this concept
                </button>
              </div>
            </>
          ) : (
            <>
              <p className="muted">Select a concept from the graph to inspect its evidence and relationships.</p>
              {selectedConceptId === null && detailError && (
                <p className="notice notice--error" role="alert">{detailError}</p>
              )}
            </>
          )}
        </aside>
      </div>
    </section>
  );
}
