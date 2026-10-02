export class BackendRequestError extends Error {
  constructor(
    readonly status: number,
    readonly detail: Record<string, unknown>,
  ) {
    const message =
      typeof detail.detail === 'string'
        ? detail.detail
        : typeof detail.error === 'object' && detail.error && 'message' in detail.error
          ? String(detail.error.message)
          : status === 413
            ? 'This file is too large.'
            : 'The request could not be completed.';
    super(message);
  }
}

export function backendUrl(workspaceId: string, path: string): string {
  return `/api/w/${encodeURIComponent(workspaceId)}/backend${path}`;
}

export type BackendRequestOptions = { signal?: AbortSignal };

export async function backendRequest(
  workspaceId: string,
  path: string,
  method = 'GET',
  body?: unknown,
  { signal }: BackendRequestOptions = {},
): Promise<Response> {
  const multipart = body instanceof FormData;
  const response = await fetch(backendUrl(workspaceId, path), {
    method,
    signal,
    credentials: 'same-origin',
    cache: 'no-store',
    headers: {
      'x-ten-request': '1',
      ...(!multipart && body !== undefined ? { 'content-type': 'application/json' } : {}),
    },
    body: multipart ? body : body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new BackendRequestError(response.status, await response.json().catch(() => ({})));
  if (method !== 'GET' && typeof window !== 'undefined') window.dispatchEvent(new Event('ten:mutated'));
  return response;
}

export async function backendJson<T>(
  workspaceId: string,
  path: string,
  method = 'GET',
  body?: unknown,
  options: BackendRequestOptions = {},
): Promise<T> {
  return (await backendRequest(workspaceId, path, method, body, options)).json();
}
