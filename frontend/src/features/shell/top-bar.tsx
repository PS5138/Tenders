'use client';
import Link from 'next/link';
import { useState } from 'react';
import { Dialog } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Upload } from '@/features/backend/shared';
import { useRouter } from 'next/navigation';
import * as Dropdown from '@radix-ui/react-dropdown-menu';
import { ChevronDown, Moon, Settings, Sun, SunMoon, LogOut, UserRoundCog } from 'lucide-react';
import { DEV_ACCOUNTS } from '@/lib/dev-accounts';
import type { ShellData } from '@/server/services/workspaces';
import { Avatar } from '@/components/ui/avatar';
import { api } from '@/lib/api-client';
import { NotificationsBell } from './notifications-bell';

function setTheme(theme: 'system' | 'light' | 'dark') {
  if (theme === 'system') {
    document.cookie = 'theme=; path=/; max-age=0; samesite=lax';
    delete document.documentElement.dataset.theme;
  } else {
    document.cookie = `theme=${theme}; path=/; max-age=31536000; samesite=lax`;
    document.documentElement.dataset.theme = theme;
  }
}

const menuItem =
  'flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-[13px] outline-none data-[highlighted]:bg-soft [&_svg]:size-4';

export function TopBar({ shell }: { shell: ShellData }) {
  const router = useRouter();
  const [uploadOpen, setUploadOpen] = useState(false);
  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-2.5 text-xs text-muted sm:px-6">
      <div className="flex min-w-0 items-center gap-2">
        {shell.workspaces.length > 1 ? (
          <label className="flex items-center gap-2">
            <span className="sr-only">Switch workspace</span>
            <select
              className="max-w-[240px] rounded-md border border-line bg-bg px-2 py-1 text-xs text-ink"
              value={shell.workspace.id}
              onChange={(e) => router.push(`/w/${e.target.value}/tenders`)}
            >
              {shell.workspaces.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <span className="truncate md:hidden">{shell.workspace.name}</span>
        )}
      </div>
      <div className="flex items-center gap-2">
        <Button onClick={() => setUploadOpen(true)}>Upload to library</Button>
        <Dialog
          open={uploadOpen}
          onOpenChange={setUploadOpen}
          title="Upload to the library"
          description="Add a reusable source document. Upload buyer documents within their tender."
        >
          <Upload workspaceId={shell.workspace.id} onUploaded={() => router.refresh()} />
        </Dialog>
        <NotificationsBell workspaceId={shell.workspace.id} />
        <Dropdown.Root>
          <Dropdown.Trigger className="flex items-center gap-2 rounded-md px-1.5 py-1 text-ink hover:bg-soft" aria-label="Account menu">
            <Avatar name={shell.me.displayName} />
            <span className="hidden text-xs sm:inline">{shell.me.displayName}</span>
            <ChevronDown className="size-3.5 text-muted" aria-hidden />
          </Dropdown.Trigger>
          <Dropdown.Portal>
            <Dropdown.Content
              align="end"
              sideOffset={6}
              className="z-50 min-w-56 rounded-lg border border-line bg-bg p-1 text-ink shadow-lg"
            >
              <div className="px-2 py-2 text-xs">
                <p className="font-semibold wrap-anywhere">{shell.me.displayName}</p>
                <p className="text-muted wrap-anywhere">{shell.me.email}</p>
                <p className="mt-1 text-muted">{shell.me.role === 'admin' ? 'Workspace admin' : 'Workspace member'}</p>
              </div>
              <Dropdown.Separator className="my-1 h-px bg-line" />
              <Dropdown.Item asChild className={menuItem}>
                <Link href={`/account?ws=${shell.workspace.id}`}>
                  <Settings aria-hidden /> Account settings
                </Link>
              </Dropdown.Item>
              <Dropdown.Label className="px-2 pb-1 pt-2 text-[11px] uppercase tracking-wide text-muted">Appearance</Dropdown.Label>
              <Dropdown.Item className={menuItem} onSelect={() => setTheme('system')}>
                <SunMoon aria-hidden /> Match system
              </Dropdown.Item>
              <Dropdown.Item className={menuItem} onSelect={() => setTheme('light')}>
                <Sun aria-hidden /> Light
              </Dropdown.Item>
              <Dropdown.Item className={menuItem} onSelect={() => setTheme('dark')}>
                <Moon aria-hidden /> Dark
              </Dropdown.Item>
              {shell.devSwitch ? (
                <>
                  <Dropdown.Separator className="my-1 h-px bg-line" />
                  <Dropdown.Label className="flex items-center gap-1.5 px-2 pb-1 pt-1.5 text-[11px] text-muted">
                    <UserRoundCog className="size-3.5" aria-hidden /> Switch test user (development only)
                  </Dropdown.Label>
                  <div className="max-h-72 overflow-y-auto">
                    {DEV_ACCOUNTS.filter((a) => a.email !== shell.me.email).map((a) => (
                      <Dropdown.Item
                        key={a.email}
                        className={menuItem}
                        aria-label={`Switch to ${a.name}`}
                        onSelect={async () => {
                          try {
                            await api('/api/auth/switch', { body: { email: a.email } });
                          } finally {
                            // A full page load picks up the new session everywhere.
                            // eslint-disable-next-line @next/next/no-location-assign-relative-destination
                            window.location.assign('/');
                          }
                        }}
                      >
                        <Avatar name={a.name} />
                        <span className="min-w-0">
                          <span className="block">{a.name}</span>
                          <span className="block text-[11px] text-muted">
                            {a.role === 'admin' ? 'Admin' : 'Member'} · {a.business === 'example' ? 'Example Health' : 'Riverside Medical'} · {a.title}
                          </span>
                        </span>
                      </Dropdown.Item>
                    ))}
                  </div>
                </>
              ) : null}
              <Dropdown.Separator className="my-1 h-px bg-line" />
              <Dropdown.Item
                className={menuItem}
                onSelect={async () => {
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
              </Dropdown.Item>
            </Dropdown.Content>
          </Dropdown.Portal>
        </Dropdown.Root>
      </div>
    </header>
  );
}
