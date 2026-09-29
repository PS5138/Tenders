import 'server-only';
import type { paths } from './schema';
import { backendConfig } from './config';
import { backendIdentity, type BackendIdentity } from './context';
import { backendFetch } from './transport';

type GetPath = {
  [P in keyof paths]: paths[P] extends { get: object } ? P : never;
}[keyof paths];
type GetResult<P extends GetPath> = paths[P] extends {
  get: { responses: { 200: { content: { 'application/json': infer T } } } };
}
  ? T
  : never;
type PathParameters<P extends GetPath> = paths[P] extends {
  get: { parameters: { path: infer T } };
}
  ? T
  : Record<string, never>;

export class BackendError extends Error {
  constructor(
    readonly status: number,
    readonly body: Record<string, unknown>,
  ) {
    super(typeof body.detail === 'string' ? body.detail : 'The backend could not complete this request.');
  }
}

export function createBackendClient(identity: BackendIdentity) {
  return {
    async get<P extends GetPath>(path: P, parameters: PathParameters<P>): Promise<GetResult<P>> {
      const resolved = path.replace(/\{([^}]+)\}/g, (_, key: string) => {
        const value = (parameters as Record<string, unknown>)[key];
        if (typeof value !== 'string' || !/^[0-9a-f-]{36}$/i.test(value)) throw new Error(`Invalid backend parameter: ${key}`);
        return value;
      });
      const response = await backendFetch(backendConfig(), identity, resolved);
      if (!response.ok) {
        const body = await response.json().catch(() => ({ detail: 'The backend is unavailable.' }));
        throw new BackendError(response.status, body);
      }
      return response.json() as Promise<GetResult<P>>;
    },
  };
}

export async function backendForWorkspace(userId: string, workspaceId: string) {
  return createBackendClient(await backendIdentity(userId, workspaceId));
}
