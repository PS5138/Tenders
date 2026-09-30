import type { ReactNode } from 'react';
import { cn } from './cn';

export type Tone = 'neutral' | 'green' | 'amber' | 'red' | 'blue' | 'violet';

const tones: Record<Tone, string> = {
  neutral: 'bg-soft text-muted',
  green: 'bg-tint text-accent',
  amber: 'bg-amber-bg text-amber',
  red: 'bg-red-bg text-red',
  blue: 'bg-blue-bg text-blue',
  violet: 'bg-violet-bg text-violet',
};

export function Badge({ tone = 'neutral', children, className, title }: { tone?: Tone; children: ReactNode; className?: string; title?: string }) {
  return (
    <span
      title={title}
      className={cn('inline-flex max-w-full items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium leading-4 wrap-anywhere [&_svg]:size-3', tones[tone], className)}
    >
      {children}
    </span>
  );
}
