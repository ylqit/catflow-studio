import { describe, expect, it } from "vitest";

import { routes } from "./router";

describe("CatFlow web routes", () => {
  it("exposes exactly the five creator workspaces for a project", () => {
    const projectRoutes = routes
      .filter((route) => String(route.path).startsWith("/projects/:projectId/"))
      .map((route) => String(route.path).split("/").at(-1));

    expect(projectRoutes).toEqual(["planner", "assets", "storyboard", "generation", "delivery"]);
  });

  it("contains no desktop or legacy canvas routes", () => {
    const paths = routes.map((route) => String(route.path));
    expect(paths.some((path) => path.includes("desktop"))).toBe(false);
    expect(paths.some((path) => path.includes("canvas"))).toBe(false);
    expect(paths.some((path) => path.includes("studio"))).toBe(false);
  });
});
