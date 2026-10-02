'use client';
import { useSyncExternalStore } from 'react';

const subscribe = () => () => {};

/**
 * Warns before the first write when the browser's address differs from the origin the server
 * accepts writes from (APP_URL). Reads happen regardless; every save would be refused with 403.
 */
export function OriginNotice({ expected }: { expected: string | null }) {
  const actual = useSyncExternalStore(
    subscribe,
    () => window.location.origin,
    () => null,
  );
  if (!expected || !actual || actual === expected) return null;
  const sameApp = (() => {
    try {
      return new URL(`${window.location.pathname}${window.location.search}`, expected).href;
    } catch {
      return expected;
    }
  })();
  return (
    <p role="alert" className="border-b border-amber/40 bg-amber-bg px-4 py-1.5 text-[11px] text-amber sm:px-6">
      <strong>Address mismatch.</strong> You opened Ten at {actual}, but it is configured for {expected}. Changes will not save from this
      address.{' '}
      <a className="underline" href={sameApp}>
        Open this page at {expected}
      </a>
      , or set <code>APP_URL</code> to {actual} and restart.
    </p>
  );
}
