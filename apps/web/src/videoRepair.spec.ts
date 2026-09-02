import { describe, expect, it } from "vitest";

import {
  allRepairChecksPass,
  clampIssueEnd,
  clampIssueStart,
  formatFrameTimecode,
  isValidIssueRange,
  mediaTimeToFrame,
  moveCandidateCoreRange,
  snapFrame,
} from "./videoRepair";

describe("frame-accurate video repair helpers", () => {
  it("keeps both range handles inside a 12 second source with a four second minimum", () => {
    expect(clampIssueStart(250, 192, 288)).toBe(96);
    expect(clampIssueStart(-12, 192, 288)).toBe(0);
    expect(clampIssueEnd(40, 96, 288)).toBe(192);
    expect(clampIssueEnd(400, 96, 288)).toBe(288);
    expect(isValidIssueRange({ startFrame: 0, endFrame: 95 }, 288)).toBe(false);
    expect(isValidIssueRange({ startFrame: 0, endFrame: 96 }, 288)).toBe(true);
    expect(clampIssueStart(0, 480, 480)).toBe(120);
    expect(clampIssueEnd(480, 0, 480)).toBe(360);
    expect(isValidIssueRange({ startFrame: 0, endFrame: 361 }, 480)).toBe(false);
  });

  it("formats the exclusive 24 fps edit time base as SMPTE-like timecode", () => {
    expect(formatFrameTimecode(0)).toBe("00:00:00:00");
    expect(formatFrameTimecode(95)).toBe("00:00:03:23");
    expect(formatFrameTimecode(288)).toBe("00:00:12:00");
  });

  it("maps media seek rounding noise back to the requested frame", () => {
    expect(mediaTimeToFrame(100 / 24, 24, 288)).toBe(100);
    expect(mediaTimeToFrame(4.1666665, 24, 288)).toBe(100);
    expect(mediaTimeToFrame(12, 24, 288)).toBe(287);
  });

  it("moves the candidate core one frame without changing its duration", () => {
    expect(moveCandidateCoreRange({ startFrame: 24, endFrame: 120 }, 1, 144)).toEqual({
      startFrame: 25,
      endFrame: 121,
    });
    expect(moveCandidateCoreRange({ startFrame: 24, endFrame: 120 }, -99, 144)).toEqual({
      startFrame: 0,
      endFrame: 96,
    });
  });

  it("snaps only when a frame is within the requested tolerance", () => {
    expect(snapFrame(94, [0, 96, 192, 288], 3)).toBe(96);
    expect(snapFrame(90, [0, 96, 192, 288], 3)).toBe(90);
  });

  it("requires all seven quality checks and both seam checks to pass", () => {
    const quality: Record<string, "pass"> = Object.fromEntries(
      ["child_identity", "cat_identity", "pair_scale", "style", "structure", "motion_continuity", "causal_chain"]
        .map((key) => [key, "pass"]),
    );
    expect(allRepairChecksPass(quality, { in: "pass", out: "pass" })).toBe(true);
    expect(allRepairChecksPass({ ...quality, structure: "warning" }, { in: "pass", out: "pass" })).toBe(false);
  });
});
