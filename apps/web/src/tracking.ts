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
const MIN_INLIERS = 8;
const MIN_INLIER_RATIO = 0.24;
const SMOOTHING_RESPONSE = 0.7;

export function hasReliableInlierSupport(inliers: number, matches: number): boolean {
  return (
    Number.isInteger(inliers) &&
    Number.isInteger(matches) &&
    matches >= MIN_INLIERS &&
    inliers >= MIN_INLIERS &&
    inliers <= matches &&
    inliers / matches >= MIN_INLIER_RATIO
  );
}

export function areCorrespondencesValid(
  referencePoints: readonly number[],
  framePoints: readonly number[],
  referenceWidth: number,
  referenceHeight: number,
  frameWidth: number,
  frameHeight: number,
): boolean {
  if (
    referencePoints.length !== framePoints.length ||
    referencePoints.length < MIN_INLIERS * 2 ||
    referencePoints.length % 2 !== 0 ||
    [referenceWidth, referenceHeight, frameWidth, frameHeight].some(
      (value) => !Number.isFinite(value) || value <= 0,
    )
  ) {
    return false;
  }

  const inBounds = (points: readonly number[], width: number, height: number) => {
    for (let index = 0; index < points.length; index += 2) {
      const x = points[index]!;
      const y = points[index + 1]!;
      if (!Number.isFinite(x) || !Number.isFinite(y) || x < 0 || y < 0 || x > width || y > height) {
        return false;
      }
    }
    return true;
  };
  const hasSpatialSpread = (points: readonly number[], width: number, height: number) => {
    const xs = points.filter((_, index) => index % 2 === 0);
    const ys = points.filter((_, index) => index % 2 === 1);
    return (
      Math.max(...xs) - Math.min(...xs) >= width * 0.16 &&
      Math.max(...ys) - Math.min(...ys) >= height * 0.16
    );
  };
  return (
    inBounds(referencePoints, referenceWidth, referenceHeight) &&
    inBounds(framePoints, frameWidth, frameHeight) &&
    hasSpatialSpread(referencePoints, referenceWidth, referenceHeight) &&
    hasSpatialSpread(framePoints, frameWidth, frameHeight)
  );
}

export function smoothHomography(
  previous: readonly number[] | null,
  current: readonly number[],
  response = SMOOTHING_RESPONSE,
): number[] | null {
  if (
    current.length !== 9 ||
    current.some((value) => !Number.isFinite(value)) ||
    Math.abs(current[8]!) < 1e-9 ||
    !Number.isFinite(response) ||
    response <= 0 ||
    response > 1
  ) {
    return null;
  }
  const normalizedCurrent = current.map((value) => value / current[8]!);
  if (previous === null) return normalizedCurrent;
  if (previous.length !== 9 || previous.some((value) => !Number.isFinite(value)) || Math.abs(previous[8]!) < 1e-9) {
    return normalizedCurrent;
  }

  const normalizedPrevious = previous.map((value) => value / previous[8]!);
  const result = normalizedCurrent.map(
    (value, index) => normalizedPrevious[index]! * (1 - response) + value * response,
  );
  if (Math.abs(result[8]!) < 1e-9 || result.some((value) => !Number.isFinite(value))) return null;
  return result.map((value) => value / result[8]!);
}

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

  const cornerInputs = [
    [0, 0],
    [referenceWidth, 0],
    [referenceWidth, referenceHeight],
    [0, referenceHeight],
  ] as const;
  const denominators = cornerInputs.map(([x, y]) => matrix[6]! * x + matrix[7]! * y + matrix[8]!);
  if (denominators.some((value) => !Number.isFinite(value) || Math.abs(value) < 1e-8)) return false;
  if (denominators.some((value) => Math.sign(value) !== Math.sign(denominators[0]!))) return false;

  const corners = [
    project(0, 0),
    project(referenceWidth, 0),
    project(referenceWidth, referenceHeight),
    project(0, referenceHeight),
  ];
  if (corners.some((point) => point === null)) return false;
  const points = corners as Array<[number, number]>;
  const frameTolerance = 0.02;
  if (points.some(([x, y]) =>
    x < -frameWidth * frameTolerance ||
    x > frameWidth * (1 + frameTolerance) ||
    y < -frameHeight * frameTolerance ||
    y > frameHeight * (1 + frameTolerance)
  )) {
    return false;
  }

  let twiceArea = 0;
  let turnSign = 0;
  for (let index = 0; index < points.length; index += 1) {
    const [x1, y1] = points[index]!;
    const [x2, y2] = points[(index + 1) % points.length]!;
    const [x3, y3] = points[(index + 2) % points.length]!;
    twiceArea += x1 * y2 - x2 * y1;
    const cross = (x2 - x1) * (y3 - y2) - (y2 - y1) * (x3 - x2);
    if (Math.abs(cross) < 1e-6) return false;
    if (turnSign !== 0 && Math.sign(cross) !== turnSign) return false;
    turnSign = Math.sign(cross);
    if (Math.hypot(x2 - x1, y2 - y1) < Math.min(frameWidth, frameHeight) * 0.03) return false;
  }
  const areaRatio = Math.abs(twiceArea / 2) / (frameWidth * frameHeight);
  return areaRatio >= 0.015 && areaRatio <= 1.5;
}
