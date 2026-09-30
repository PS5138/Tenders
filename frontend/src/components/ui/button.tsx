import { forwardRef, type ButtonHTMLAttributes } from 'react';
import { cn } from './cn';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'link' | 'danger';
export type ButtonSize = 'sm' | 'md';

export function buttonClasses(variant: ButtonVariant = 'secondary', size: ButtonSize = 'md', extra?: string): string {
  return cn(
    'inline-flex items-center justify-center gap-2 rounded-md border text-left font-medium transition-colors',
    'disabled:cursor-not-allowed disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0',
    size === 'sm' ? 'px-2.5 py-1.5 text-xs' : 'px-3 py-2 text-[13px]',
    variant === 'primary' && 'border-accent bg-accent text-on-accent hover:brightness-95',
    variant === 'secondary' && 'border-line bg-bg text-ink hover:bg-soft',
    variant === 'ghost' && 'border-transparent bg-transparent text-ink hover:bg-soft',
    variant === 'link' && 'border-transparent bg-transparent px-0 py-0 text-accent hover:underline',
    variant === 'danger' && 'border-red/40 bg-red-bg text-red hover:brightness-95',
    extra,
  );
}

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  busy?: boolean;
};

export const Button = forwardRef<HTMLButtonElement, Props>(function Button(
  { variant = 'secondary', size = 'md', busy, className, disabled, children, type = 'button', ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={buttonClasses(variant, size, className)}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      {...rest}
    >
      {busy ? <span className="size-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" aria-hidden /> : null}
      {children}
    </button>
  );
});
