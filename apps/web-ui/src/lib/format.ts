import type { ActionStatus, RiskLevel } from "./api";

export const riskLabel: Record<RiskLevel, string> = {
  low: "Низкий",
  medium: "Средний",
  high: "Высокий",
};

export const statusLabel: Record<ActionStatus, string> = {
  pending: "Ожидает",
  completed: "Готово",
  failed: "Ошибка",
};

export function shortId(id: string): string {
  return id.slice(0, 8);
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function jsonPreview(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}
