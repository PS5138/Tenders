import { Badge, type Tone } from '@/components/ui/badge';
import { label } from './labels';

const STATUS_TONE: Record<string, Tone> = {
  not_started: 'neutral',
  ai_draft: 'blue',
  writer_edited: 'violet',
  sme_verified: 'amber',
  approved: 'green',
};
const COVERAGE_TONE: Record<string, Tone> = { covered: 'green', partial: 'amber', new: 'red', unknown: 'neutral' };

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={STATUS_TONE[status] ?? 'neutral'}>{label(status)}</Badge>;
}

/** Coverage is spelled out, never shown by colour alone. */
export function CoverageBadge({ coverage }: { coverage: string }) {
  return (
    <Badge tone={COVERAGE_TONE[coverage] ?? 'neutral'} title="How much of this question your library already covers">
      {coverage === 'unknown' ? 'Coverage pending' : `${label(coverage)} coverage`}
    </Badge>
  );
}
