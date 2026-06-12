import { useQuery } from "@tanstack/react-query";
import { FlaskConical, Plus } from "lucide-react";
import { Link } from "react-router-dom";

import { api } from "../lib/api";
import { formatDate } from "../lib/format";

function pct(value: number | null | undefined): string {
  if (value == null) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function sec(value: number | null | undefined): string {
  if (value == null) return "—";
  return `${value.toFixed(1)} с`;
}

export function EvalListPage() {
  const runs = useQuery({ queryKey: ["eval-runs"], queryFn: () => api.evalRuns() });

  return (
    <div className="page-grid">
      <section className="surface">
        <div className="section-head">
          <div>
            <h2>Eval runs</h2>
            <p>Оценка качества PMAgent</p>
          </div>
          <div className="row gap-2">
            <FlaskConical className="h-5 w-5 text-muted" />
            <Link className="button primary" to="/eval/new">
              <Plus className="h-4 w-4" /> Новый run
            </Link>
          </div>
        </div>
        <table className="data-table">
          <thead>
            <tr>
              <th>Название</th>
              <th>Статус</th>
              <th>Progress</th>
              <th>Pass rate</th>
              <th>Время агента (avg)</th>
              <th>Создан</th>
            </tr>
          </thead>
          <tbody>
            {(runs.data?.items ?? []).map((run) => (
              <tr key={run.id}>
                <td>
                  <Link to={`/eval/${run.id}`}>{run.name}</Link>
                </td>
                <td>{run.status}</td>
                <td>
                  {run.completed_cases}/{run.total_cases}
                </td>
                <td>{pct(run.pass_rate)}</td>
                <td>{sec(run.avg_agent_latency_sec)}</td>
                <td>{formatDate(run.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
