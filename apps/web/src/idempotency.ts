interface PendingIdempotency {
  fingerprint: string;
  key: string;
}

const memory = new Map<string, PendingIdempotency>();

function storageKey(scope: string) {
  return `catflow:pending-idempotency:${scope}`;
}

function readPending(scope: string): PendingIdempotency | null {
  if (typeof window === "undefined") return memory.get(scope) ?? null;
  const raw = window.sessionStorage.getItem(storageKey(scope));
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<PendingIdempotency>;
    return typeof parsed.fingerprint === "string" && typeof parsed.key === "string"
      ? { fingerprint: parsed.fingerprint, key: parsed.key }
      : null;
  } catch {
    window.sessionStorage.removeItem(storageKey(scope));
    return null;
  }
}

export function pendingIdempotencyKey(scope: string, fingerprint: string): string {
  const pending = readPending(scope);
  if (pending?.fingerprint === fingerprint) return pending.key;
  const created = { fingerprint, key: crypto.randomUUID() };
  memory.set(scope, created);
  if (typeof window !== "undefined") {
    window.sessionStorage.setItem(storageKey(scope), JSON.stringify(created));
  }
  return created.key;
}

export function settleIdempotencyKey(scope: string, fingerprint: string): void {
  const pending = readPending(scope);
  if (pending?.fingerprint !== fingerprint) return;
  memory.delete(scope);
  if (typeof window !== "undefined") window.sessionStorage.removeItem(storageKey(scope));
}
