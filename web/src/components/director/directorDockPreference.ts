import { ref, watch } from "vue";

function storageKey(projectId: string, dockId: string): string {
  return `cat-video-director:${projectId}:dock:${dockId}`;
}

export function useDirectorDockPreference(
  projectId: () => string,
  dockId: string,
  defaultOpen = true,
) {
  const open = ref(defaultOpen);
  let currentKey = "";
  let restoring = false;

  const stopProjectWatch = watch(projectId, (value) => {
    currentKey = value ? storageKey(value, dockId) : "";
    restoring = true;
    try {
      const saved = currentKey ? window.localStorage.getItem(currentKey) : null;
      open.value = saved === null ? defaultOpen : saved === "open";
    } catch {
      open.value = defaultOpen;
    } finally {
      restoring = false;
    }
  }, { immediate: true });

  const stopOpenWatch = watch(open, (value) => {
    if (restoring || !currentKey) return;
    try {
      window.localStorage.setItem(currentKey, value ? "open" : "closed");
    } catch {
      // Browser privacy policies can disable storage. The dock remains usable in memory.
    }
  });

  function stop() {
    stopOpenWatch();
    stopProjectWatch();
  }

  return { open, stop };
}
