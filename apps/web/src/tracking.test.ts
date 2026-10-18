import { describe, expect, it } from "vitest";
import {
  areCorrespondencesValid,
  advanceTracking,
  hasReliableInlierSupport,
  homographyToCssMatrix,
  INITIAL_TRACKING_PROGRESS,
  isPlausibleDetection,
  smoothHomography,
  type TrackingProgress,
} from "./tracking";

describe("automatic diagram tracking", () => {
  it("converts a reference-to-frame homography into a responsive CSS transform", () => {
    const transform = homographyToCssMatrix(
      [0.5, 0, 0, 0, 0.5, 0, 0, 0, 1],
      1000,
      640,
      500,
      320,
      500,
      320,
      500,
      320,
    );

    expect(transform).toBe("matrix3d(1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1)");
  });

  it("preserves detected rotation and translation in the overlay transform", () => {
    const transform = homographyToCssMatrix(
      [0, -0.5, 75, 0.5, 0, 25, 0, 0, 1],
      100,
      100,
      100,
      100,
      100,
      100,
      100,
      100,
    );

    expect(transform).toBe("matrix3d(0,0.5,0,0,-0.5,0,0,0,0,0,1,0,75,25,0,1)");
  });

  it("rejects invalid or degenerate alignment estimates", () => {
    expect(homographyToCssMatrix([1, 2], 1000, 640, 640, 410, 640, 410, 500, 320)).toBeNull();
    expect(isPlausibleDetection([0, 0, 0, 0, 0, 0, 0, 0, 1], 1000, 640, 640, 410)).toBe(false);
    expect(isPlausibleDetection([0.5, 0, 0, 0, 0.5, 0, 0, 0, 1], 1000, 640, 640, 410)).toBe(true);
  });

  it("rejects references outside the frame and homographies crossing the projective horizon", () => {
    expect(isPlausibleDetection([0.5, 0, -20, 0, 0.5, 0, 0, 0, 1], 1000, 640, 640, 410)).toBe(false);
    expect(isPlausibleDetection([1, 0, 0, 0, 1, 0, -0.02, 0, 1], 1000, 640, 640, 410)).toBe(false);
  });

  it("validates finite, in-bounds reference and frame correspondences", () => {
    const reference = [10, 10, 80, 10, 80, 80, 10, 80, 40, 40, 25, 60, 60, 25, 55, 55];
    const frame = [20, 15, 90, 15, 90, 85, 20, 85, 50, 50, 35, 70, 70, 35, 65, 65];
    expect(areCorrespondencesValid(reference, frame, 100, 100, 100, 100)).toBe(true);
    expect(areCorrespondencesValid(reference, [...frame.slice(0, 14), 120, 65], 100, 100, 100, 100)).toBe(false);
    expect(areCorrespondencesValid(reference, [...frame.slice(0, 14), Number.NaN, 65], 100, 100, 100, 100)).toBe(false);
    expect(areCorrespondencesValid(reference.slice(0, 10), frame.slice(0, 10), 100, 100, 100, 100)).toBe(false);
    const clustered = [40, 40, 45, 40, 45, 45, 40, 45, 42, 41, 43, 44, 44, 42, 41, 43];
    expect(areCorrespondencesValid(clustered, clustered, 100, 100, 100, 100)).toBe(false);
  });

  it("rejects weak RANSAC inlier support", () => {
    expect(hasReliableInlierSupport(8, 20)).toBe(true);
    expect(hasReliableInlierSupport(7, 20)).toBe(false);
    expect(hasReliableInlierSupport(8, 40)).toBe(false);
    expect(hasReliableInlierSupport(21, 20)).toBe(false);
  });

  it("smooths frame-to-frame jitter while keeping the homography normalized", () => {
    const settled = [1, 0, 0, 0, 1, 0, 0, 0, 1];
    const jittered = [1, 0, 10, 0, 1, -6, 0, 0, 1];
    expect(smoothHomography(null, settled)).toEqual(settled);
    expect(smoothHomography(settled, jittered, 0.5)).toEqual([1, 0, 5, 0, 1, -3, 0, 0, 1]);
    expect(smoothHomography(settled, jittered, 0.5)?.[2]).toBeLessThan(jittered[2]!);
  });

  it("maps reference geometry through the object-fit cover crop", () => {
    expect(
      homographyToCssMatrix([1, 0, 0, 0, 1, 0, 0, 0, 1], 100, 100, 200, 100, 200, 100, 100, 100),
    ).toBe("matrix3d(1,0,0,0,0,1,0,0,0,0,1,0,-50,0,0,1)");
  });

  it("pauses selection on tracking loss and recovers only after stable reacquisition", () => {
    let progress: TrackingProgress = { ...INITIAL_TRACKING_PROGRESS, status: "searching" };
    progress = advanceTracking(progress, true);
    expect(progress.status).toBe("searching");
    progress = advanceTracking(progress, true);
    expect(progress.status).toBe("tracking");

    progress = advanceTracking(progress, false);
    expect(progress.status).toBe("searching");
    progress = advanceTracking(progress, false);
    progress = advanceTracking(progress, false);
    expect(progress.status).toBe("lost");

    progress = advanceTracking(progress, true);
    expect(progress.status).toBe("searching");
    progress = advanceTracking(progress, true);
    expect(progress.status).toBe("tracking");
  });
});
