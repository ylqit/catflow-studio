export interface VideoSelection {
  startMs: number;
  endMs: number;
}

export interface VideoEditConsoleDraft extends VideoSelection {
  instruction: string;
  referenceAssetIds: string[];
  annotations: Array<{
    frameTimestampMs: number;
    coordinateSpace?: "source_normalized";
    tool: "rectangle" | "brush" | "arrow" | "text" | "marker";
    points: Array<{ x: number; y: number }>;
    label: string;
  }>;
}

export interface ScreenRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export function clampVideoSelection(
  selection: VideoSelection,
  durationMs: number,
  movedEdge: "start" | "end",
): VideoSelection {
  const boundedDuration = Math.max(500, durationMs);
  let startMs = Math.max(0, Math.min(selection.startMs, boundedDuration - 500));
  let endMs = Math.max(500, Math.min(selection.endMs, boundedDuration));
  if (endMs - startMs < 500) {
    if (movedEdge === "start") startMs = Math.max(0, endMs - 500);
    else endMs = Math.min(boundedDuration, startMs + 500);
  }
  if (endMs - startMs > 13_000) {
    if (movedEdge === "start") startMs = endMs - 13_000;
    else endMs = startMs + 13_000;
  }
  return { startMs: Math.round(startMs), endMs: Math.round(endMs) };
}

export function renderedVideoRect(
  container: ScreenRect,
  source: { width: number; height: number },
): ScreenRect {
  if (source.width <= 0 || source.height <= 0 || container.width <= 0 || container.height <= 0) {
    return { ...container };
  }
  const scale = Math.min(container.width / source.width, container.height / source.height);
  const width = source.width * scale;
  const height = source.height * scale;
  return {
    left: container.left + (container.width - width) / 2,
    top: container.top + (container.height - height) / 2,
    width,
    height,
  };
}

export function normalizedVideoPoint(
  point: { x: number; y: number },
  videoRect: ScreenRect,
): { x: number; y: number } | null {
  if (
    point.x < videoRect.left || point.x > videoRect.left + videoRect.width
    || point.y < videoRect.top || point.y > videoRect.top + videoRect.height
  ) return null;
  return {
    x: (point.x - videoRect.left) / videoRect.width,
    y: (point.y - videoRect.top) / videoRect.height,
  };
}

export function filmstripTimestamps(durationMs: number, frameCount: number): number[] {
  if (durationMs <= 0 || frameCount <= 0) return [];
  if (frameCount === 1) return [0];
  const finalTimestamp = Math.max(0, durationMs - 1);
  return Array.from({ length: frameCount }, (_, index) => (
    Math.round(index * finalTimestamp / (frameCount - 1))
  ));
}
