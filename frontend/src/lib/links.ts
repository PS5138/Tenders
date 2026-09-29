export const links = {
  tenders: (ws: string) => `/w/${ws}/tenders`,
  newTender: (ws: string) => `/w/${ws}/tenders/new`,
  tender: (ws: string, t: string, tab: 'overview' | 'documents' | 'specification' | 'responses' | 'activity' | 'submission' = 'overview') =>
    `/w/${ws}/tenders/${t}/${tab}`,
  response: (ws: string, t: string, r: string) => `/w/${ws}/tenders/${t}/responses/${r}`,
  reviews: (ws: string) => `/w/${ws}/tasks`,
  library: (ws: string) => `/w/${ws}/library`,
  source: (ws: string, d: string) => `/w/${ws}/library/${d}`,
  team: (ws: string) => `/w/${ws}/team`,
  notifications: (ws: string) => `/w/${ws}/notifications`,
  account: () => `/account`,
};

export function notificationHref(ws: string, n: { tenderId: string | null; responseId: string | null }): string {
  if (n.tenderId && n.responseId) return links.response(ws, n.tenderId, n.responseId);
  if (n.tenderId) return links.tender(ws, n.tenderId);
  return links.notifications(ws);
}
