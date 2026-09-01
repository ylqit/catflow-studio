import { describe, expect, it, vi } from "vitest";

import { DirectorDirtyCoordinator } from "../director/directorDirtyState";

describe("DirectorDirtyCoordinator", () => {
  it("blocks navigation when the user continues editing", async () => {
    const coordinator = new DirectorDirtyCoordinator();
    coordinator.register({ scope: "story", label: "剧情", save: vi.fn(), discard: vi.fn() });
    expect(await coordinator.resolve(async () => "continue")).toBe(false);
    expect(coordinator.active?.scope).toBe("story");
  });

  it("clears only after a successful save or explicit discard", async () => {
    const save = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const discard = vi.fn();
    const coordinator = new DirectorDirtyCoordinator();
    coordinator.register({ scope: "canon", label: "Canon", save, discard });
    expect(await coordinator.resolve(async () => "save")).toBe(false);
    expect(coordinator.active).toBeDefined();
    expect(await coordinator.resolve(async () => "save")).toBe(true);
    expect(coordinator.active).toBeUndefined();
    coordinator.register({ scope: "canon", label: "Canon", save, discard });
    expect(await coordinator.resolve(async () => "discard")).toBe(true);
    expect(discard).toHaveBeenCalledTimes(1);
  });
});
