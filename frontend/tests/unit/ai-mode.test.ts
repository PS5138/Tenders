import { describe, expect, it } from "vitest";
import { aiMode, type BackendHealth } from "@/server/backend/health";

const live: BackendHealth = { status: "ok", llm_provider: "anthropic", embedding_provider: "openai", synthetic_demo: false };
const keyless: BackendHealth = { status: "ok", llm_provider: "fake", embedding_provider: "fake", synthetic_demo: true };

describe("aiMode", () => {
  it("keeps a synthetic business synthetic even when the backend runs live providers", () => {
    expect(aiMode(live, true)).toBe("synthetic");
    expect(aiMode(keyless, true)).toBe("synthetic");
  });

  it("calls a live business live only when the backend has real providers", () => {
    expect(aiMode(live, false)).toBe("live");
    expect(aiMode({ ...live, embedding_provider: "voyage" }, false)).toBe("live");
    expect(aiMode(keyless, false)).toBe("no-keys");
  });

  it("reports an unreachable backend or an unknown business as unavailable", () => {
    expect(aiMode(null, false)).toBe("unavailable");
    expect(aiMode({ status: "down" }, false)).toBe("unavailable");
    expect(aiMode(live, null)).toBe("unavailable");
  });
});
