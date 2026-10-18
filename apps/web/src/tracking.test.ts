import { describe, expect, it } from "vitest";
import {
  advanceTracking,
  homographyToCssMatrix,
  INITIAL_TRACKING_PROGRESS,
  isPlausibleDetection,
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
