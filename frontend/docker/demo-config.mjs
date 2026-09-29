// First start of the Docker demo: creates local-only secrets in the `config`
// volume. They are random per laptop and only work with this demo's containers.
import { createHmac, randomBytes } from 'node:crypto';
import { chmodSync, existsSync, writeFileSync } from 'node:fs';

const target = '/config/demo.env';
// Readable by the auth container, which runs as a different user. The volume
// is private to this laptop's Docker.
if (existsSync(target)) {
  chmodSync(target, 0o644);
  console.log('Demo configuration already exists.');
  process.exit(0);
}
const secret = randomBytes(32).toString('base64url');
const b64 = (value) => Buffer.from(JSON.stringify(value)).toString('base64url');
function sign(role) {
  const header = b64({ alg: 'HS256', typ: 'JWT' });
  const payload = b64({ iss: 'supabase-local', role, iat: 1700000000, exp: 4102444800 });
  return `${header}.${payload}.${createHmac('sha256', secret).update(`${header}.${payload}`).digest('base64url')}`;
}
writeFileSync(
  target,
  [
    `LOCAL_JWT_SECRET=${secret}`,
    `SUPABASE_ANON_KEY=${sign('anon')}`,
    `SUPABASE_SERVICE_ROLE_KEY=${sign('service_role')}`,
    '',
  ].join('\n'),
  { mode: 0o644 },
);
console.log('Created demo configuration.');
