import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiTimeoutError, canvasApi, request } from "../client";

describe("API request lifecycle", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("aborts a hanging request after the default 15 second deadline", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    })));

    const assertion = expect(request("/health")).rejects.toBeInstanceOf(ApiTimeoutError);
    await vi.advanceTimersByTimeAsync(15_000);
    await assertion;
  });

  it("preserves an external abort signal instead of misreporting it as a timeout", async () => {
    const external = new AbortController();
    vi.stubGlobal("fetch", vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    })));

    const pending = request("/health", { signal: external.signal });
    external.abort("route changed");

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });

  it("lets a workspace shell load be cancelled when the project route changes", async () => {
    const external = new AbortController();
    vi.stubGlobal("fetch", vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    })));

    const pending = canvasApi.workspaceShell("project-1", external.signal);
    external.abort("project changed");

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });

  it("loads the direct workspace shell and fixed production flow read models", async () => {
    const fetchMock = vi.fn((url: string) => Promise.resolve(new Response(JSON.stringify(
      url.endsWith("/workspace-shell")
        ? {
          project: { id: "project-1", title: "Project", status: "active", updatedAt: "now" },
          modules: [],
          recommendedModuleId: "production",
          activeTaskSummary: { activeCount: 0, attentionCount: 0 },
        }
        : {
          nodes: [], edges: [], shotOrder: [], viewport: { x: 0, y: 0, zoom: 0.78 }, revision: 4,
        },
    ), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);

    const shell = await canvasApi.workspaceShell("project-1");
    const production = await canvasApi.productionFlow("project-1");

    expect(shell.recommendedModuleId).toBe("production");
    expect(production.revision).toBe(4);
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/v2/projects/project-1/workspace-shell",
      "/api/v2/projects/project-1/production-flow",
    ]);
  });
});
