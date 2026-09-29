'use client';
import { useState } from 'react';
import { LogOut } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { api } from '@/lib/api-client';

export function SignOutButton({ variant = 'secondary' }: { variant?: 'secondary' | 'ghost' }) {
  const [busy, setBusy] = useState(false);
  return (
    <Button
      variant={variant}
      busy={busy}
      onClick={async () => {
        setBusy(true);
        try {
          await api('/api/auth/logout');
        } finally {
          // A full page load clears all client state after signing out.
          // eslint-disable-next-line @next/next/no-location-assign-relative-destination
          window.location.assign('/login');
        }
      }}
    >
      <LogOut aria-hidden /> Sign out
    </Button>
  );
}
