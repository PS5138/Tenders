/** Explicit wire contract. Never turn a browser-supplied URL into an upstream URL. */
const id = '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}';
type Rule = {
  methods: readonly string[];
  path: RegExp;
  query?: readonly string[];
  developmentOnly?: boolean;
};
const rule = (methods: string[], path: string, query: string[] = []): Rule => ({
  methods,
  path: new RegExp(`^${path}$`),
  query,
});
const rules: Rule[] = [
  rule(['GET', 'POST'], '/tenders'),
  rule(['GET', 'PATCH'], `/tenders/${id}`),
  rule(['GET'], `/tenders/${id}/questions`),
  rule(['POST'], `/tenders/${id}/(documents|retriage|draft-all|submit)`),
  rule(['GET'], `/tenders/${id}/export`, ['format', 'mode']),
  rule(['GET', 'POST'], '/documents'),
  rule(['GET', 'PATCH'], `/documents/${id}`),
  rule(['GET'], `/documents/${id}/(sections|unpaired|file)`),
  rule(['POST'], `/documents/${id}/(confirm|pairs)`),
  rule(['GET'], `/sections/${id}`, ['start', 'end']),
  rule(['GET'], '/library/(items|facts|supersession-decisions)'),
  rule(['GET'], `/library/items/${id}`),
  rule(['POST'], `/library/supersession-decisions/${id}`),
  rule(['GET'], `/jobs/${id}`),
  rule(['GET', 'PATCH'], `/questions/${id}`),
  rule(['GET', 'POST'], `/questions/${id}/answers`),
  rule(['GET'], `/questions/${id}/events`),
  rule(['POST'], `/questions/${id}/(draft|verbatim|evidence|comments|gaps/acknowledge)`),
  rule(['DELETE'], `/questions/${id}/evidence/${id}`),
  rule(['POST'], `/answers/${id}/segments/(0|[1-9][0-9]*)/(attest|dispute)`),
  rule(['POST'], '/threads'),
  rule(['GET'], `/threads/${id}`),
  rule(['POST'], `/threads/${id}/messages`),
  rule(['POST'], `/messages/${id}/save-as-answer`),
  { ...rule(['GET'], '/fixtures/answer'), developmentOnly: true },
  {
    ...rule(['POST'], '/fixtures/draft', ['fail_after', 'delay_ms']),
    developmentOnly: true,
  },
];

export function allowedBackendRequest(method: string, path: string, query: URLSearchParams, development: boolean): boolean {
  // Refuse encoded separators, dot segments, hostnames and ambiguous duplicate parameters.
  const match = rules.find((r) => r.methods.includes(method) && r.path.test(path) && (!r.developmentOnly || development));
  return Boolean(match && [...query.keys()].every((key) => match.query?.includes(key) && query.getAll(key).length === 1));
}

export function browserIdentityHeader(headers: Headers): string | undefined {
  return ['x-actor', 'x-org-id', 'authorization', 'proxy-authorization'].find((name) => headers.has(name));
}

export function isSameOriginMutation(request: Request, appUrl: string): boolean {
  if (['GET', 'HEAD'].includes(request.method)) return true;
  if (request.headers.get('x-ten-request') !== '1') return false;
  try {
    return new URL(request.headers.get('origin') ?? '').origin === new URL(appUrl).origin;
  } catch {
    return false;
  }
}

export function validActor(name: string): boolean {
  return (
    name.trim() === name && name.length > 0 && name.length <= 200 && name.toLowerCase() !== 'system' && !/[\u0000-\u001f\u007f]/.test(name)
  );
}
