'use client';
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { usePathname } from 'next/navigation';

type Counts = { openReviewCount: number; unreadNotificationCount: number };
const Ctx = createContext<{ counts: Counts; refresh: () => void }>({
  counts: { openReviewCount: 0, unreadNotificationCount: 0 },
  refresh: () => {},
});

export const MUTATION_EVENT = 'ten:mutated';

/** Signals that data changed so header counts (reviews, notifications) refresh. */
export function announceMutation() {
  window.dispatchEvent(new Event(MUTATION_EVENT));
}

export function ShellCountsProvider({ workspaceId, initial, children }: { workspaceId: string; initial: Counts; children: ReactNode }) {
  const [counts, setCounts] = useState(initial);
  const pathname = usePathname();

  const refresh = useCallback(() => {
    fetch(`/api/w/${workspaceId}/shell`, { credentials: 'same-origin', cache: 'no-store' })
      .then((res) => res.json())
      .then((json) => {
        if (json.ok) setCounts(json.data);
      })
      // Counts are a convenience; keep the last known values if this fails.
      .catch(() => {});
  }, [workspaceId]);

  // Refresh on navigation.
  useEffect(refresh, [pathname, refresh]);

  useEffect(() => {
    const onFocus = () => refresh();
    window.addEventListener('focus', onFocus);
    window.addEventListener(MUTATION_EVENT, onFocus);
    return () => {
      window.removeEventListener('focus', onFocus);
      window.removeEventListener(MUTATION_EVENT, onFocus);
    };
  }, [refresh]);

  return <Ctx.Provider value={{ counts, refresh }}>{children}</Ctx.Provider>;
}

export function useShellCounts() {
  return useContext(Ctx);
}
