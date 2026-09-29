import type { ReactNode } from 'react';
import { AlertCircle, CheckCircle2, Info, OctagonAlert } from 'lucide-react';
import { cn } from './cn';

type Tone = 'neutral' | 'amber' | 'red' | 'green' | 'blue';

const styles: Record<Tone, string> = {
  neutral: 'bg-soft text-muted',
  amber: 'bg-amber-bg text-amber',
  red: 'bg-red-bg text-red',
  green: 'bg-tint text-accent',
  blue: 'bg-blue-bg text-blue',
};

const icons = { neutral: Info, amber: AlertCircle, red: OctagonAlert, green: CheckCircle2, blue: Info };

export function Notice({ tone = 'neutral', title, children, action, className }: { tone?: Tone; title?: ReactNode; children?: ReactNode; action?: ReactNode; className?: string }) {
  const Icon = icons[tone];
  return (
    <div className={cn('flex items-start gap-2.5 rounded-lg px-3.5 py-3 text-xs leading-relaxed', styles[tone], className)} role={tone === 'red' ? 'alert' : undefined}>
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div className="min-w-0 flex-1 wrap-anywhere">
        {title ? <p className="font-semibold">{title}</p> : null}
        {children}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}
