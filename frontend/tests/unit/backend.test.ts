import { afterEach, describe, expect, it, vi } from "vitest";
import {
  allowedBackendRequest,
  browserIdentityHeader,
  configuredOrigin,
  isSameOriginMutation,
  mutationOriginProblem,
} from "@/server/backend/policy";
import { backendFetch, passthroughResponse } from "@/server/backend/transport";
import {
  applyDraftEvent,
  codePointRange,
  consumeDraftStream,
  emptyDraft,
  type DraftEvent,
} from "@/lib/backend-stream";
import { assertLiveBackend } from "@/server/backend/health";

const id = "11111111-1111-4111-8111-111111111111";
afterEach(() => vi.unstubAllGlobals());

describe("private backend boundary", () => {
  it("allows only explicit paths, methods and query parameters", () => {
    expect(
      allowedBackendRequest("GET", "/tenders", new URLSearchParams(), false),
    ).toBe(true);
    expect(
      allowedBackendRequest(
        "POST",
        `/questions/${id}/draft`,
        new URLSearchParams(),
        false,
      ),
    ).toBe(true);
    for (const path of [
      "/health",
      "/openapi.json",
      "/organisations",
      "//example.com/tenders",
      "/tenders/../documents",
      "/tenders%2F..",
      "/tenders/",
    ]) {
      expect(
        allowedBackendRequest("GET", path, new URLSearchParams(), true),
      ).toBe(false);
    }
    expect(
      allowedBackendRequest("DELETE", "/tenders", new URLSearchParams(), true),
    ).toBe(false);
    expect(
      allowedBackendRequest(
        "GET",
        "/tenders",
        new URLSearchParams("org_id=other"),
        true,
      ),
    ).toBe(false);
    expect(
      allowedBackendRequest(
        "GET",
        `/tenders/${id}/export`,
        new URLSearchParams("mode=review&mode=submission"),
        true,
      ),
    ).toBe(false);
    expect(
      allowedBackendRequest(
        "POST",
        "/fixtures/draft",
        new URLSearchParams("fail_after=3"),
        false,
      ),
    ).toBe(false);
    expect(
      allowedBackendRequest(
        "POST",
        "/fixtures/draft",
        new URLSearchParams("fail_after=3"),
        true,
      ),
    ).toBe(true);
  });

  it("allows the specification requirements routes, UUID-anchored, with their methods only", () => {
    const none = new URLSearchParams();
    expect(allowedBackendRequest("GET", `/tenders/${id}/requirements`, none, false)).toBe(true);
    expect(allowedBackendRequest("POST", `/tenders/${id}/requirements/rescan`, none, false)).toBe(true);
    expect(allowedBackendRequest("PATCH", `/requirements/${id}`, none, false)).toBe(true);
    for (const [method, path] of [
      ["POST", `/tenders/${id}/requirements`],
      ["GET", `/tenders/${id}/requirements/rescan`],
      ["DELETE", `/requirements/${id}`],
      ["GET", `/requirements/${id}`],
      ["PATCH", "/requirements/not-a-uuid"],
      ["PATCH", `/requirements/${id}/extra`],
      ["GET", "/tenders/not-a-uuid/requirements"],
    ])
      expect(allowedBackendRequest(method, path, none, true)).toBe(false);
    expect(
      allowedBackendRequest("GET", `/tenders/${id}/requirements`, new URLSearchParams("rag=red"), true),
    ).toBe(false);
  });

  it("refuses supplied identity and requires the exact configured origin on writes", () => {
    for (const name of [
      "X-Actor",
      "X-Org-ID",
      "Authorization",
      "Proxy-Authorization",
    ])
      expect(
        browserIdentityHeader(new Headers({ [name]: "spoof" })),
      ).toBeTruthy();
    const request = (origin?: string) =>
      new Request("https://ten.example/api", {
        method: "POST",
        headers: {
          "x-ten-request": "1",
          "x-forwarded-host": "evil.example",
          ...(origin ? { origin } : {}),
        },
      });
    expect(
      isSameOriginMutation(
        request("https://ten.example"),
        "https://ten.example",
      ),
    ).toBe(true);
    for (const origin of [
      undefined,
      "http://ten.example",
      "https://evil.example",
      "null",
    ])
      expect(isSameOriginMutation(request(origin), "https://ten.example")).toBe(
        false,
      );
  });

  it("says which origin arrived and which APP_URL expects, without reflecting junk", () => {
    const headers = (origin?: string) =>
      new Headers({ "x-ten-request": "1", ...(origin ? { origin } : {}) });
    expect(
      mutationOriginProblem(headers("https://ten.example"), "https://ten.example/"),
    ).toBeNull();
    const mismatch = mutationOriginProblem(
      headers("http://localhost:3000"),
      "https://user:secret@ten.example/app?x=1",
    );
    expect(mismatch).toContain("http://localhost:3000");
    expect(mismatch).toContain("https://ten.example");
    expect(mismatch).toContain("APP_URL");
    expect(mismatch).not.toContain("secret");
    expect(mutationOriginProblem(headers(), "https://ten.example")).toContain(
      "no Origin header",
    );
    const junk = mutationOriginProblem(
      headers(`https://evil.ex\u00e4mple${"a".repeat(500)}`),
      "https://ten.example",
    );
    expect(junk).not.toContain("\u00e4");
    expect(junk!.length).toBeLessThan(500);
    expect(
      mutationOriginProblem(new Headers({ origin: "https://ten.example" }), "https://ten.example"),
    ).toBe("This request must come from the Ten application.");
    expect(configuredOrigin("https://user:secret@ten.example/app")).toBe("https://ten.example");
    expect(configuredOrigin("not a url")).toBeNull();
  });

  it("injects trusted headers and passes the original upload stream without reading it", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response("{}"));
    vi.stubGlobal("fetch", fetch);
    const upload = new ReadableStream<Uint8Array>({ pull: () => {} });
    await backendFetch(
      { origin: "http://api:8000", secret: "server-secret" },
      { actor: "Roger", orgId: id },
      "/documents",
      {
        method: "POST",
        body: upload,
        contentType: "multipart/form-data; boundary=abc",
      },
    );
    const [url, options] = fetch.mock.calls[0];
    expect(String(url)).toBe("http://api:8000/documents");
    expect(options.body).toBe(upload);
    expect(upload.locked).toBe(false);
    expect(options.duplex).toBe("half");
    expect(options.redirect).toBe("manual");
    expect(options.headers.get("x-actor")).toBe("Roger");
    expect(options.headers.get("x-org-id")).toBe(id);
    expect(options.headers.get("authorization")).toBe("Bearer server-secret");
    expect(options.headers.get("cookie")).toBeNull();
  });

  it("forwards the first NDJSON chunk before the upstream finishes and keeps download metadata", async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        controller = c;
      },
    });
    const result = passthroughResponse(
      new Response(body, {
        headers: {
          "content-type": "application/x-ndjson",
          "content-disposition": 'attachment; filename="test.docx"',
          "set-cookie": "secret=bad",
        },
      }),
    );
    expect(result.body).toBe(body);
    expect(result.headers.get("set-cookie")).toBeNull();
    expect(result.headers.get("content-disposition")).toContain("test.docx");
    const reader = result.body!.getReader();
    controller.enqueue(new TextEncoder().encode('{"type":"segment"}\n'));
    expect(new TextDecoder().decode((await reader.read()).value)).toContain(
      "segment",
    );
    controller.close();
    expect((await reader.read()).done).toBe(true);
  });

  it("preserves 409 details and refuses redirects", async () => {
    const error = {
      detail: "Confirm displacement.",
      code: "displacement",
      current_answer: { id },
    };
    const result = passthroughResponse(
      Response.json(error, { status: 409, headers: { "x-document-id": id } }),
    );
    expect(result.status).toBe(409);
    expect(await result.json()).toEqual(error);
    expect(result.headers.get("x-document-id")).toBe(id);
    const redirect = passthroughResponse(
      new Response(null, {
        status: 302,
        headers: { location: "https://evil.example" },
      }),
    );
    expect(redirect.status).toBe(502);
    expect(redirect.headers.get("location")).toBeNull();
  });

  it("refuses paths that URL resolution would rewrite", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const connection = { origin: "http://api:8000" };
    const identity = { actor: "Valerie", orgId: id };
    for (const path of [
      "/tenders/../../fixtures/answer/export",
      "/tenders/./x",
      "/tenders/%2e%2e/fixtures/answer",
      `/tenders/${id}\\export`,
      "//evil.example/tenders",
    ])
      await expect(backendFetch(connection, identity, path)).rejects.toThrow(
        "Invalid backend path.",
      );
    expect(fetch).not.toHaveBeenCalled();
    await backendFetch(connection, identity, `/tenders/${id}/export?format=docx&mode=review%20copy`);
    expect(fetch).toHaveBeenCalledOnce();
  });
});

