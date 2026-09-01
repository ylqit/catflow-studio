import { describe, expect, it } from "vitest";

import { mountWebAvPreview } from "./webavPreview";

describe("WebAV preview boundary", () => {
  it("fails visibly when WebCodecs is unavailable instead of pretending to edit", async () => {
    await expect(
      mountWebAvPreview(document.createElement("div"), "/video.mp4", {
        startMs: 0,
        endMs: 8000,
      }),
    ).rejects.toThrow("WebCodecs");
  });
});
