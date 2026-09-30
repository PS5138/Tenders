// Browser-side helper for the JSON API. Every request carries the header the
// server requires to accept cookie-authenticated mutations.

export class ApiClientError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details?: unknown;

  constructor(code: string, message: string, status: number, details?: unknown) {
    super(message);
    this.name = 'ApiClientError';
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

type Options = {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';
  body?: unknown;
  formData?: FormData;
  signal?: AbortSignal;
};

export async function api<T>(path: string, { method = 'POST', body, formData, signal }: Options = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      signal,
      credentials: 'same-origin',
      headers: {
        'x-ten-request': '1',
        ...(formData ? {} : body !== undefined ? { 'content-type': 'application/json' } : {}),
      },
      body: formData ?? (body !== undefined ? JSON.stringify(body) : undefined),
    });
  } catch (err) {
    if ((err as Error)?.name === 'AbortError') throw err;
    throw new ApiClientError('NETWORK', 'Could not reach Ten. Check your connection and try again.', 0);
  }
  let payload: { ok: boolean; data?: T; error?: { code: string; message: string; details?: unknown } } | null = null;
  try {
    payload = await res.json();
  } catch {
    // Non-JSON responses fall through to the generic error below.
  }
  if (!res.ok || !payload?.ok) {
    const e = payload?.error;
    throw new ApiClientError(e?.code ?? 'HTTP_' + res.status, e?.message ?? `Request failed (${res.status}).`, res.status, e?.details);
  }
  if (method !== 'GET' && typeof window !== 'undefined') {
    window.dispatchEvent(new Event('ten:mutated'));
  }
  return payload.data as T;
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiClientError) return err.message;
  if (err instanceof Error) return err.message;
  return 'Something went wrong.';
}
