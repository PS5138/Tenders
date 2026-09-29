'use client';
import { useId, useRef, useState, type TextareaHTMLAttributes } from 'react';
import { activeMention, mentionedNames, splitMentions } from '@/lib/mentions';
import { Avatar } from '@/components/ui/avatar';
import { field } from './shared';

type Member = { userId: string; displayName: string };

/** A textarea that suggests colleagues after "@" and says who will be notified. */
export function MentionTextarea({
  members,
  value,
  onValue,
  className = '',
  ...rest
}: {
  members: Member[];
  value: string;
  onValue: (value: string) => void;
} & Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'value' | 'onChange'>) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const listId = useId();
  const [mention, setMention] = useState<{ start: number; query: string } | null>(null),
    [highlight, setHighlight] = useState(0);
  const options = mention
    ? members.filter((m) => m.displayName.toLocaleLowerCase().includes(mention.query.toLocaleLowerCase())).slice(0, 6)
    : [];
  const open = options.length > 0;
  const notified = mentionedNames(
    value,
    members.map((m) => m.displayName),
  );
  function track(text: string, caret: number) {
    const next = activeMention(text, caret);
    setMention(next);
    setHighlight(0);
  }
  function choose(member: Member) {
    const input = ref.current;
    if (!input || !mention) return;
    const caret = input.selectionStart ?? value.length;
    const inserted = `@${member.displayName} `;
    const next = value.slice(0, mention.start) + inserted + value.slice(caret);
    onValue(next);
    setMention(null);
    requestAnimationFrame(() => {
      input.focus();
      const position = mention.start + inserted.length;
      input.setSelectionRange(position, position);
    });
  }
  return (
    <div className="relative">
      <textarea
        ref={ref}
        {...rest}
        className={`${field} ${className}`}
        value={value}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-activedescendant={open ? `${listId}-${highlight}` : undefined}
        onChange={(e) => {
          onValue(e.target.value);
          track(e.target.value, e.target.selectionStart ?? e.target.value.length);
        }}
        onClick={(e) => track(e.currentTarget.value, e.currentTarget.selectionStart ?? 0)}
        onBlur={() => setTimeout(() => setMention(null), 150)}
        onKeyDown={(e) => {
          if (!open) return;
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            setHighlight((h) => (h + (e.key === 'ArrowDown' ? 1 : options.length - 1)) % options.length);
          } else if (e.key === 'Enter' || e.key === 'Tab') {
            e.preventDefault();
            choose(options[highlight]);
          } else if (e.key === 'Escape') {
            setMention(null);
          }
        }}
      />
      {open ? (
        <ul
          id={listId}
          role="listbox"
          aria-label="People to mention"
          className="absolute left-0 right-0 top-full z-30 mt-1 max-h-56 overflow-y-auto rounded-md border border-line bg-bg p-1 shadow-lg"
        >
          {options.map((m, i) => (
            <li
              key={m.userId}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === highlight}
              className={`flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-[13px] ${i === highlight ? 'bg-soft' : ''}`}
              onMouseDown={(e) => {
                e.preventDefault();
                choose(m);
              }}
              onMouseEnter={() => setHighlight(i)}
            >
              <Avatar name={m.displayName} />
              {m.displayName}
            </li>
          ))}
        </ul>
      ) : null}
      {notified.length ? <p className="mt-1 text-[11px] text-muted">Will notify {notified.join(', ')}.</p> : null}
    </div>
  );
}

/** Comment text with mentions shown as tags. */
export function MentionText({ text, members }: { text: string; members: Member[] }) {
  return (
    <p className="whitespace-pre-wrap wrap-anywhere">
      {splitMentions(
        text,
        members.map((m) => m.displayName),
      ).map((part, i) =>
        part.mention ? (
          <span key={i} className="rounded bg-blue-bg px-1 font-medium text-blue">
            {part.text}
          </span>
        ) : (
          <span key={i}>{part.text}</span>
        ),
      )}
    </p>
  );
}
