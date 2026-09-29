import { forwardRef, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react';
import { cn } from './cn';

const base = 'min-w-0 rounded-md border border-line bg-bg px-2.5 py-2 text-[13px] text-ink placeholder:text-muted/80 disabled:opacity-60 aria-[invalid=true]:border-red';

// Controls fill their container unless the caller sets a width.
const control = (className?: string) => cn(/(^|\s)([a-z0-9]+:)*w-/.test(className ?? '') ? undefined : 'w-full', base, className);

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...rest }, ref) {
  return <input ref={ref} className={control(className)} {...rest} />;
});

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select({ className, ...rest }, ref) {
  return <select ref={ref} className={control(cn('pr-7', className))} {...rest} />;
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea({ className, ...rest }, ref) {
  return <textarea ref={ref} className={control(cn('leading-relaxed', className))} {...rest} />;
});

export function Field({
  label,
  htmlFor,
  hint,
  error,
  children,
  className,
}: {
  label: ReactNode;
  htmlFor?: string;
  hint?: ReactNode;
  error?: string | null;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('flex min-w-0 flex-col gap-1.5', className)}>
      <label htmlFor={htmlFor} className="text-xs text-muted">
        {label}
      </label>
      {children}
      {hint && !error ? <p className="text-xs text-muted">{hint}</p> : null}
      {error ? (
        <p className="text-xs text-red" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function Checkbox({ label, className, ...rest }: InputHTMLAttributes<HTMLInputElement> & { label: ReactNode }) {
  return (
    <label className={cn('flex items-start gap-2 text-[13px]', className)}>
      <input type="checkbox" className="mt-0.5 size-4 accent-[var(--accent)]" {...rest} />
      <span>{label}</span>
    </label>
  );
}
