import 'server-only';
import { requireUser } from '../auth';
import { prepareQuestionMutation, notifyQuestionMutation } from './notifications';
import { errorResponse } from '../http';
import { env } from '../env';
import { backendConfig } from './config';
import { backendIdentity } from './context';
import { allowedBackendRequest, browserIdentityHeader, isSameOriginMutation } from './policy';
import { backendFetch, passthroughResponse } from './transport';

export async function proxyBackend(request: Request, workspaceId: string, segments: string[]): Promise<Response> {
  try {
    const user = await requireUser();
    const config = env();
    if (browserIdentityHeader(request.headers))
      return Response.json({ detail: 'Identity headers are set by the server.' }, { status: 400 });
    if (!isSameOriginMutation(request, config.APP_URL))
      return Response.json({ detail: 'This request must come from the Ten application.' }, { status: 403 });
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
    if (error instanceof TypeError) return Response.json({ detail: 'The backend is unavailable. Please try again.' }, { status: 502 });
    return errorResponse(error);
  }
}
