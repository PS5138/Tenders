import 'server-only';
import { headers } from 'next/headers';
import { z, type ZodType } from 'zod';
import { AppError, invalid } from './errors';
import { requireUser, type SessionUser } from './auth';
import { env } from './env';
import { BackendError } from './backend/client';

export type ApiError = { code: string; message: string; details?: unknown };
export type ApiResult<T> = { ok: true; data: T } | { ok: false; error: ApiError };

export function errorResponse(err: unknown): Response {
  if (err instanceof BackendError)
    return Response.json(
      { ok: false, error: { code: `BACKEND_${err.status}`, message: err.message, details: err.body } },
      { status: err.status },
    );
  if (err instanceof AppError) {
    return Response.json({ ok: false, error: { code: err.code, message: err.message, details: err.details } } satisfies ApiResult<never>, {
      status: err.status,
    });
  }
  const pgCode = (err as { code?: string })?.code;
  if (pgCode === '23505' || pgCode === '23514') {
    return Response.json(
      { ok: false, error: { code: 'CONFLICT', message: 'Check that the display name is valid and unique within each business.' } },
      { status: 409 },
    );
  }
  if (pgCode === '42501') {
    return Response.json({ ok: false, error: { code: 'FORBIDDEN', message: 'You do not have permission to do that.' } }, { status: 403 });
  }
  console.error('[api] unexpected error', err);
  return Response.json(
    { ok: false, error: { code: 'INTERNAL', message: 'Something went wrong on our side. Your changes were not saved; please try again.' } },
    { status: 500 },
  );
}

/**
 * Blocks cross-site requests to cookie-authenticated mutation endpoints: the
 * browser client always sends X-Ten-Request, which a cross-site form
 * cannot set without a CORS preflight, and any Origin must match this app.
 */
export async function assertSameOrigin(): Promise<void> {
  const h = await headers();
  if (h.get('x-ten-request') !== '1') {
    throw new AppError('FORBIDDEN', 'This request must come from the Ten application.');
  }
  const origin = h.get('origin');
  if (!origin || new URL(origin).origin !== new URL(env().APP_URL).origin) {
    throw new AppError('FORBIDDEN', 'Cross-site requests are not allowed.');
  }
}

type Handler<T> = (ctx: { user: SessionUser }) => Promise<T>;

/** Authenticated JSON mutation endpoint. */
export async function mutation<T>(fn: Handler<T>): Promise<Response> {
  try {
    await assertSameOrigin();
    const user = await requireUser();
    const data = await fn({ user });
    return Response.json({ ok: true, data } satisfies ApiResult<T>);
  } catch (err) {
    return errorResponse(err);
  }
}

/** Authenticated JSON read endpoint. */
export async function query<T>(fn: Handler<T>): Promise<Response> {
  try {
    const user = await requireUser();
    const data = await fn({ user });
    return Response.json({ ok: true, data } satisfies ApiResult<T>, { headers: { 'cache-control': 'private, no-store' } });
  } catch (err) {
    return errorResponse(err);
  }
}

export async function parseJson<S extends ZodType>(req: Request, schema: S): Promise<z.infer<S>> {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    throw invalid('The request body must be valid JSON.');
  }
  const parsed = schema.safeParse(body);
  if (!parsed.success) {
    throw invalid(
      // Our own messages are full sentences; Zod's defaults get the field name for context.
      parsed.error.issues
        .map((i) => (i.path.length && !i.message.endsWith('.') ? `${i.path.join('.')}: ${i.message}` : i.message))
        .join(' '),
      parsed.error.issues,
    );
  }
  return parsed.data;
}

export const uuid = z.string().uuid();
