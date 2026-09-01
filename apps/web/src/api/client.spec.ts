import { afterEach, describe, expect, it, vi } from "vitest";

import { CatFlowClient } from "./client";

describe("CatFlowClient", () => {
  afterEach(() => vi.restoreAllMocks());

  it("bootstraps csrf and sends it only to the same-origin v1 API", async () => {
    const fetchMock = vi
      .spyOn(window, "fetch")
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            csrfToken: "csrf-1",
            baseUrl: "http://127.0.0.1:8877",
            localOnly: true,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify([]), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    const client = new CatFlowClient();

    await client.bootstrap();
    await client.createProject({ title: "纸星星", theme: "窗边折纸", targetDurationSeconds: 10 });

    const [url, options] = fetchMock.mock.calls[1];
    expect(url).toBe("/api/v1/projects");
    expect((options?.headers as Record<string, string>)["X-CatFlow-CSRF"]).toBe("csrf-1");
    expect(String(url)).not.toContain("/api/v2");
  });
});
