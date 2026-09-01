import type { EditDecisionListDto } from "./api/types";

export type EditDecisionList = EditDecisionListDto;

export function validateEditDecisionList(
  edit: EditDecisionList,
  currentHashes: Readonly<Record<string, string>>,
): EditDecisionList {
  if (edit.sourceVideoSelections.length === 0) {
    throw new Error("at least one source video is required");
  }
  for (const source of edit.sourceVideoSelections) {
    if (source.startMs < 0 || source.endMs <= source.startMs) {
      throw new Error(`invalid source interval: ${source.assetId}`);
    }
    if (currentHashes[source.assetId] !== source.sha256) {
      throw new Error(`source hash changed: ${source.assetId}`);
    }
  }
  if (
    edit.output.aspectRatio !== "9:16" ||
    edit.output.width !== 720 ||
    edit.output.height !== 1280 ||
    edit.output.format !== "mp4"
  ) {
    throw new Error("CatFlow export must be 720x1280 9:16 MP4");
  }
  return edit;
}
