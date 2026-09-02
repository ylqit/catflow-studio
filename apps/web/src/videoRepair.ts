import type { FrameRangeDto } from "./api/types";

export type RepairVerdict = "pass" | "warning" | "fail" | "";

const requiredQualityChecks = [
  "child_identity",
  "cat_identity",
  "pair_scale",
  "style",
  "structure",
  "motion_continuity",
  "causal_chain",
] as const;

export function formatFrameTimecode(frame: number, framesPerSecond = 24): string {
  const safeFrame = Math.max(0, Math.trunc(frame));
  const frames = safeFrame % framesPerSecond;
  const totalSeconds = Math.floor(safeFrame / framesPerSecond);
  const seconds = totalSeconds % 60;
  const minutes = Math.floor(totalSeconds / 60) % 60;
  const hours = Math.floor(totalSeconds / 3600);
  return [hours, minutes, seconds, frames].map((part) => String(part).padStart(2, "0")).join(":");
}

export function mediaTimeToFrame(currentTime: number, framesPerSecond: number, totalFrames: number): number {
  return Math.max(0, Math.min(totalFrames - 1, Math.round(currentTime * framesPerSecond)));
}

export function snapFrame(frame: number, boundaries: number[], tolerance: number): number {
  const nearest = boundaries.reduce(
    (best, boundary) => Math.abs(boundary - frame) < Math.abs(best - frame) ? boundary : best,
    boundaries[0] ?? frame,
  );
  return Math.abs(nearest - frame) <= tolerance ? nearest : frame;
}

export function moveCandidateCoreRange(
  range: FrameRangeDto,
  deltaFrames: number,
  totalFrames: number,
): FrameRangeDto {
  const duration = range.endFrame - range.startFrame;
  const startFrame = Math.max(0, Math.min(totalFrames - duration, range.startFrame + deltaFrames));
  return { startFrame, endFrame: startFrame + duration };
}

export function allRepairChecksPass(
  quality: Record<string, RepairVerdict>,
  seams: Record<string, RepairVerdict>,
): boolean {
  return requiredQualityChecks.every((key) => quality[key] === "pass")
    && seams.in === "pass"
    && seams.out === "pass";
}
