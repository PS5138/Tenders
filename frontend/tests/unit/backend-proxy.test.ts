import { afterEach, beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  requireUser: vi.fn(),
  identity: vi.fn(),
  fetch: vi.fn(),
}));
vi.mock("@/server/auth", () => ({ requireUser: mocks.requireUser }));
vi.mock("@/server/backend/context", () => ({
  backendIdentity: mocks.identity,
}));
vi.mock("@/server/env", () => ({
  env: () => ({ APP_URL: "https://ten.example", APP_MODE: "test" }),
}));
vi.mock("@/server/backend/config", () => ({
  backendConfig: () => ({ origin: "http://api:8000" }),
}));
import { proxyBackend } from "@/server/backend/proxy";
import { AppError } from "@/server/errors";

const workspace = "11111111-1111-4111-8111-111111111111";
beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal("fetch", mocks.fetch);
  mocks.requireUser.mockResolvedValue({ id: "session-user" });
  mocks.identity.mockResolvedValue({ actor: "Roger", orgId: "server-org" });
  mocks.fetch.mockResolvedValue(Response.json([]));
});
afterEach(() => vi.unstubAllGlobals());

it("requires a session and business membership before any upstream request", async () => {
  mocks.requireUser.mockRejectedValue(
    new AppError("UNAUTHENTICATED", "Sign in."),
  );
  expect(
    (
      await proxyBackend(new Request("https://ten.example/api"), workspace, [
        "tenders",
      ])
    ).status,
  ).toBe(401);
  expect(mocks.fetch).not.toHaveBeenCalled();
  mocks.requireUser.mockResolvedValue({ id: "session-user" });
  mocks.identity.mockRejectedValue(
    new AppError("NOT_FOUND", "Workspace not found."),
  );
  expect(
    (
      await proxyBackend(new Request("https://ten.example/api"), workspace, [
        "tenders",
      ])
    ).status,
  ).toBe(404);
  expect(mocks.identity).toHaveBeenCalledWith("session-user", workspace);
  expect(mocks.fetch).not.toHaveBeenCalled();
});

it("refuses an actor supplied by the browser even when signed in", async () => {
  const response = await proxyBackend(
    new Request("https://ten.example/api", { headers: { "x-actor": "Puru" } }),
    workspace,
    ["tenders"],
  );
  expect(response.status).toBe(400);
  expect(mocks.fetch).not.toHaveBeenCalled();
});

it("never forwards a non-allow-listed path or a cross-origin write", async () => {
  expect(
    (
      await proxyBackend(new Request("https://ten.example/api"), workspace, [
        "openapi.json",
      ])
    ).status,
  ).toBe(404);
  expect(
    (
      await proxyBackend(
        new Request("https://ten.example/api", {
          method: "POST",
          headers: { origin: "https://evil.example", "x-ten-request": "1" },
        }),
        workspace,
        ["tenders"],
      )
    ).status,
  ).toBe(403);
  expect(mocks.fetch).not.toHaveBeenCalled();
});

it("forwards the canonical org and preserves an upstream cross-org 404", async () => {
  mocks.fetch.mockResolvedValue(
    Response.json({ detail: "Tender not found." }, { status: 404 }),
  );
  const result = await proxyBackend(
    new Request("https://ten.example/api"),
    workspace,
    ["tenders", workspace],
  );
  expect(result.status).toBe(404);
  const [, options] = mocks.fetch.mock.calls[0];
  expect(options.headers.get("x-org-id")).toBe("server-org");
  expect(options.headers.get("x-actor")).toBe("Roger");
});
