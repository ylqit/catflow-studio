export interface ProjectJobEvent {
  jobId: string;
  projectId: string;
  eventType: string;
  lastEventId: number | null;
}

export function projectJobEvent(event: MessageEvent, activeProjectId: string): ProjectJobEvent | null {
  let payload: unknown;
  try {
    payload = JSON.parse(event.data) as unknown;
  } catch {
    return null;
  }
  if (!payload || typeof payload !== "object") return null;

  const candidate = payload as Record<string, unknown>;
  if (
    candidate.projectId !== activeProjectId
    || typeof candidate.jobId !== "string"
    || typeof candidate.eventType !== "string"
  ) {
    return null;
  }

  const parsedEventId = Number(event.lastEventId);
  return {
    jobId: candidate.jobId,
    projectId: candidate.projectId,
    eventType: candidate.eventType,
    lastEventId: event.lastEventId && Number.isSafeInteger(parsedEventId) ? parsedEventId : null,
  };
}
