'use client';
import type { ReactNode } from 'react';
import { RotateCcw } from 'lucide-react';
import { Button } from './button';
import { cn } from './cn';

export function Spinner({ className, label = 'Loading' }: { className?: string; label?: string }) {
  return (
    <span role="status" className={cn('inline-flex items-center gap-2 text-xs text-muted', className)}>
      <span className="size-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" aria-hidden />
      <span>{label}</span>
    </span>
  );
}

export function EmptyState({ title, children, action, className }: { title: ReactNode; children?: ReactNode; action?: ReactNode; className?: string }) {
  return (
    <div className={cn('rounded-lg border border-dashed border-line bg-soft px-5 py-8 text-center', className)}>
      <h3 className="text-sm font-semibold">{title}</h3>
      {children ? <div className="mx-auto mt-1.5 max-w-prose text-xs text-muted">{children}</div> : null}
      {action ? <div className="mt-4 flex flex-wrap justify-center gap-2">{action}</div> : null}
    </div>
  );
}

export function ErrorState({ title = 'This could not be loaded', message, onRetry }: { title?: string; message?: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded-lg border border-red/30 bg-red-bg px-4 py-4 text-xs text-red">
      <p className="font-semibold">{title}</p>
      {message ? <p className="mt-1">{message}</p> : null}
      {onRetry ? (
        <Button size="sm" className="mt-3" onClick={onRetry}>
          <RotateCcw aria-hidden /> Try again
        </Button>
      ) : null}
    </div>
  );
}
