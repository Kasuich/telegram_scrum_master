import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { api, type JudgeCriterionScore } from "../lib/api";

const CRITERION_LABELS: Record<string, string> = {
  action_correctness: "Тип действия",
  intent_alignment: "Соответствие запросу",
  forbidden_compliance: "Запрещённые операции",
  completeness: "Полнота",
  final_state_quality: "Состояние трекера",
};

function JsonBlock({ title, data }: { title: string; data: unknown }) {
  if (data == null) return null;
  return (
    <details className="audit-block">
      <summary>{title}</summary>
      <pre>{JSON.stringify(data, null, 2)}</pre>
    </details>
  );
}

function CriteriaTable({ criteria }: { criteria: Record<string, JudgeCriterionScore> }) {
  const entries = Object.entries(criteria);
  if (!entries.length) return <p className="muted">Нет оценок</p>;

  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Критерий</th>
          <th>Оценка</th>
          <th>Вес</th>
          <th>Вклад</th>
          <th>Комментарий</th>
        </tr>
      </thead>
      <tbody>
        {entries.map(([name, c]) => (
          <tr key={name}>
            <td>{CRITERION_LABELS[name] ?? name}</td>
            <td>{c.score.toFixed(1)}/10</td>
            <td>{(c.weight * 100).toFixed(0)}%</td>
            <td>{(c.score * c.weight).toFixed(2)}</td>
            <td className="truncate">{c.reason || "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
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

  return (
    <div className="page-grid">
      <section className="surface">
        <div className="section-head">
          <h2>
            Case {d.suite} / {d.difficulty}
          </h2>
          <p>
            {d.status} · agent {d.agent_latency_sec ?? "—"}s · passed{" "}
            {d.passed == null ? "—" : d.passed ? "✓" : "✗"}
          </p>
        </div>

        <section className="surface nested">
          <div className="section-head">
            <h3>Оценка судьи</h3>
            <p>
              Итого: <strong>{weighted != null ? `${weighted.toFixed(1)}/10` : "—"}</strong>
              {d.judge_explanation ? ` — ${d.judge_explanation}` : ""}
            </p>
          </div>
          <CriteriaTable criteria={criteria} />
        </section>

        <JsonBlock title="user_text" data={d.user_text} />
        <JsonBlock title="generated_scenario" data={d.generated_scenario} />
        <JsonBlock title="initial_state" data={d.initial_state} />
        <JsonBlock title="expected_operations" data={d.expected_operations} />
        <JsonBlock title="forbidden_operations" data={d.forbidden_operations} />
        <JsonBlock title="agent_raw_output" data={d.agent_raw_output} />
        <JsonBlock title="agent_normalized_output" data={d.agent_normalized_output} />
        <JsonBlock title="final_fake_tracker_state" data={d.final_fake_tracker_state} />
        <JsonBlock title="deterministic_evaluation (audit)" data={d.deterministic_evaluation} />
        <JsonBlock title="llm_judge_evaluation (raw)" data={d.llm_judge_evaluation} />
        <JsonBlock title="final_evaluation" data={d.final_evaluation} />
      </section>
    </div>
  );
}
