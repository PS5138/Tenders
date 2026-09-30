import { describe, expect, it } from 'vitest';
import { DEV_ACCOUNTS, devSwitchEnabled } from '@/lib/dev-accounts';

describe('development user switch', () => {
  it('is available only in development with the explicit flag', () => {
    expect(devSwitchEnabled('development', 'true')).toBe(true);
    for (const [mode, flag] of [
      ['development', undefined],
      ['development', 'false'],
      ['demo', 'true'],
      ['test', 'true'],
      [undefined, 'true'],
    ] as const)
      expect(devSwitchEnabled(mode, flag)).toBe(false);
  });

  it('lists only fictional .test accounts, with unique names per business', () => {
    expect(DEV_ACCOUNTS.every((a) => a.email.endsWith('.test'))).toBe(true);
    for (const business of ['example', 'riverside'] as const) {
      const names = DEV_ACCOUNTS.filter((a) => a.business === business).map((a) => a.name.toLowerCase());
      expect(new Set(names).size).toBe(names.length);
    }
    expect(DEV_ACCOUNTS.find((a) => a.key === 'puru')?.role).toBe('admin');
  });
});
