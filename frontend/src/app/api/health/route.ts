import { db } from '@/server/db';
import { env } from '@/server/env';
import { getBackendHealth, assertLiveBackend } from '@/server/backend/health';

export async function GET() {
  try {
    env();
  } catch {
    // Details go to the server log only.
    return Response.json({ ok: false, error: { code: 'MISCONFIGURED', message: 'Server configuration is incomplete. See the server log.' } }, { status: 503 });
  }
  try {
    await db()`select 1`;
    if (env().BACKEND_URL) {
      const health = await getBackendHealth();
      if (health.status !== 'ok') throw new Error('Backend unavailable');
      if (env().APP_MODE === 'demo') assertLiveBackend(health);
    }
    return Response.json({ ok: true, data: { database: 'ok' } }, { headers: { 'cache-control': 'no-store' } });
  } catch {
    return Response.json({ ok: false, error: { code: 'UNAVAILABLE', message: 'Database unavailable' } }, { status: 503 });
  }
}
