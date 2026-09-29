import type { ReactNode } from 'react';

export function PageHeader({ eyebrow, title, subtitle, actions }: { eyebrow?: ReactNode; title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        {eyebrow ? <p className="mb-2 text-[11px] uppercase tracking-[0.08em] text-muted">{eyebrow}</p> : null}
        <h1 className="text-[26px] font-semibold leading-tight tracking-tight wrap-anywhere">{title}</h1>
        {subtitle ? <div className="mt-1.5 text-sm text-muted">{subtitle}</div> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function SectionTitle({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
      <h2 className="text-lg font-semibold tracking-tight">{children}</h2>
      {aside ? <div className="text-xs text-muted">{aside}</div> : null}
    </div>
  );
}

export function Panel({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-lg border border-line bg-bg p-4 ${className}`}>{children}</section>;
}

export function Meter({ value, max, label }: { value: number; max: number; label?: string }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  return (
    <div className="mt-1.5 h-1 max-w-[130px] overflow-hidden rounded bg-line" role="meter" aria-valuemin={0} aria-valuemax={max} aria-valuenow={value} aria-label={label}>
      <span className="block h-full bg-accent" style={{ width: `${pct}%` }} />
    </div>
  );
}
