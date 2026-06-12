import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import {
  ConfidencePill,
  CRITERION_LABELS,
  ModeChips,
  ScoreBar,
  sec,
  score10,
} from "../components/shturm";
import { api, type JudgeCriterionScore, type TraceStep } from "../lib/api";

function JsonBlock({ title, data }: { title: string; data: unknown }) {
  if (data == null) return null;
  return (
    <details className="audit-block">
      <summary>{title}</summary>
      <pre>{JSON.stringify(data, null, 2)}</pre>
    </details>
  );
}

function CriteriaTable({
  criteria,
  stddev,
}: {
  criteria: Record<string, JudgeCriterionScore>;
  stddev?: Record<string, number>;
}) {
  const entries = Object.entries(criteria);
  if (!entries.length) return <p className="muted">Нет оценок</p>;
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Критерий</th>
          <th style={{ width: 160 }}>Оценка</th>
          <th>Вес</th>
          <th>Вклад</th>
          <th>± разброс</th>
          <th>Комментарий</th>
        </tr>
      </thead>
      <tbody>
        {entries.map(([name, c]) => (
          <tr key={name}>
            <td>{CRITERION_LABELS[name] ?? name}</td>
            <td>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <ScoreBar value={c.score} />
                <span className="bar-num">{c.score.toFixed(1)}</span>
              </div>
            </td>
            <td>{(c.weight * 100).toFixed(0)}%</td>
            <td>{(c.score * c.weight).toFixed(2)}</td>
            <td>{stddev?.[name] != null ? `±${stddev[name].toFixed(1)}` : "—"}</td>
            <td className="truncate" title={c.reason}>
              {c.reason || "—"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function stepDetail(step: TraceStep): string {
  if (step.kind === "stage") return String((step as unknown as Record<string, unknown>).stage ?? "");
  if (step.kind === "tool_call") return JSON.stringify(step.tool_args ?? {});
  if (step.kind === "tool_result") {
    const r = step.result;
    if (r && typeof r === "object") {
      const obj = r as Record<string, unknown>;
      if (obj.error) return `error: ${String(obj.error).slice(0, 80)}`;
      if ("count" in obj) return `ok (count=${obj.count})`;
      if (obj.key) return `ok (${obj.key})`;
      return "ok";
    }
    return r == null ? "empty" : "ok";
  }
  if (step.kind === "tool_error") return String(step.error ?? "");
  return step.content ? String(step.content).slice(0, 120) : "";
}

function Trajectory({ steps }: { steps: TraceStep[] }) {
  const shown = steps.filter((s) =>
    ["stage", "tool_call", "tool_result", "tool_error", "clarification", "final"].includes(s.kind),
  );
  if (!shown.length) return <p className="muted">Трейс пуст</p>;
  return (
    <div>
      {shown.map((s, i) => (
        <div className={`traj-step k-${s.kind}`} key={i}>
          <span className="traj-i">{i}</span>
          <span className="traj-kind">{s.tool_name ?? s.kind}</span>
          <span className="traj-detail">{stepDetail(s)}</span>
        </div>
      ))}
    </div>
  );
}

export function EvalCasePage() {
  const { runId = "", caseId = "" } = useParams();
  const detail = useQuery({
    queryKey: ["eval-case", runId, caseId],
    queryFn: () => api.evalCase(runId, caseId),
    enabled: Boolean(runId && caseId),
  });

  const d = detail.data;
  if (!d) return <div className="loading">Загрузка</div>;

  const criteria = d.criteria ?? d.llm_judge_evaluation?.criteria ?? {};
  const weighted = d.weighted_score ?? d.llm_judge_evaluation?.weighted_score;
  const judge = d.llm_judge_evaluation;
  const steps = (d.agent_raw_output?.steps ?? []) as TraceStep[];
  const tool = d.tool_latency;

  return (
    <div className="shturm-page">
      <section className="surface">
        <div className="section-head">
          <div>
            <h2 style={{ display: "flex", alignItems: "center", gap: 10 }}>
              {d.suite} <span className="muted">/ {d.difficulty}</span>
              <span className={`verdict ${d.passed ? "good" : d.passed === false ? "bad" : "run"}`}>
                {d.passed == null ? d.status : d.passed ? "прошёл" : "провал"}
              </span>
            </h2>
            <p>
              время агента {sec(d.agent_latency_sec)} · судья {sec(d.judge_latency_sec)}
              {d.samples ? ` · панель ×${d.samples}` : ""}
            </p>
          </div>
          <Link className="secondary-button" to={`/eval/${runId}`}>
            ← к прогону
          </Link>
        </div>

        {d.user_text ? (
          <div className="diag-summary" style={{ borderLeftColor: "#2563eb", background: "#eff6ff", color: "#1e3a8a" }}>
            <strong>Запрос:</strong> {d.user_text}
          </div>
        ) : null}

        <section className="surface nested" style={{ marginTop: 14 }}>
          <div className="section-head">
            <div>
              <h3 className="section-title">Вердикт судьи</h3>
              <p className="section-note" style={{ margin: 0 }}>
                Итого <strong>{weighted != null ? score10(weighted) : "—"}</strong>
                {d.judge_explanation ? ` — ${d.judge_explanation}` : ""}
              </p>
            </div>
            <ConfidencePill value={d.confidence} low={d.low_confidence} />
          </div>
          {d.failure_modes?.length ? (
            <div style={{ margin: "6px 0 12px" }}>
              <ModeChips modes={d.failure_modes} />
            </div>
          ) : null}
          <CriteriaTable
            criteria={criteria as Record<string, JudgeCriterionScore>}
            stddev={judge?.criteria_stddev}
          />
        </section>

        <section className="surface nested" style={{ marginTop: 14 }}>
          <h3 className="section-title">Траектория агента</h3>
          <Trajectory steps={steps} />
        </section>

        {tool?.by_op && Object.keys(tool.by_op).length > 0 ? (
          <section className="surface nested" style={{ marginTop: 14 }}>
            <h3 className="section-title">Латентность тулзов</h3>
            <table className="data-table compact">
              <thead>
                <tr>
                  <th>Операция</th>
                  <th>Вызовов</th>
                  <th>Всего</th>
                  <th>p50 / p95</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(tool.by_op).map(([op, s]) => (
                  <tr key={op}>
                    <td>{op}</td>
                    <td>{s.count}</td>
                    <td>{s.total_sec.toFixed(2)}с</td>
                    <td>
                      {s.p50_sec.toFixed(2)}с / {s.p95_sec.toFixed(2)}с
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        ) : null}

        <div style={{ marginTop: 14 }}>
          <JsonBlock title="generated_scenario" data={d.generated_scenario} />
          <JsonBlock title="initial_state (фейк-доска)" data={d.initial_state} />
          <JsonBlock title="expected_operations" data={d.expected_operations} />
          <JsonBlock title="forbidden_operations" data={d.forbidden_operations} />
          <JsonBlock title="agent_normalized_output" data={d.agent_normalized_output} />
          <JsonBlock title="final_fake_tracker_state" data={d.final_fake_tracker_state} />
          <JsonBlock title="deterministic_evaluation" data={d.deterministic_evaluation} />
          <JsonBlock title="llm_judge_evaluation (raw)" data={d.llm_judge_evaluation} />
        </div>
      </section>
    </div>
  );
}
