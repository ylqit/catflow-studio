export const REQUIRED_DIRECTOR_API_FEATURES = [
  "storyboard_production_confirmations",
  "reference_media_video_generation",
  "visual_asset_plan_manual_revisions",
  "manual_video_edit_boundaries",
  "workflow_task_cancellation_v1",
] as const;

export function missingDirectorApiFeatures(features: readonly string[] | undefined): string[] {
  const advertised = new Set(features ?? []);
  return REQUIRED_DIRECTOR_API_FEATURES.filter((feature) => !advertised.has(feature));
}
