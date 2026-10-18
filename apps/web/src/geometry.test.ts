import { describe, expect, it } from "vitest";
import { alignmentTransform, normalizedRegionStyle } from "./geometry";

describe("normalized diagram geometry", () => {
  it("maps normalized bounds to responsive percentages", () => {
    expect(normalizedRegionStyle({ x: 0.25, y: 0.1, width: 0.5, height: 0.4 })).toEqual({
      left: "25%",
      top: "10%",
      width: "50%",
      height: "40%",
    });
  });

  it("represents manual translation, scale, and rotation in the shared stage", () => {
    expect(alignmentTransform({ x: 0.1, y: -0.2, scale: 1.25, rotation: 15 })).toBe(
      "translate(10%, -20%) rotate(15deg) scale(1.25)",
    );
  });
});
