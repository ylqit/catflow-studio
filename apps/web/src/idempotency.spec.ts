import { beforeEach, describe, expect, it, vi } from "vitest";

import { pendingIdempotencyKey, settleIdempotencyKey } from "./idempotency";

describe("pending paid-request idempotency", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.spyOn(crypto, "randomUUID")
      .mockReturnValueOnce("00000000-0000-4000-8000-000000000001")
      .mockReturnValueOnce("00000000-0000-4000-8000-000000000002");
  });

  it("reuses a key across component reloads while the same input is unresolved", () => {
    const first = pendingIdempotencyKey("video:project-1", "hash-a");
    const recovered = pendingIdempotencyKey("video:project-1", "hash-a");

    expect(recovered).toBe(first);
    expect(crypto.randomUUID).toHaveBeenCalledTimes(1);
  });

  it("creates a new key after a response settles or the frozen input changes", () => {
    const first = pendingIdempotencyKey("video:project-1", "hash-a");
    settleIdempotencyKey("video:project-1", "hash-a");
    const next = pendingIdempotencyKey("video:project-1", "hash-b");

    expect(next).not.toBe(first);
  });
});
