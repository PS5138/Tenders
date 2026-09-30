import 'server-only';

export function backendConfig() {
  const value = process.env.BACKEND_URL;
  if (!value) throw new Error('BACKEND_URL is required for the joined backend.');
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname !== '/') {
    throw new Error('BACKEND_URL must be an HTTP(S) origin without credentials, a path or a query.');
  }
  const secret = process.env.BACKEND_SERVICE_SECRET;
  if (process.env.APP_MODE === 'demo' && (!secret || secret.length < 32)) {
    throw new Error('Demo mode requires a BACKEND_SERVICE_SECRET of at least 32 characters.');
  }
  return { origin: url.origin, secret };
}
