import { AlertTriangle, CheckCircle2, CircleDot, Clock, MessageSquare, Wrench } from "lucide-react";
import type { ComponentType } from "react";

import type { TraceStep } from "../lib/api";
import { formatDate, jsonPreview } from "../lib/format";

const icons: Record<string, ComponentType<{ className?: string }>> = {
  tool_call: Wrench,
  tool_result: CheckCircle2,
  tool_error: AlertTriangle,
  confirm_wait: Clock,
  confirm_rejected: AlertTriangle,
  final: MessageSquare,
};

function stepTitle(step: TraceStep): string {
  if (step.tool_name) return `${step.kind}: ${step.tool_name}`;
  if (step.reason) return `${step.kind}: ${step.reason}`;
  return step.kind;
}

export function TraceTimeline({ steps }: { steps: TraceStep[] }) {
  if (steps.length === 0) {
    return <div className="empty">Шагов трейса пока нет</div>;
  }

  return (
    <ol className="timeline">
      {steps.map((step, index) => {
        const Icon = icons[step.kind] ?? CircleDot;
        const body = step.content ?? step.error ?? jsonPreview(step.result ?? step.tool_args);
        return (
          <li className="timeline-row" key={`${step.kind}-${step.ts ?? index}`}>
            <span className="timeline-icon">
              <Icon className="h-4 w-4" />
            </span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-ink">{stepTitle(step)}</span>
                {step.ts ? <span className="text-xs text-muted">{formatDate(step.ts)}</span> : null}
                {step.confirm_id ? <span className="mono-chip">{step.confirm_id.slice(0, 8)}</span> : null}
              </div>
              {body ? <pre className="json-block mt-2">{body}</pre> : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
