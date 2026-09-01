import { describe, expect, it } from "vitest";

import { projectJobEvent } from "./projectJobEvents";

describe("projectJobEvent", () => {
  it("accepts an event only when it belongs to the active project", () => {
    const event = new MessageEvent("job.succeeded", {
      data: JSON.stringify({
        jobId: "job-1",
        projectId: "project-current",
        eventType: "job.succeeded",
      }),
      lastEventId: "42",
    });

    expect(projectJobEvent(event, "project-current")).toEqual({
      jobId: "job-1",
      projectId: "project-current",
      eventType: "job.succeeded",
      lastEventId: 42,
    });
    expect(projectJobEvent(event, "project-other")).toBeNull();
  });

  it("ignores malformed and unscoped event payloads", () => {
    expect(projectJobEvent(new MessageEvent("job.failed", { data: "not-json" }), "project-current")).toBeNull();
    expect(projectJobEvent(
      new MessageEvent("job.failed", { data: JSON.stringify({ jobId: "job-1" }) }),
      "project-current",
    )).toBeNull();
  });
});
