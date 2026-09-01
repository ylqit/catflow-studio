import { beforeEach, describe, expect, it } from "vitest";
import { nextTick, ref } from "vue";

import { useDirectorDockPreference } from "../director/directorDockPreference";

describe("director dock preference", () => {
  beforeEach(() => window.localStorage.clear());

  it("persists a dock choice per project and restores it after remount", async () => {
    const projectId = ref("project-1");
    const first = useDirectorDockPreference(() => projectId.value, "assistant");
    first.open.value = false;
    await nextTick();
    expect(window.localStorage.getItem("cat-video-director:project-1:dock:assistant")).toBe("closed");
    first.stop();

    const restored = useDirectorDockPreference(() => projectId.value, "assistant");
    expect(restored.open.value).toBe(false);
    restored.stop();
  });

  it("keeps different project preferences independent", async () => {
    const projectId = ref("project-1");
    const preference = useDirectorDockPreference(() => projectId.value, "assistant");
    preference.open.value = false;
    await nextTick();

    projectId.value = "project-2";
    await nextTick();
    expect(preference.open.value).toBe(true);
    preference.open.value = false;
    await nextTick();

    projectId.value = "project-1";
    await nextTick();
    expect(preference.open.value).toBe(false);
    preference.stop();
  });
});
