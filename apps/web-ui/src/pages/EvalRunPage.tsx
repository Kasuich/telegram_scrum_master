import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../lib/api";
import { formatDate } from "../lib/format";

function pct(v: number | null | undefined): string {
  return v == null ? "—" : `${(v * 100).toFixed(1)}%`;
}

function sec(v: number | null | undefined): string {
  return v == null ? "—" : `${v.toFixed(1)} с`;
}

function score10(v: number | null | undefined): string {
  return v == null ? "—" : `${v.toFixed(1)}/10`;
}

function criteriaTooltip(summary: Record<string, number> | null | undefined): string {
  if (!summary) return "";
  return Object.entries(summary)
    .map(([k, v]) => `${k}: ${v.toFixed(1)}`)
    .join("\n");
}

export function EvalRunPage() {
  const { runId = "" } = useParams();
  const queryClient = useQueryClient();
  const [suiteFilter, setSuiteFilter] = useState("");
  const caseParams = useMemo(() => {
    const p = new URLSearchParams({ limit: "100" });
    if (suiteFilter) p.set("suite", suiteFilter);
    return p;
  }, [suiteFilter]);

  const run = useQuery({
    queryKey: ["eval-run", runId],
    queryFn: () => api.evalRun(runId),
    enabled: Boolean(runId),
    refetchInterval: 3000,
  });

  const cases = useQuery({
    queryKey: ["eval-cases", runId, suiteFilter],
    queryFn: () => api.evalCases(runId, caseParams),
    enabled: Boolean(runId),
    refetchInterval: 3000,
  });

  const cancel = useMutation({
    mutationFn: () => api.cancelEvalRun(runId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["eval-run", runId] }),
  });

  const data = run.data;
  const metrics = data?.metrics_summary;
  const progress = data ? (data.completed_cases / Math.max(data.total_cases, 1)) * 100 : 0;

  return (
    <div className="page-grid">
      <section className="surface">
        <div className="section-head">
          <div>
            <h2>{data?.name ?? "Eval run"}</h2>
            <p>{data?.status}</p>
          </div>
          {data && !["completed", "failed", "cancelled", "completed_with_errors"].includes(data.status) && (
            <button className="button" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
              Cancel
            </button>
          )}
        </div>
        <div className="stats-grid">
          <div className="stat-card">
            <span>Progress</span>
            <strong>
              {data?.completed_cases ?? 0}/{data?.total_cases ?? 0}
            </strong>
            <div className="progress-bar">
              <div style={{ width: `${progress}%` }} />
            </div>
          </div>
          <div className="stat-card">
            <span>Pass rate</span>
            <strong>{pct(data?.pass_rate)}</strong>
          </div>
          <div className="stat-card">
            <span>Avg score</span>
            <strong>{score10(metrics?.avg_weighted_score)}</strong>
          </div>
          <div className="stat-card">
            <span>Avg agent latency</span>
            <strong>{sec(data?.avg_agent_latency_sec)}</strong>
          </div>
          <div className="stat-card">
            <span>P95 agent latency</span>
            <strong>{sec(data?.p95_agent_latency_sec)}</strong>
          </div>
          <div className="stat-card">
            <span>Timeouts</span>
            <strong>{data?.timeout_cases ?? 0}</strong>
          </div>
        </div>

        {metrics?.criteria_avg && Object.keys(metrics.criteria_avg).length > 0 && (
          <table className="data-table compact">
            <thead>
              <tr>
                <th>Критерий (среднее)</th>
                <th>Оценка</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(metrics.criteria_avg).map(([name, avg]) => (
                <tr key={name}>
                  <td>{name}</td>
                  <td>{avg.toFixed(1)}/10</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="surface">
        <div className="section-head">
          <h3>Cases</h3>
          <select value={suiteFilter} onChange={(e) => setSuiteFilter(e.target.value)}>
            <option value="">Все suites</option>
            {[
              "create_task",
              "update_task",
              "multi_task",
              "hierarchy",
              "duplicate_search",
              "no_task",
            ].map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
        <table className="data-table">
          <thead>
            <tr>
              <th>Suite</th>
              <th>Status</th>
              <th>Passed</th>
              <th>Score</th>
              <th>Action</th>
              <th>Время агента</th>
              <th>Ошибка</th>
            </tr>
          </thead>
          <tbody>
            {(cases.data?.items ?? []).map((c) => (
              <tr key={c.id}>
                <td>
                  <Link to={`/eval/${runId}/cases/${c.id}`}>{c.suite}</Link>
                </td>
                <td>{c.status}</td>
                <td>{c.passed == null ? "—" : c.passed ? "✓" : "✗"}</td>
                <td title={criteriaTooltip(c.criteria_summary)}>
                  {c.weighted_score != null ? score10(c.weighted_score) : c.score?.toFixed(2) ?? "—"}
                </td>
                <td>{c.action_correctness != null ? score10(c.action_correctness) : "—"}</td>
                <td>{sec(c.agent_latency_sec)}</td>
                <td className="truncate">{c.main_error ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted">Обновлено: {data ? formatDate(data.created_at) : "—"}</p>
      </section>
    </div>
  );
}
