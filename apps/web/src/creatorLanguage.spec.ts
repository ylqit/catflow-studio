import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const creatorSurfaces = [
  "views/ProjectListView.vue",
  "views/WorkspaceView.vue",
  "components/workspace/PlannerStep.vue",
  "components/workspace/AssetsStep.vue",
  "components/workspace/StoryboardStep.vue",
  "components/workspace/GenerationStep.vue",
  "components/workspace/DeliveryStep.vue",
  "components/workspace/VideoRepairWorkspace.vue",
];

const forbiddenDefaultCopy = [
  "LIFE PLANNER",
  "STRUCTURED PROPOSALS",
  "VIDEO GENERATION",
  "GENERATION SAFETY",
  "VIDEO CANDIDATES",
  "PROJECT USAGE",
  "PostgreSQL",
  "SSE",
  "Worker",
  "FFmpeg",
  "Provider task ID",
  "SHA256",
  "input hash",
  "capability",
  "style_source",
  "Revision",
  "succeeded",
  "polling",
  "submission_unknown",
  "Fake Provider",
  "AI 修改片段",
  "规划付费确认",
];

function defaultVisibleLiteralCopy(source: string): string {
  const template = source.match(/<template>([\s\S]*?)<\/template>/)?.[1] ?? "";
  let withoutDetails = template;
  let previous = "";

  // Technical terms are allowed in collapsed production/technical records. Remove
  // those regions before inspecting copy visible on the default creator surface.
  while (withoutDetails !== previous) {
    previous = withoutDetails;
    withoutDetails = withoutDetails.replace(/<details\b[\s\S]*?<\/details>/g, " ");
  }

  return withoutDetails
    .replace(/\{\{[\s\S]*?\}\}/g, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

describe("creator-facing language", () => {
  it.each(creatorSurfaces)("keeps internal implementation terms off %s", (surface) => {
    const sourcePath = fileURLToPath(new URL(surface, import.meta.url));
    const visibleCopy = defaultVisibleLiteralCopy(readFileSync(sourcePath, "utf8"));

    for (const term of forbiddenDefaultCopy) {
      expect(visibleCopy, `${surface} exposes ${term}`).not.toContain(term);
    }
  });
});
