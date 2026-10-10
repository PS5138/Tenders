export function Brand({ compact }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-2.5 text-lg font-semibold tracking-tight">
      <span aria-hidden className="grid size-6 place-items-center rounded-md bg-accent text-[17px] font-bold leading-none text-on-accent">
        T
      </span>
      {compact ? <span className="sr-only">Ten</span> : <span>Ten</span>}
    </div>
  );
}
