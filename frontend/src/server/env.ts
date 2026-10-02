import 'server-only';
import { z } from 'zod';

const bool = z
  .enum(['true', 'false', '1', '0', ''])
  .optional()
  .transform((v) => v === 'true' || v === '1');

const schema = z
  .object({
    APP_MODE: z.enum(['development', 'test', 'demo']).default('development'),
    APP_URL: z.string().url().default('http://localhost:3000'),
    SUPABASE_URL: z.string().url(),
    SUPABASE_ANON_KEY: z.string().min(20),
    SUPABASE_SERVICE_ROLE_KEY: z.string().min(20),
    DATABASE_URL: z.string().min(1),
    DATABASE_POOL_MAX: z.coerce.number().int().positive().default(10),
    BACKEND_URL: z.string().url(),
    BACKEND_SERVICE_SECRET: z.string().optional(),
    // Largest upload the proxy forwards to the backend (bytes); 25 MiB by default.
    MAX_UPLOAD_BYTES: z.coerce
      .number()
      .int()
      .positive()
      .default(25 * 1024 * 1024),
    // Hosted Supabase's transaction pooler (port 6543) does not support prepared statements.
    DATABASE_PREPARE: bool,
  })
  .superRefine((env, ctx) => {
    if (env.APP_MODE === 'demo' && (!env.BACKEND_SERVICE_SECRET || env.BACKEND_SERVICE_SECRET.length < 32))
      ctx.addIssue({
        code: 'custom',
        path: ['BACKEND_SERVICE_SECRET'],
        message: 'Demo mode requires a backend service secret of at least 32 characters.',
      });
  });

export type Env = z.infer<typeof schema>;

let cached: Env | undefined;

/** Validated server configuration. Throws a readable error listing every problem. */
export function env(): Env {
  if (cached) return cached;
  const parsed = schema.safeParse(process.env);
  if (!parsed.success) {
    const lines = parsed.error.issues.map((i) => `  - ${i.path.join('.') || 'env'}: ${i.message}`);
    throw new Error(`Ten configuration is invalid:\n${lines.join('\n')}`);
  }
  cached = parsed.data;
  return cached;
}
