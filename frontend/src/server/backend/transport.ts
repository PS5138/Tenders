import 'server-only';
import type { BackendIdentity } from './context';

export type BackendConnection = { origin: string; secret?: string };

/** Only this function constructs upstream headers. Cookies and browser identity never cross. */
export async function backendFetch(
  connection: BackendConnection,
  identity: BackendIdentity,
  path: string,
  init: { method?: string; body?: BodyInit | null; contentType?: string; signal?: AbortSignal } = {},
): Promise<Response> {
  // Callers interpolate route parameters into paths, so refuse anything URL resolution could
  // rewrite (dot segments, encoded characters) rather than trust every caller to validate.
  const pathname = path.split('?')[0];
  if (
    !path.startsWith('/') ||
    path.startsWith('//') ||
    /[\\#%]/.test(pathname) ||
    pathname.split('/').some((segment) => segment === '.' || segment === '..')
  )
    throw new Error('Invalid backend path.');
  const url = new URL(path, connection.origin);
  if (url.origin !== connection.origin) throw new Error('Invalid backend origin.');
  const headers = new Headers({
    'x-actor': identity.actor,
    'x-org-id': identity.orgId,
    accept: 'application/json, application/x-ndjson',
  });
  if (connection.secret) headers.set('authorization', `Bearer ${connection.secret}`);
  if (init.contentType) headers.set('content-type', init.contentType);
  // Node fetch requires duplex for streaming request bodies. No request.json(), formData()
  // or arrayBuffer() here: multipart files and response streams remain streaming end to end.
  const options: RequestInit & { duplex?: 'half' } = {
    method: init.method ?? 'GET',
    headers,
    body: init.body,
    cache: 'no-store',
    redirect: 'manual',
    signal: init.signal,
    ...(init.body instanceof ReadableStream ? { duplex: 'half' as const } : {}),
  };
  return fetch(url, options);
}

export function passthroughResponse(upstream: Response): Response {
  const headers = new Headers({
    'cache-control': 'private, no-store, no-transform',
    'x-content-type-options': 'nosniff',
    'x-accel-buffering': 'no',
  });
  for (const name of ['content-type', 'content-disposition', 'x-document-id', 'retry-after']) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  // Do not forward Location, Set-Cookie, CORS or hop-by-hop headers from the private service.
  if (upstream.status >= 300 && upstream.status < 400) {
    void upstream.body?.cancel();
    return Response.json({ detail: 'The backend returned an unexpected redirect.' }, { status: 502, headers });
  }
  return new Response(upstream.body, { status: upstream.status, headers });
}