describe("NDJSON and trace offsets", () => {
  it("handles split UTF-8, multiple events per chunk and a final line without newline", async () => {
    const bytes = new TextEncoder().encode(
      '{"type":"gaps","gaps":["😀 café"]}\r\n{"type":"error","code":"fixture_failure","message":"Expected failure"}',
    );
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        for (const byte of bytes) c.enqueue(Uint8Array.of(byte));
        c.close();
      },
    });
    const events: DraftEvent[] = [];
    await consumeDraftStream(stream, (event) => events.push(event));
    expect(events).toHaveLength(2);
    expect(events[0]).toEqual({ type: "gaps", gaps: ["😀 café"] });
    expect(events[1].type).toBe("error");
  });
  it("detects disconnects and invalid events instead of reporting success", async () => {
    for (const line of [
      '{"type":"gaps","gaps":[]}\n',
      "{bad}\n",
      '{"type":"unknown"}\n',
    ]) {
      const stream = new Response(line).body!;
      await expect(consumeDraftStream(stream, () => {})).rejects.toThrow();
    }
  });
  it("replaces pending sources with verified offsets and clears partial failed output", () => {
    let state = applyDraftEvent(emptyDraft(), {
      type: "segment",
      index: 0,
      paragraph: 0,
      text: "A claim.",
      kind: "substantive",
      support_status: "pending",
      sources: [],
    });
    const sources = [
      {
        source_type: "human_attestation" as const,
        attested_by: "Puru",
        at: "2026-09-29T12:00:00Z",
      },
    ];
    state = applyDraftEvent(state, {
      type: "support",
      index: 0,
      support_status: "supported",
      sources,
    });
    expect(state.segments[0].sources).toEqual(sources);
    expect(state.segments[0].support_status).toBe("supported");
    state = applyDraftEvent(state, {
      type: "error",
      code: "fixture_failure",
      message: "Failed",
    });
    expect(state.segments).toEqual([]);
    expect(state.result).toBeNull();
    expect(state.error).toBe("Failed");
  });
  it("converts code-point offsets without splitting surrogate pairs or silently clamping bad offsets", () => {
    const text = "A😀B e\u0301";
    expect(codePointRange(text, 1, 3)).toEqual({ start: 1, end: 4 });
    expect(text.slice(1, 4)).toBe("😀B");
    expect(codePointRange(text, 4, 6)).toEqual({ start: 5, end: 7 });
    for (const [start, end] of [
      [-1, 3],
      [0, 100],
      [1.5, 2],
      [2, 1],
    ])
      expect(codePointRange(text, start, end)).toBeNull();
  });
});

it("fails closed on fake or unreported backend providers and missing service protection", () => {
  expect(() => assertLiveBackend({ status: "ok" })).toThrow();
  expect(() =>
    assertLiveBackend({
      status: "ok",
      llm_provider: "fake",
      embedding_provider: "openai",
      service_secret_enabled: true,
    }),
  ).toThrow();
  expect(() =>
    assertLiveBackend({
      status: "ok",
      llm_provider: "anthropic",
      embedding_provider: "fake",
      service_secret_enabled: true,
    }),
  ).toThrow();
  expect(() =>
    assertLiveBackend({
      status: "ok",
      llm_provider: "anthropic",
      embedding_provider: "openai",
    }),
  ).toThrow();
  expect(() =>
    assertLiveBackend({
      status: "ok",
      llm_provider: "anthropic",
      embedding_provider: "openai",
      service_secret_enabled: true,
    }),
  ).not.toThrow();
});
