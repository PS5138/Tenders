import 'server-only';
import { requireUser } from '../auth';
import { prepareQuestionMutation, notifyQuestionMutation } from './notifications';
import { errorResponse } from '../http';
import { env } from '../env';
import { backendConfig } from './config';
import { backendIdentity } from './context';
import { allowedBackendRequest, browserIdentityHeader, mutationOriginProblem } from './policy';
import { backendFetch, passthroughResponse } from './transport';

const uploadRoute = /^\/(documents|tenders\/[0-9a-f-]{36}\/documents)$/i;

/** Same sentence and rounding as the API's `format_upload_limit`: whole MiB as a whole number, otherwise one decimal, never rounded up. */
export function uploadLimitMessage(limit: number): string {
  const tenths = Math.floor((limit * 10) / 1_048_576);
  const text = tenths % 10 === 0 ? String(tenths / 10) : (tenths / 10).toFixed(1);
  return `This file is too large. Uploads are limited to ${text} MB.`;
}

/**
 * Passes bytes through untouched until the limit is crossed, then fails the stream so the
 * upstream fetch aborts instead of relaying an unbounded body. `onExceeded` runs first so the
 * caller can tell this failure from a backend outage.
 */
export function byteLimitStream(limit: number, onExceeded: () => void): TransformStream<Uint8Array, Uint8Array> {
  let seen = 0;
  return new TransformStream<Uint8Array, Uint8Array>({
    transform(chunk, controller) {
      seen += chunk.byteLength;
      if (seen > limit) {
        onExceeded();
        controller.error(new Error('Upload exceeds the configured limit.'));
        return;
      }
      controller.enqueue(chunk);
    },
  });
}

export async function proxyBackend(request: Request, workspaceId: string, segments: string[]): Promise<Response> {
  let uploadTooLarge = false;
  let uploadLimit = 0;
  try {
    const user = await requireUser();
    const config = env();
    if (browserIdentityHeader(request.headers))
      return Response.json({ detail: 'Identity headers are set by the server.' }, { status: 400 });
    if (!['GET', 'HEAD'].includes(request.method)) {
      const problem = mutationOriginProblem(request.headers, config.APP_URL);
      if (problem) return Response.json({ detail: problem }, { status: 403 });
    }
    const path = `/${segments.join('/')}`;
    const query = new URL(request.url).searchParams;
    if (!allowedBackendRequest(request.method, path, query, config.APP_MODE !== 'demo'))
      return Response.json({ detail: 'Backend route not found.' }, { status: 404 });
    const identity = await backendIdentity(user.id, workspaceId);
    // Inspect only bounded JSON mutations. Upload and NDJSON bodies remain streams.
    const questionMutation = path.match(/^\/questions\/([0-9a-f-]{36})(\/comments)?$/i);
    let mutationBody: Record<string, unknown> | undefined;
    let prepared: Awaited<ReturnType<typeof prepareQuestionMutation>> | undefined;
    let forwardedBody: BodyInit | undefined = ['GET', 'HEAD'].includes(request.method) ? undefined : (request.body ?? undefined);
    if (request.method === 'POST' && uploadRoute.test(path)) {
      // Uploads stay streams, but never unbounded ones: refuse a declared size over the limit
      // before reading, and count the bytes when the size is undeclared (or misdeclared).
      uploadLimit = config.MAX_UPLOAD_BYTES;
      const declared = Number(request.headers.get('content-length'));
      if (Number.isFinite(declared) && declared > uploadLimit) {
        await request.body?.cancel().catch(() => {});
        return Response.json({ detail: uploadLimitMessage(uploadLimit) }, { status: 413 });
      }
      if (request.body)
        forwardedBody = request.body.pipeThrough(
          byteLimitStream(uploadLimit, () => {
            uploadTooLarge = true;
          }),
        );
    }
    if (questionMutation && (request.method === 'PATCH' || (request.method === 'POST' && questionMutation[2]))) {
      const reader = request.body?.getReader();
      const chunks: Uint8Array[] = [];
      let length = 0;
      if (reader)
        try {
          while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            length += value.length;
            if (length > 65536) {
              await reader.cancel();
              return Response.json({ detail: 'Request is too large.' }, { status: 413 });
            }
            chunks.push(value);
          }
        } finally {
          reader.releaseLock();
        }
      const raw = Buffer.concat(chunks).toString('utf8');
      try {
        mutationBody = JSON.parse(raw);
      } catch {
        return Response.json({ detail: 'Invalid JSON.' }, { status: 422 });
      }
      if (!mutationBody || Array.isArray(mutationBody) || typeof mutationBody !== 'object')
        return Response.json({ detail: 'Expected a JSON object.' }, { status: 422 });
      prepared = await prepareQuestionMutation(user.id, workspaceId, identity, questionMutation[1], mutationBody);
      forwardedBody = raw;
    }
    const queryString = query.toString();
    const response = await backendFetch(backendConfig(), identity, path + (queryString ? `?${queryString}` : ''), {
      method: request.method,
      body: forwardedBody,
      contentType: request.headers.get('content-type') ?? undefined,
    });
    if (response.ok && prepared && mutationBody) {
      try {
        await notifyQuestionMutation(user.id, workspaceId, identity, prepared, mutationBody, Boolean(questionMutation?.[2]));
      } catch (error) {
        console.error('[notifications] Backend change saved, notification creation failed', error);
      }
    }
    return passthroughResponse(response);
  } catch (error) {
    if (uploadTooLarge) return Response.json({ detail: uploadLimitMessage(uploadLimit) }, { status: 413 });
    if (error instanceof TypeError) return Response.json({ detail: 'The backend is unavailable. Please try again.' }, { status: 502 });
    return errorResponse(error);
  }
}
