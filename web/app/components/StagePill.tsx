import {
  IconBuildingBank,
  IconCheck,
  IconFlagCheck,
  IconGavel,
  IconInbox,
  IconListCheck,
  IconMicroscope,
  IconX,
} from "@tabler/icons-react";
import type { LoanStage } from "@/lib/api";
import { Pill, type PillVariant } from "./Pill";

/**
 * Status badge for a loan's lifecycle stage, attached to the case-file
 * header. Labels mirror the backend stage names — that's what the
 * timeline mockup shows ("Underwriting", "Decision", "Conditions"…),
 * and the pipeline view also uses these so the vocabulary stays
 * consistent across the app.
 *
 * Variant choices follow the mockup's colour logic:
 *
 *   - intake / underwriting / decision → info (work-in-progress blue)
 *   - conditions → warn (open items to chase)
 *   - closing → info (paperwork phase, not yet closed)
 *   - servicing / approved → success
 *   - declined → danger
 */
const STAGE_META: Record<
  LoanStage,
  {
    label: string;
    variant: PillVariant;
    Icon: React.ComponentType<{ size?: number }>;
  }
> = {
  intake: { label: "Intake", variant: "info", Icon: IconInbox },
  underwriting: { label: "Underwriting", variant: "info", Icon: IconMicroscope },
  decision: { label: "Decision", variant: "info", Icon: IconGavel },
  conditions: { label: "Conditions", variant: "warn", Icon: IconListCheck },
  closing: { label: "Closing", variant: "info", Icon: IconFlagCheck },
  servicing: { label: "Servicing", variant: "success", Icon: IconBuildingBank },
  approved: { label: "Approved", variant: "success", Icon: IconCheck },
  declined: { label: "Declined", variant: "danger", Icon: IconX },
};

interface Props {
  stage: LoanStage;
}

export function StagePill({ stage }: Props) {
  const meta = STAGE_META[stage];
  const Icon = meta.Icon;
  return (
    <Pill variant={meta.variant} leading={<Icon size={11} />}>
      {meta.label}
    </Pill>
  );
}
