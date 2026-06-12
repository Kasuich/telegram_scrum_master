import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { Link } from "react-router-dom";

import { ShturmHero, pct, sec, verdict } from "../components/shturm";
import { api } from "../lib/api";
import { formatDate } from "../lib/format";

export function EvalListPage() {
  const runs = useQuery({
    queryKey: ["eval-runs"],
    queryFn: () => api.evalRuns(),
    refetchInterval: 5000,
  });

  return (
    <div className="shturm-page">
      <ShturmHero
        title="Прогоны оценки"
        subtitle="LLM-as-a-judge для PM-агента: достоверные метрики и разбор, где агент тупит"
      >
        <Link className="primary-button" to="/eval/new">
          <Plus className="h-4 w-4" /> Новый прогон
        </Link>
      </ShturmHero>

      <section className="surface">
        <table className="data-table">
          <thead>
            <tr>
              <th>Название</th>
              <th>Вердикт</th>
              <th>Прогресс</th>
              <th>Pass rate</th>
              <th>Время агента</th>
              <th>Создан</th>
            </tr>
          </thead>
          <tbody>
            {(runs.data?.items ?? []).map((run) => {
              const v = verdict(run.status, run.pass_rate);
              return (
                <tr key={run.id}>
                  <td>
                    <Link to={`/eval/${run.id}`}>{run.name}</Link>
                  </td>
                  <td>
                    <span className={`verdict ${v.kind}`}>{v.label}</span>
                  </td>
                  <td>
                    {run.completed_cases}/{run.total_cases}
                  </td>
                  <td>{pct(run.pass_rate)}</td>
                  <td>{sec(run.avg_agent_latency_sec)}</td>
                  <td>{formatDate(run.created_at)}</td>
                </tr>
              );
            })}
            {!runs.data?.items.length && (
              <tr>
                <td colSpan={6} className="muted">
                  Пока нет прогонов. Запусти первый — «Новый прогон».
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
    </div>
  );
}
