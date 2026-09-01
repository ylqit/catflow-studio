import { defineStore } from "pinia";

export const useUiStore = defineStore("ui", {
  state: () => ({
    currentProjectId: null as string | null,
    sseConnected: false,
    lastEventId: 0,
    sidePanelOpen: true,
  }),
  actions: {
    setProject(projectId: string) {
      this.currentProjectId = projectId;
    },
  },
});
