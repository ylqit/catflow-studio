import { readonly, ref } from "vue";

import { api } from "./api/client";
import type { HealthDto, RuntimeSettingsDto } from "./api/types";

const health = ref<HealthDto | null>(null);
const settings = ref<RuntimeSettingsDto | null>(null);
const unreachable = ref(false);
let timer: number | undefined;
let started = false;

export async function refreshRuntimeStatus() {
  try {
    const [nextHealth, nextSettings] = await Promise.all([
      api.health(),
      api.runtimeSettings(),
    ]);
    health.value = nextHealth;
    settings.value = nextSettings;
    unreachable.value = false;
  } catch {
    unreachable.value = true;
  }
}

export function startRuntimeStatus() {
  if (started || typeof window === "undefined") return;
  started = true;
  void refreshRuntimeStatus();
  timer = window.setInterval(refreshRuntimeStatus, 30_000);
}

export function stopRuntimeStatus() {
  if (!started || typeof window === "undefined") return;
  started = false;
  if (timer !== undefined) window.clearInterval(timer);
  timer = undefined;
}

export function useRuntimeStatus() {
  return {
    health: readonly(health),
    settings: readonly(settings),
    unreachable: readonly(unreachable),
  };
}
