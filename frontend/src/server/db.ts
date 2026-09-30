import 'server-only';
import postgres from 'postgres';
import { env } from './env';

export type Sql = postgres.Sql;
export type Tx = postgres.TransactionSql;

declare global {
  var __tenSql: Sql | undefined;
}

/** Shared connection pool. Column names come back camelCased; JSON values are left as stored. */
export function db(): Sql {
  if (!globalThis.__tenSql) {
    const config = env();
    globalThis.__tenSql = postgres(config.DATABASE_URL, {
      max: config.DATABASE_POOL_MAX,
      prepare: config.DATABASE_PREPARE,
      onnotice: () => {},
      transform: {
        column: { from: postgres.toCamel, to: postgres.fromCamel },
        undefined: null,
      },
    });
  }
  return globalThis.__tenSql;
}

/**
 * Runs `fn` in a transaction as the given user. The transaction switches to the
 * `authenticated` role with that user's JWT claims, so row-level security
 * applies exactly as it would for a Supabase client request.
 */
export async function withUser<T>(userId: string, fn: (tx: Tx) => Promise<T>): Promise<T> {
  return db().begin(async (tx) => {
    const claims = JSON.stringify({ sub: userId, role: 'authenticated' });
    await tx`select set_config('request.jwt.claims', ${claims}, true)`;
    await tx`set local role authenticated`;
    return fn(tx);
  }) as Promise<T>;
}

/**
 * Runs `fn` in a privileged transaction that bypasses row-level security.
 * Only for operator provisioning, invitations and seeds; callers must scope every query
 * to an explicit workspace themselves.
 */
export async function withService<T>(fn: (tx: Tx) => Promise<T>): Promise<T> {
  return db().begin(fn) as Promise<T>;
}

export async function closeDb(): Promise<void> {
  await globalThis.__tenSql?.end({ timeout: 5 });
  globalThis.__tenSql = undefined;
}
