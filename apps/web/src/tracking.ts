export type TrackingStatus = "off" | "loading" | "searching" | "tracking" | "lost" | "unavailable";

export type TrackingProgress = {
  status: TrackingStatus;
  consecutiveHits: number;
  consecutiveMisses: number;
};

export const INITIAL_TRACKING_PROGRESS: TrackingProgress = {
  status: "off",
  consecutiveHits: 0,
  consecutiveMisses: 0,
};

const HITS_TO_LOCK = 2;
const MISSES_TO_LOSE = 3;

export function advanceTracking(progress: TrackingProgress, detected: boolean): TrackingProgress {
  if (detected) {
    const consecutiveHits = progress.consecutiveHits + 1;
    return {
      status: consecutiveHits >= HITS_TO_LOCK ? "tracking" : "searching",
      consecutiveHits,
      consecutiveMisses: 0,
    };
  }

  const consecutiveMisses = progress.consecutiveMisses + 1;
  return {
    status: consecutiveMisses >= MISSES_TO_LOSE ? "lost" : "searching",
    consecutiveHits: 0,
    consecutiveMisses,
  };
}

export function homographyToCssMatrix(
  matrix: readonly number[],
  referenceWidth: number,
  referenceHeight: number,
  frameWidth: number,
  frameHeight: number,
  videoWidth: number,
  videoHeight: number,
  stageWidth: number,
  stageHeight: number,
): string | null {
  if (
    matrix.length !== 9 ||
    [referenceWidth, referenceHeight, frameWidth, frameHeight, videoWidth, videoHeight, stageWidth, stageHeight].some(
      (value) => !Number.isFinite(value) || value <= 0,
    ) ||
    matrix.some((value) => !Number.isFinite(value)) ||
    Math.abs(matrix[8]!) < 1e-9
  ) {
    return null;
  }

  const h = matrix.map((value) => value / matrix[8]!);
  const videoAspect = videoWidth / videoHeight;
  const stageAspect = stageWidth / stageHeight;
  let stageScaleX: number;
  let stageScaleY: number;
  let stageOffsetX: number;
  let stageOffsetY: number;
  if (videoAspect > stageAspect) {
    stageScaleX = stageHeight / videoHeight;
    stageScaleY = stageScaleX;
    stageOffsetX = (stageWidth - videoWidth * stageScaleX) / 2;
    stageOffsetY = 0;
  } else {
    stageScaleX = stageWidth / videoWidth;
    stageScaleY = stageScaleX;
    stageOffsetX = 0;
    stageOffsetY = (stageHeight - videoHeight * stageScaleY) / 2;
  }

  const frameToVideoX = videoWidth / frameWidth;
  const frameToVideoY = videoHeight / frameHeight;
  const xInput = referenceWidth / stageWidth;
  const yInput = referenceHeight / stageHeight;
  const a = stageScaleX * frameToVideoX * h[0]! * xInput;
  const c = stageScaleX * frameToVideoX * h[1]! * yInput;
  const e = stageOffsetX + stageScaleX * frameToVideoX * h[2]!;
  const b = stageScaleY * frameToVideoY * h[3]! * xInput;
  const d = stageScaleY * frameToVideoY * h[4]! * yInput;
  const f = stageOffsetY + stageScaleY * frameToVideoY * h[5]!;
  const p = h[6]! * xInput;
  const q = h[7]! * yInput;
  const cssValues = [a, b, c, d, e, f, p, q];
  if (cssValues.some((value) => !Number.isFinite(value))) return null;

  const [cssA, cssB, cssC, cssD, cssE, cssF, cssP, cssQ] = cssValues;
  return `matrix3d(${cssA},${cssB},0,${cssP},${cssC},${cssD},0,${cssQ},0,0,1,0,${cssE},${cssF},0,1)`;
}

export function isPlausibleDetection(
  matrix: readonly number[],
  referenceWidth: number,
  referenceHeight: number,
  frameWidth: number,
  frameHeight: number,
): boolean {
  if (matrix.length !== 9 || matrix.some((value) => !Number.isFinite(value))) return false;
  if (referenceWidth <= 0 || referenceHeight <= 0 || frameWidth <= 0 || frameHeight <= 0) return false;

  const project = (x: number, y: number): [number, number] | null => {
    const denominator = matrix[6]! * x + matrix[7]! * y + matrix[8]!;
    if (!Number.isFinite(denominator) || Math.abs(denominator) < 1e-8) return null;
    const mappedX = (matrix[0]! * x + matrix[1]! * y + matrix[2]!) / denominator;
    const mappedY = (matrix[3]! * x + matrix[4]! * y + matrix[5]!) / denominator;
    return Number.isFinite(mappedX) && Number.isFinite(mappedY) ? [mappedX, mappedY] : null;
  };

  const corners = [
    project(0, 0),
    project(referenceWidth, 0),
    project(referenceWidth, referenceHeight),
    project(0, referenceHeight),
  ];
  if (corners.some((point) => point === null)) return false;
  const points = corners as Array<[number, number]>;
  if (points.some(([x, y]) => x < -frameWidth * 0.5 || x > frameWidth * 1.5 || y < -frameHeight * 0.5 || y > frameHeight * 1.5)) {
    return false;
  }

  let twiceArea = 0;
  for (let index = 0; index < points.length; index += 1) {
    const [x1, y1] = points[index]!;
    const [x2, y2] = points[(index + 1) % points.length]!;
    twiceArea += x1 * y2 - x2 * y1;
  }
  const areaRatio = Math.abs(twiceArea / 2) / (frameWidth * frameHeight);
  return areaRatio >= 0.015 && areaRatio <= 1.5;
}
