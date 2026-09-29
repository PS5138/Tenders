// Applies supabase/migrations/*.sql in order to DATABASE_URL.
// Records versions in supabase_migrations.schema_migrations, the table the
// Supabase CLI uses, so `supabase db push` sees the same history.
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import postgres from 'postgres';

const databaseUrl = process.env.DATABASE_URL;
if (!databaseUrl) {
  console.error('DATABASE_URL is not set');
  process.exit(1);
}

const dir = path.resolve(import.meta.dirname, '../supabase/migrations');
const sql = postgres(databaseUrl, { max: 1, onnotice: () => {} });

async function main() {
  await sql`create schema if not exists supabase_migrations`;
  await sql`
    create table if not exists supabase_migrations.schema_migrations (
      version text primary key,
      statements text[],
      name text
    )`;
  const applied = new Set(
    (await sql<{ version: string }[]>`select version from supabase_migrations.schema_migrations`).map((r) => r.version),
  );
  const files = readdirSync(dir).filter((f) => /^\d+_.+\.sql$/.test(f)).sort();
  let count = 0;
  for (const file of files) {
    const [version, ...rest] = file.replace(/\.sql$/, '').split('_');
    if (applied.has(version)) continue;
    const body = readFileSync(path.join(dir, file), 'utf8');
    await sql.begin(async (tx) => {
      await tx.unsafe(body);
      await tx`insert into supabase_migrations.schema_migrations (version, statements, name)
               values (${version}, ${[body]}, ${rest.join('_')})`;
    });
    console.log(`applied ${file}`);
    count += 1;
  }
  console.log(count ? `${count} migration(s) applied` : 'database is up to date');
}

main()
  .catch((err) => {
    console.error(err instanceof Error ? err.message : err);
    process.exitCode = 1;
  })
  .finally(() => sql.end());
