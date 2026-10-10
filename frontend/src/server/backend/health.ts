import 'server-only';
import { backendConfig } from './config';
import type { BackendIdentity } from './context';
import { backendFetch } from './transport';

export type BackendHealth = {
  status: string;
  llm_provider?: string;
  embedding_provider?: string;
  service_secret_enabled?: boolean;
  synthetic_demo?: boolean;
};

export async function getBackendHealth(): Promise<BackendHealth> {
  const config = backendConfig();
  const response = await fetch(`${config.origin}/health`, {
    headers: config.secret ? { authorization: `Bearer ${config.secret}` } : {},
    cache: 'no-store',
    redirect: 'error',
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error('Backend health check failed.');
  return response.json();
}

export function assertLiveBackend(health: BackendHealth): void {
  if (
    health.status !== 'ok' ||
    health.synthetic_demo === true ||
    health.llm_provider !== 'anthropic' ||
    !['openai', 'voyage'].includes(health.embedding_provider ?? '') ||
    health.service_secret_enabled !== true
  ) {
    throw new Error(
      'Demo mode requires the backend to report live providers and an enforced service secret. Fake or unreported providers are refused.',
    );
  }
}

/** Whether this business is a synthetic demonstration (simulated AI, synthetic uploads only). */
export async function getBusinessSynthetic(identity: BackendIdentity): Promise<boolean> {
  const response = await backendFetch(backendConfig(), identity, '/organisations/current', {
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error('Business mode lookup failed.');
  const body: { synthetic?: boolean } = await response.json();
  return body.synthetic === true;
}

export type AiMode = 'unavailable' | 'synthetic' | 'no-keys' | 'live';

/**
 * What the banner says for one business. A synthetic business always runs on the stand-ins. A live
 * business runs on the real providers once the backend has them; until then (no keys in .env, so the
 * whole backend runs synthetic) it says so rather than pretending to be live.
 */
export function aiMode(health: BackendHealth | null, businessSynthetic: boolean | null): AiMode {
  if (!health || health.status !== 'ok' || businessSynthetic === null) return 'unavailable';
  if (businessSynthetic) return 'synthetic';
  const liveProviders =
    health.llm_provider === 'anthropic' && ['openai', 'voyage'].includes(health.embedding_provider ?? '') && !health.synthetic_demo;
  return liveProviders ? 'live' : 'no-keys';
}
