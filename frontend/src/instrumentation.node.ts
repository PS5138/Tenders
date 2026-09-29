// Node.js server only (see instrumentation.ts). Validates configuration once,
// before the server accepts requests. A buyer-demo deployment without its live
// AI provider or private storage must not start quietly: in production the
// process exits with the reasons in the log.
import { env } from './server/env';
import { assertLiveBackend, getBackendHealth } from './server/backend/health';

export async function checkConfiguration() {
  try {
    const config = env();
    if (config.APP_MODE === 'demo') assertLiveBackend(await getBackendHealth());
    console.log(`[ten] mode=${config.APP_MODE} backend=configured`);
  } catch (err) {
    console.error(`[ten] ${(err as Error).message}`);
    if (process.env.NODE_ENV === 'production') process.exit(1);
  }
}
