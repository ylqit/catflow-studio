import { ref } from "vue";
import { api } from "./api/client";

type Scope = { projectId?: string | null; seriesId?: string | null; storySourceDocumentId?: string | null; jobId?: string | null };
type Subscriber = { scope: () => Scope; refresh: () => Promise<unknown>; active: () => boolean; busy: boolean; again: boolean };
const subscribers = new Set<Subscriber>();
const revisions = new Map<string, number>();
export const jobsConnected = ref(false);
let source: EventSource | null = null;
let cursor = 0;
let timer: ReturnType<typeof setInterval> | undefined;
let listening = false;

async function refresh(subscriber: Subscriber) {
  if (subscriber.busy) { subscriber.again = true; return; }
  subscriber.busy = true;
  try { await subscriber.refresh(); }
  catch { /* Keep the last known snapshot; visibility and polling reconcile it. */ }
  finally {
    subscriber.busy = false;
    if (subscriber.again && subscribers.has(subscriber)) {
      subscriber.again = false;
      void refresh(subscriber);
    }
  }
}

function reconcileVisible() {
  if (document.visibilityState === "visible") for (const item of subscribers) void refresh(item);
}

function connect() {
  listening = true;
  if (typeof EventSource !== "undefined") {
  source = new EventSource(api.eventsUrl(cursor));
  source.onopen = () => { jobsConnected.value = true; reconcileVisible(); };
  source.onerror = () => { jobsConnected.value = false; };
  const receive = (event: MessageEvent) => {
    const incoming = Number(event.lastEventId);
    if (!Number.isSafeInteger(incoming) || incoming <= cursor) return;
    cursor = incoming;
    let data: Record<string, unknown>;
    try { data = JSON.parse(event.data); } catch { return; }
    const jobId = String(data.jobId ?? "");
    const revision = Number(data.revision ?? 0);
    if (revision && revision < (revisions.get(jobId) ?? 0)) return;
    revisions.set(jobId, revision);
    for (const item of subscribers) {
      if (Object.entries(item.scope()).some(([key, value]) => value && value === data[key])) void refresh(item);
    }
  };
  for (const type of ["job.queued", "job.submitting", "job.submission_started", "job.submitted", "job.polling", "job.storing", "job.succeeded", "job.failed", "job.submission_unknown", "job.cancel_requested", "job.cancelled", "job.received", "job.receipt_conflict", "job.query_deferred", "job.recovery_requested", "job.local_recovery", "job.historical_result", "job.late_receipt_recovery", "job.result_recovered", "planner.proposal.created"]) {
    source.addEventListener(type, receive as EventListener);
  }
  }
  timer = setInterval(() => {
    if (document.visibilityState === "visible") for (const item of subscribers) if (item.active()) void refresh(item);
  }, 5000);
  document.addEventListener("visibilitychange", reconcileVisible);
}

export function subscribeJobs(scope: () => Scope, reload: () => Promise<unknown>, active: () => boolean = () => true) {
  const item: Subscriber = { scope, refresh: reload, active, busy: false, again: false };
  subscribers.add(item);
  if (!listening) connect(); // Subscribe before reading a snapshot; poll if SSE is unavailable.
  void refresh(item);
  return () => {
    subscribers.delete(item);
    if (!subscribers.size) {
      source?.close(); source = null; jobsConnected.value = false;
      listening = false;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", reconcileVisible);
    }
  };
}
