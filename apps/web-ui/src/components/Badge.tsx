import type { ActionStatus, RiskLevel } from "../lib/api";

const riskClass: Record<RiskLevel, string> = {
  low: "border-teal/30 bg-teal/10 text-teal",
  medium: "border-amber/30 bg-amber/10 text-amber",
  high: "border-rose/30 bg-rose/10 text-rose",
};

const statusClass: Record<ActionStatus, string> = {
  pending: "border-amber/30 bg-amber/10 text-amber",
  completed: "border-teal/30 bg-teal/10 text-teal",
  failed: "border-rose/30 bg-rose/10 text-rose",
};

export function RiskBadge({ risk }: { risk: RiskLevel }) {
  const labels: Record<RiskLevel, string> = {
    low: "низкий",
    medium: "средний",
    high: "высокий",
  };
  return <span className={`badge ${riskClass[risk]}`}>{labels[risk]}</span>;
}

export function StatusBadge({ status }: { status: ActionStatus }) {
  const labels: Record<ActionStatus, string> = {
    pending: "ожидает",
    completed: "готово",
    failed: "ошибка",
  };
  return <span className={`badge ${statusClass[status]}`}>{labels[status]}</span>;
}

export function EnabledBadge({ enabled }: { enabled: boolean }) {
  return (
    <span className={`badge ${enabled ? "border-teal/30 bg-teal/10 text-teal" : "border-rose/30 bg-rose/10 text-rose"}`}>
      {enabled ? "включён" : "отключён"}
    </span>
  );
}
