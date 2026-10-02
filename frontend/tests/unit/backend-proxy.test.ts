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
  env: () => ({
    APP_URL: "https://ten.example",
    APP_MODE: "test",
    MAX_UPLOAD_BYTES: 1024,
  }),
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

it("explains a refused cross-origin write by naming both origins", async () => {
  const response = await proxyBackend(
    new Request("https://ten.example/api", {
      method: "POST",
      headers: { origin: "http://127.0.0.1:3000", "x-ten-request": "1" },
    }),
    workspace,
    ["tenders"],
  );
  expect(response.status).toBe(403);
  const { detail } = await response.json();
  expect(detail).toContain("http://127.0.0.1:3000");
  expect(detail).toContain("https://ten.example");
  expect(detail).toContain("APP_URL");
  expect(mocks.fetch).not.toHaveBeenCalled();
});

const upload = (
  path: string[],
  body: BodyInit | null,
  headers: Record<string, string> = {},
) =>
  new Request("https://ten.example/api", {
    method: "POST",
    body,
    // Node needs duplex for a streaming request body.
    ...(body instanceof ReadableStream ? { duplex: "half" } : {}),
    headers: {
      origin: "https://ten.example",
      "x-ten-request": "1",
      "content-type": "multipart/form-data; boundary=abc",
      ...headers,
    },
  } as RequestInit);

it("refuses an upload whose declared size is over the limit before reading it", async () => {
  for (const path of [["documents"], ["tenders", workspace, "documents"]]) {
    const response = await proxyBackend(
      upload(path, null, { "content-length": "2048" }),
      workspace,
      path,
    );
    expect(response.status).toBe(413);
    expect((await response.json()).detail).toMatch(/too large/);
  }
  expect(mocks.fetch).not.toHaveBeenCalled();
});

it("counts an undeclared upload body and aborts the upstream request past the limit", async () => {
  // The mocked upstream consumes the body as fetch would; the counting stream fails it.
  mocks.fetch.mockImplementation(async (_url: unknown, options: RequestInit) => {
    await new Response(options.body as BodyInit).arrayBuffer();
    return Response.json({ id: workspace });
  });
  const chunks = (count: number) =>
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (let i = 0; i < count; i += 1)
          controller.enqueue(new Uint8Array(300));
        controller.close();
      },
    });
  const tooLarge = await proxyBackend(
    upload(["documents"], chunks(5)),
    workspace,
    ["documents"],
  );
  expect(tooLarge.status).toBe(413);
  expect((await tooLarge.json()).detail).toMatch(/too large/);
  const fits = await proxyBackend(
    upload(["documents"], chunks(3)),
    workspace,
    ["documents"],
  );
  expect(fits.status).toBe(200);
  expect(await fits.json()).toEqual({ id: workspace });
  const [, options] = mocks.fetch.mock.calls[0];
  expect(options.body).toBeInstanceOf(ReadableStream);
  expect(options.duplex).toBe("half");
});
