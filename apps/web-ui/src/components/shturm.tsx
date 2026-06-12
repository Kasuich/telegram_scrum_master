// Shared presentational pieces + formatters for the «Штурм» evaluator UI.
import type { ReactNode } from "react";

import { agentLogo } from "../lib/agentLogos";

export function pct(v: number | null | undefined): string {
  return v == null ? "—" : `${(v * 100).toFixed(1)}%`;
}
export function sec(v: number | null | undefined): string {
  return v == null ? "—" : `${v.toFixed(1)} с`;
}
export function score10(v: number | null | undefined): string {
  return v == null ? "—" : `${v.toFixed(1)}/10`;
}
export function usd(v: number | null | undefined): string {
  return v == null ? "—" : `$${v.toFixed(2)}`;
}

export const CRITERION_LABELS: Record<string, string> = {
  action_correctness: "Тип действия",
  faithfulness: "Достоверность",
  intent_alignment: "Соответствие запросу",
  forbidden_compliance: "Запрещённые операции",
  completeness: "Полнота",
  final_state_quality: "Состояние трекера",
};

export const MODE_LABELS: Record<string, string> = {
  wrong_action_type: "Неверный тип действия",
  hallucinated_field: "Галлюцинация полей",
  missed_search: "Пропущен поиск",
  over_creation: "Лишние задачи",
  under_creation: "Недосоздание задач",
  forbidden_operation: "Запрещённая операция",
  ignored_existing_task: "Проигнорировал задачу",
  incomplete_steps: "Неполные шаги",
  wrong_priority_mapping: "Неверный приоритет",
  wrong_assignee: "Неверный исполнитель",
  no_action_when_needed: "Бездействие",
  acted_when_not_needed: "Лишнее действие",
  looping_or_stalled: "Зацикливание",
  other: "Прочее",
};

export function modeLabel(mode: string): string {
  return MODE_LABELS[mode] ?? mode;
}

const RUNNING = new Set([
  "queued",
  "generating_scenarios",
  "generating_user_texts",
  "running_agents",
  "judging",
  "cancelling",
]);

export function isRunning(status: string | undefined): boolean {
  return Boolean(status && RUNNING.has(status));
}

export function verdict(
  status: string | undefined,
  passRate: number | null | undefined,
): { label: string; kind: "good" | "warn" | "bad" | "run" } {
  if (isRunning(status)) return { label: "В работе", kind: "run" };
  if (status === "failed") return { label: "Сбой прогона", kind: "bad" };
  if (status === "cancelled") return { label: "Отменён", kind: "warn" };
  const pr = passRate ?? 0;
  if (pr >= 0.85) return { label: "Агент здоров", kind: "good" };
  if (pr >= 0.6) return { label: "Есть пробелы", kind: "warn" };
  return { label: "Требует работы", kind: "bad" };
}

export function ShturmHero({
  title,
  eyebrow = "Штурм · оценка качества",
  subtitle,
  children,
}: {
  title: ReactNode;
  eyebrow?: string;
  subtitle?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="shturm-hero">
      <img src={agentLogo("shturm")} alt="Штурм" />
      <div className="hero-body">
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        {subtitle ? <p className="hero-sub">{subtitle}</p> : null}
      </div>
      {children ? <div className="hero-actions">{children}</div> : null}
    </div>
  );
}

export function Kpi({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "good" | "warn" | "bad";
}) {
  return (
    <div className={`kpi-card${tone ? ` ${tone}` : ""}`}>
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
      {sub != null ? <span className="kpi-sub">{sub}</span> : null}
    </div>
  );
}

export function ConfidencePill({
  value,
  low,
}: {
  value: number | null | undefined;
  low?: boolean | null;
}) {
  if (value == null) return null;
  const cls = low ? "lo" : value >= 0.8 ? "hi" : value >= 0.6 ? "mid" : "lo";
  return (
    <span className={`confidence-pill ${cls}`} title="Согласие судейской панели">
      доверие {(value * 100).toFixed(0)}%
    </span>
  );
}

export function ModeChips({ modes }: { modes: string[] | null | undefined }) {
  if (!modes || !modes.length) return null;
  return (
    <span>
      {modes.map((m) => (
        <span className="mode-chip" key={m}>
          {modeLabel(m)}
        </span>
      ))}
    </span>
  );
}

export function ScoreBar({ value, max = 10 }: { value: number; max?: number }) {
  const w = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className="bar-track">
      <div className="bar-fill score" style={{ width: `${w}%` }} />
    </div>
  );
}

export function CountBar({ value, max }: { value: number; max: number }) {
  const w = max > 0 ? Math.max(4, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div className="bar-track">
      <div className="bar-fill" style={{ width: `${w}%` }} />
    </div>
  );
}
