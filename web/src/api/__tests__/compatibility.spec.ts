import { describe, expect, it } from "vitest";

import {
  REQUIRED_DIRECTOR_API_FEATURES,
  missingDirectorApiFeatures,
} from "../compatibility";

describe("director API compatibility", () => {
  it("treats a legacy health response without feature declarations as incompatible", () => {
    expect(missingDirectorApiFeatures(undefined)).toEqual(REQUIRED_DIRECTOR_API_FEATURES);
  });

  it("returns only the feature names missing from the running API", () => {
    expect(missingDirectorApiFeatures([
      "storyboard_production_confirmations",
      "reference_media_video_generation",
    ])).toEqual([
      "visual_asset_plan_manual_revisions",
      "manual_video_edit_boundaries",
      "workflow_task_cancellation_v1",
    ]);
  });

  it("accepts a backend that advertises every required feature", () => {
    expect(missingDirectorApiFeatures([...REQUIRED_DIRECTOR_API_FEATURES])).toEqual([]);
  });
});
