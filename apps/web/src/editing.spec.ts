import { describe, expect, it } from "vitest";

import type { EditDecisionList } from "./editing";
import { validateEditDecisionList } from "./editing";

describe("edit decision list", () => {
  it("accepts a simple 9:16 cut and rejects changed source hashes", () => {
    const edl: EditDecisionList = {
      sourceVideoSelections: [
        { assetId: "asset-1", sha256: "a".repeat(64), startMs: 0, endMs: 8000 },
      ],
      transitions: [],
      audioPolicy: "native_fades" as const,
      output: { aspectRatio: "9:16" as const, width: 720, height: 1280, format: "mp4" as const },
    };

    expect(validateEditDecisionList(edl, { "asset-1": "a".repeat(64) })).toEqual(edl);
    expect(() => validateEditDecisionList(edl, { "asset-1": "b".repeat(64) })).toThrow(
      "source hash changed",
    );
  });
});
