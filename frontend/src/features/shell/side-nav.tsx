'use client';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { BookOpen, Inbox, Layers, Users } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/components/ui/cn';
import { useShellCounts } from './shell-counts';

export function SideNav({ workspaceId }: { workspaceId: string }) {
  const pathname = usePathname();
  const { counts } = useShellCounts();
  const base = `/w/${workspaceId}`;
  const items = [
    { href: `${base}/tenders`, label: 'All tenders', icon: Layers },
    { href: `${base}/tasks`, label: 'My tasks', icon: Inbox, count: counts.openReviewCount },
    { href: `${base}/library`, label: 'Evidence library', icon: BookOpen },
    { href: `${base}/team`, label: 'Team', icon: Users },
  ];
  return (
    <nav aria-label="Workspace" className="mt-3 flex flex-wrap gap-1 md:mt-0 md:flex-col">
      {items.map((item) => {
        const active = pathname === item.href || pathname.startsWith(item.href + '/');
        const Icon = item.icon;
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? 'page' : undefined}
            className={cn(
              'flex items-center gap-2 rounded-md px-2.5 py-2 text-[13px] md:w-full',
              active ? 'bg-bg font-semibold text-accent shadow-sm' : 'text-ink hover:bg-bg/60',
            )}
          >
            <Icon className="size-4" aria-hidden />
            <span>{item.label}</span>
            {item.count ? (
              <Badge tone="blue" className="ml-auto">
                <span className="sr-only">,</span>
                {item.count}
                <span className="sr-only"> waiting on you</span>
              </Badge>
            ) : null}
          </Link>
        );
      })}
    </nav>
  );
}
