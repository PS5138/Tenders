import { cn } from './cn';

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase();
}

export function Avatar({ name, className }: { name: string; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn('inline-grid size-7 shrink-0 place-items-center rounded-full bg-tint text-[11px] font-semibold text-accent', className)}
    >
      {initials(name)}
    </span>
  );
}
