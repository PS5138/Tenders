// Local stand-in for the Supabase API gateway. Hosted Supabase serves Auth at
// <project-url>/auth/v1; locally the Auth binary serves it at the root, so this
// forwards /auth/v1/* to it. Nothing else is exposed.
import http from 'node:http';

const port = Number(process.env.LOCAL_GATEWAY_PORT ?? 54321);
const listenHost = process.env.LOCAL_GATEWAY_HOST ?? '127.0.0.1';
const authHost = process.env.LOCAL_AUTH_HOST ?? '127.0.0.1';
const authPort = Number(process.env.LOCAL_AUTH_PORT ?? 9999);

http
  .createServer((req, res) => {
    if (!req.url?.startsWith('/auth/v1')) {
      res.writeHead(404, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ message: 'Only /auth/v1 is served locally' }));
      return;
    }
    const upstream = http.request(
      {
        host: authHost,
        port: authPort,
        method: req.method,
        path: req.url.slice('/auth/v1'.length) || '/',
        headers: { ...req.headers, host: `${authHost}:${authPort}` },
      },
      (up) => {
        res.writeHead(up.statusCode ?? 502, up.headers);
        up.pipe(res);
      },
    );
    upstream.on('error', (err) => {
      res.writeHead(502, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ message: `Auth service unavailable: ${err.message}` }));
    });
    req.pipe(upstream);
  })
  .listen(port, listenHost, () => {
    console.log(`Local Supabase gateway on http://${listenHost}:${port} -> auth ${authHost}:${authPort}`);
  });
