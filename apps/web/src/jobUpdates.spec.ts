import { afterEach, expect, it, vi } from "vitest";
vi.mock("./api/client", () => ({ api: { eventsUrl: (cursor: number) => `/events?cursor=${cursor}` } }));

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

it("shares one event connection, ignores old revisions and reconciles visibility", async () => {
  vi.resetModules(); vi.useFakeTimers();
  const sources: FakeSource[] = [];
  class FakeSource {
    handlers = new Map<string, (event: unknown) => void>();
    onopen?: () => void; onerror?: () => void;
    close = vi.fn();
    constructor() { sources.push(this); }
    addEventListener(name: string, handler: (event: unknown) => void) { this.handlers.set(name, handler); }
    send(id: number, revision: number) { this.handlers.get("job.succeeded")?.({ lastEventId: String(id), data: JSON.stringify({ jobId: "job", projectId: "project", revision }) }); }
  }
  vi.stubGlobal("EventSource", FakeSource);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  const { subscribeJobs } = await import("./jobUpdates");
  const first = vi.fn(async () => {}), second = vi.fn(async () => {});
  const stopA = subscribeJobs(() => ({ jobId: "job" }), first);
  const stopB = subscribeJobs(() => ({ projectId: "project" }), second);
  await Promise.resolve();
  expect(sources).toHaveLength(1);
  sources[0].send(1, 4); await Promise.resolve();
  expect(first).toHaveBeenCalledTimes(2); expect(second).toHaveBeenCalledTimes(2);
  sources[0].send(1, 4); sources[0].send(2, 3); await Promise.resolve();
  expect(first).toHaveBeenCalledTimes(2);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
  await vi.advanceTimersByTimeAsync(5000); expect(first).toHaveBeenCalledTimes(2);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  document.dispatchEvent(new Event("visibilitychange")); await Promise.resolve();
  expect(first).toHaveBeenCalledTimes(3);
  stopA(); expect(sources[0].close).not.toHaveBeenCalled();
  stopB(); expect(sources[0].close).toHaveBeenCalledOnce();
});
