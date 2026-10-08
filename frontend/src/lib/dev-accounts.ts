/**
 * Fictional development accounts. They have a published password, so they exist only in
 * development stacks and the user switch that signs into them is disabled everywhere else.
 */
export const DEV_PASSWORD = 'ten-dev-only';

export type DevAccount = { key: string; email: string; name: string; title: string; business: 'example' | 'riverside'; role: 'admin' | 'member' };

export const DEV_ACCOUNTS: DevAccount[] = [
  { key: 'valerie', email: 'valerie@example-health.test', name: 'Valerie', title: 'Commercial lead and tender owner', business: 'example', role: 'admin' },
  { key: 'puru', email: 'puru@example-health.test', name: 'Puru', title: 'Technical lead', business: 'example', role: 'admin' },
  { key: 'roger', email: 'roger@example-health.test', name: 'Roger', title: 'Product and implementation', business: 'example', role: 'admin' },
  { key: 'sam', email: 'sam@example-health.test', name: 'Sam', title: 'Bid writer', business: 'example', role: 'member' },
  { key: 'alex', email: 'alex@example-health.test', name: 'Alex', title: 'Clinical safety officer (SME)', business: 'example', role: 'member' },
  { key: 'jordan', email: 'jordan@example-health.test', name: 'Jordan', title: 'Information governance lead (SME)', business: 'example', role: 'member' },
  { key: 'olive', email: 'olive@riverside-medical.test', name: 'Olive', title: 'Bid manager', business: 'riverside', role: 'admin' },
  { key: 'riley', email: 'riley@riverside-medical.test', name: 'Riley', title: 'Bid writer', business: 'riverside', role: 'member' },
];

/**
 * The switch is on by default while the team tests, including `npm run dev`. It stays off in
 * demo mode (a hosted buyer demonstration), and DEMO_USER_SWITCH=false turns it off anywhere.
 */
export function devSwitchEnabled(mode: string | undefined, flag: string | undefined): boolean {
  return mode !== 'demo' && flag !== 'false';
}
