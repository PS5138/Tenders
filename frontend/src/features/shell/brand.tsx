export function Brand({ compact }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-2.5 text-lg font-semibold tracking-tight">
      <span aria-hidden className="grid size-6 place-items-center rounded-md bg-accent text-[15px] text-on-accent">
        t
      </span>
      {compact ? <span className="sr-only">Ten</span> : <span>ten</span>}
    </div>
  );
}
