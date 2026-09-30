import 'server-only';
import { backendConfig } from './config';

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
