import { describe, expect, it } from "vitest";

import {
  clampVideoSelection,
  filmstripTimestamps,
  normalizedVideoPoint,
  renderedVideoRect,
} from "../canvas/videoEditing";

describe("video editing geometry", () => {
  it("keeps a dragged selection inside the source and the 0.5-13 second limits", () => {
    expect(clampVideoSelection({ startMs: 9_800, endMs: 15_000 }, 10_000, "end")).toEqual({
      startMs: 9_500,
      endMs: 10_000,
    });
    expect(clampVideoSelection({ startMs: 1_000, endMs: 20_000 }, 30_000, "end")).toEqual({
      startMs: 1_000,
      endMs: 14_000,
    });
  });

  it("normalizes portrait annotations against the rendered video instead of letterboxing", () => {
    const rect = renderedVideoRect(
      { left: 0, top: 0, width: 1600, height: 900 },
      { width: 1080, height: 1920 },
    );
    expect(rect).toEqual({ left: 546.875, top: 0, width: 506.25, height: 900 });
    expect(normalizedVideoPoint({ x: 800, y: 450 }, rect)).toEqual({ x: 0.5, y: 0.5 });
    expect(normalizedVideoPoint({ x: 100, y: 450 }, rect)).toBeNull();
  });

  it("requests distinct real frame timestamps without exceeding the final frame", () => {
    expect(filmstripTimestamps(5_000, 5)).toEqual([0, 1_250, 2_500, 3_749, 4_999]);
  });
});
