import { useMutation } from "@tanstack/react-query";
import { Loader2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api, type ChatResponse } from "../lib/api";
import { Markdown } from "./Markdown";
import { TraceTimeline } from "./TraceTimeline";

// Status lines cycled while the audit runs — mock "thinking" status, mirroring
// how the pm_agent shows progress in Telegram/Playground.
const STATUS_STEPS = [
  "Открываю доску…",
  "Считаю здоровье и гигиену доски…",
  "Ищу просрочки и застрявшие задачи…",
  "Разбираю нагрузку по участникам…",
  "Готовлю рекомендации проджект-менеджера…",
];

/** Reveal `text` progressively to fake token streaming (not a real stream). */
function useStreamedText(text: string | null): string {
  const [shown, setShown] = useState("");
  useEffect(() => {
    if (!text) {
      setShown("");
      return;
    }
    setShown("");
    let i = 0;
    const step = Math.max(2, Math.round(text.length / 240)); // ~240 ticks total
    const timer = setInterval(() => {
      i = Math.min(text.length, i + step);
      setShown(text.slice(0, i));
      if (i >= text.length) clearInterval(timer);
    }, 16);
    return () => clearInterval(timer);
  }, [text]);
  return shown;
}

function RunningStatus() {
  const [idx, setIdx] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setIdx((i) => Math.min(i + 1, STATUS_STEPS.length - 1)), 1100);
    return () => clearInterval(timer);
  }, []);
  return (
    <ol className="audit-status">
      {STATUS_STEPS.slice(0, idx + 1).map((line, i) => (
        <li className={i === idx ? "active" : "done"} key={line}>
          {i === idx ? <Loader2 className="h-3.5 w-3.5 spin" /> : "✓"} {line}
        </li>
      ))}
    </ol>
  );
}

export function AuditReport({ teamId, window = 14, onClose }: { teamId: string; window?: number; onClose: () => void }) {
  const startedRef = useRef(false);
  const audit = useMutation<ChatResponse>({ mutationFn: () => api.teamAudit(teamId, window) });

  useEffect(() => {
    if (!startedRef.current) {
      startedRef.current = true;
      audit.mutate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const reply = audit.data?.reply ?? null;
  const streamed = useStreamedText(reply);
  const steps = audit.data?.steps ?? [];

  return (
    <div className="audit-report surface">
      <div className="section-head">
        <div>
          <h2>Аудит доски</h2>
          <p>{audit.isPending ? "выполняется…" : audit.isError ? "ошибка" : "готово"}</p>
        </div>
        <button className="icon-button" onClick={onClose} title="Закрыть">
          <X className="h-4 w-4" />
        </button>
      </div>

      {audit.isPending ? <RunningStatus /> : null}

      {audit.isError ? (
        <div className="empty">Не удалось выполнить аудит: {(audit.error as Error).message}</div>
      ) : null}

      {reply ? (
        <div className="audit-body">
          <Markdown text={streamed} />
          {streamed.length < (reply?.length ?? 0) ? <span className="caret">▍</span> : null}
        </div>
      ) : null}

      {steps.length ? (
        <details className="audit-trace">
          <summary>Шаги ({steps.length})</summary>
          <TraceTimeline steps={steps} />
        </details>
      ) : null}
    </div>
  );
}
