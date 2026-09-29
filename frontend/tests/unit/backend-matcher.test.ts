import { expect, it } from "vitest";
// The bundled guide calls this doesProxyMatch, but 16.3.6 still exports the old
// test helper name. Use the installed API rather than relying on the guide's alias.
import { unstable_doesMiddlewareMatch as unstable_doesProxyMatch } from "next/experimental/testing/server";
import { config } from "@/proxy";

it("keeps uploads and streams outside Next proxy while protecting pages", () => {
  for (const url of [
    "/api/w/business/backend/fixtures/draft",
    "/api/w/business/backend/documents",
    "/api/w/business/uploads",
  ]) {
    expect(unstable_doesProxyMatch({ config, nextConfig: {}, url })).toBe(
      false,
    );
  }
  for (const url of [
    "/w/business/tenders",
    "/api/w/business/team",
    "/account",
  ]) {
    expect(unstable_doesProxyMatch({ config, nextConfig: {}, url })).toBe(true);
  }
});
