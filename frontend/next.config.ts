import type { NextConfig } from 'next';

const securityHeaders = [
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'same-origin' },
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'Content-Security-Policy', value: "frame-ancestors 'none'; object-src 'none'; base-uri 'self'" },
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
];

const nextConfig: NextConfig = {
  // Preserve incremental NDJSON chunks across the private API bridge.
  compress: false,
  output: 'standalone',
  poweredByHeader: false,
  devIndicators: false,
  serverExternalPackages: ['postgres'],
  // Keep local data, fixtures and tooling out of the deployable server bundle.
  outputFileTracingExcludes: {
    '/*': ['.local/**', '.env*', 'private/**', 'fixtures/**', 'tests/**', 'docs/**', 'scripts/**', 'supabase/**', 'test-results/**', 'playwright-report/**'],
  },
  experimental: {
    // Safety net if an upload ever passes through proxy.ts: never truncate a 20 MB file.
    proxyClientMaxBodySize: '25mb',
  },
  async headers() {
    return [{ source: '/:path*', headers: securityHeaders }];
  },
};

export default nextConfig;
