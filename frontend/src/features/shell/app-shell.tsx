import type { ReactNode } from 'react';
import type { ShellData } from '@/server/services/workspaces';
import { env } from '@/server/env';
import { configuredOrigin } from '@/server/backend/policy';
import { Brand } from './brand';
import { OriginNotice } from './origin-notice';
import { ShellCountsProvider } from './shell-counts';
import { SideNav } from './side-nav';
import { TopBar } from './top-bar';

type AiMode = import('@/server/backend/health').AiMode;

const AI_MODE_TEXT: Record<AiMode, string> = {
  unavailable: 'Backend unavailable. Please try again shortly.',
  synthetic:
    'Synthetic demonstration business: the AI is simulated and only the supplied synthetic documents can be uploaded. Use Live workspace for real tenders.',
  'no-keys':
    'Live business, but no AI keys are configured yet. Add ANTHROPIC_API_KEY and OPENAI_API_KEY to the .env file at the repository root and restart.',
  live: 'Live AI (Anthropic and OpenAI). Review every answer before approval.',
};

function AiModeBanner({ ai }: { ai: AiMode }) {
  return (
    <p role="status" className={`border-b border-line px-4 py-1.5 text-[11px] sm:px-6 ${ai === 'live' ? 'text-muted' : 'bg-amber-bg text-amber'}`}>
      {AI_MODE_TEXT[ai]}
    </p>
  );
}

export function AppShell({ shell, ai, children }: { shell: ShellData; ai: AiMode; children: ReactNode }) {
  // The origin only: APP_URL carries no secret, and the client compares it with the address bar.
  const expectedOrigin = configuredOrigin(env().APP_URL);
  return (
    <ShellCountsProvider
      workspaceId={shell.workspace.id}
      initial={{ openReviewCount: shell.openReviewCount, unreadNotificationCount: shell.unreadNotificationCount }}
    >
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-bg focus:px-3 focus:py-2"
      >
        Skip to content
      </a>
      <div className="min-h-screen md:grid md:grid-cols-[200px_minmax(0,1fr)]">
        <aside className="border-b border-line bg-nav px-3 py-3 md:sticky md:top-0 md:flex md:h-screen md:flex-col md:border-b-0 md:border-r md:px-3 md:py-5">
          <div className="flex items-center justify-between gap-3 md:block">
            <div className="px-2 md:pb-5">
              <Brand />
            </div>
            <div className="hidden px-2 pb-6 text-xs text-muted md:block">
              <p className="font-medium text-ink wrap-anywhere">{shell.workspace.name}</p>
              <p>Business workspace</p>
            </div>
          </div>
          <SideNav workspaceId={shell.workspace.id} />
          <p className="mt-auto hidden px-2 pt-8 text-[11px] text-muted md:block">
            One team.
            <br />
            Every tender in view.
          </p>
        </aside>
        <div className="min-w-0">
          <TopBar shell={shell} />
          <OriginNotice expected={expectedOrigin} />
          <AiModeBanner ai={ai} />
          <main id="main" className="px-4 pb-10 pt-6 sm:px-6">
            {children}
          </main>
        </div>
      </div>
    </ShellCountsProvider>
  );
}
