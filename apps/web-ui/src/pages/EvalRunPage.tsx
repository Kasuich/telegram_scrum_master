import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Ban, Download, Lightbulb, Wrench } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  ConfidencePill,
  CountBar,
  CRITERION_LABELS,
  Kpi,
  ModeChips,
  ScoreBar,
  ShturmHero,
  isRunning,
  modeLabel,
  pct,
  score10,
  sec,
  usd,
  verdict,
} from "../components/shturm";
import {
  api,
  type DiagnosisReport,
  type EvalAnalysis,
  type EvalMetricsSummary,
} from "../lib/api";

const ALL_SUITES = [
  "create_task",
  "update_task",
  "multi_task",
  "hierarchy",
  "duplicate_search",
  "no_task",
];

export function EvalRunPage() {
  const { runId = "" } = useParams();
  const queryClient = useQueryClient();
  const [suiteFilter, setSuiteFilter] = useState("");
  const [onlyFailed, setOnlyFailed] = useState(false);

  const caseParams = useMemo(() => {
    const p = new URLSearchParams({ limit: "200" });
    if (suiteFilter) p.set("suite", suiteFilter);
    if (onlyFailed) p.set("passed", "false");
    return p;
  }, [suiteFilter, onlyFailed]);

  const run = useQuery({
    queryKey: ["eval-run", runId],
    queryFn: () => api.evalRun(runId),
    enabled: Boolean(runId),
    refetchInterval: (q) => (isRunning(q.state.data?.status) ? 3000 : false),
  });
  const cases = useQuery({
    queryKey: ["eval-cases", runId, suiteFilter, onlyFailed],
    queryFn: () => api.evalCases(runId, caseParams),
    enabled: Boolean(runId),
    refetchInterval: isRunning(run.data?.status) ? 3000 : false,
  });

  const cancel = useMutation({
    mutationFn: () => api.cancelEvalRun(runId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["eval-run", runId] }),
  });

  const data = run.data;
  const metrics = (data?.metrics_summary ?? {}) as EvalMetricsSummary;
  const running = isRunning(data?.status);
  const v = verdict(data?.status, data?.pass_rate);
  const progress = data ? (data.completed_cases / Math.max(data.total_cases, 1)) * 100 : 0;

  async function downloadReport() {
    const { markdown } = await api.evalExportMarkdown(runId);
    const blob = new Blob([markdown], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `shturm-${data?.name ?? runId}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="shturm-page">
      <ShturmHero
        title={data?.name ?? "Прогон"}
        subtitle={
          data
            ? `судья ${data.judge_model ?? "—"} · агент-модель flash-lite${
                data.git_commit ? ` · ${data.git_commit.slice(0, 7)}` : ""
              }`
            : "загрузка…"
        }
      >
        <span className={`verdict ${v.kind}`}>{v.label}</span>
        {running ? (
          <button className="secondary-button" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
            <Ban className="h-4 w-4" /> Отменить
          </button>
        ) : (
          <button className="secondary-button" onClick={downloadReport}>
            <Download className="h-4 w-4" /> Отчёт MD
          </button>
        )}
      </ShturmHero>

      {/* Progress while running */}
      {running && (
        <section className="surface">
          <div className="section-head">
            <div>
              <h2>Прогресс</h2>
              <p>{data?.status}</p>
            </div>
            <strong>
              {data?.completed_cases ?? 0}/{data?.total_cases ?? 0}
            </strong>
          </div>
          <div className="shturm-progress">
            <div style={{ width: `${progress}%` }} />
          </div>
        </section>
      )}

      {/* KPIs */}
      <section className="kpi-grid">
        <Kpi
          label="Pass rate"
          value={pct(data?.pass_rate)}
          tone={data?.pass_rate == null ? undefined : data.pass_rate >= 0.85 ? "good" : data.pass_rate >= 0.6 ? "warn" : "bad"}
          sub={`${data?.passed_cases ?? 0}/${data?.completed_cases ?? 0} прошло`}
        />
        <Kpi label="Avg score" value={score10(metrics.avg_weighted_score)} sub="взвешенно" />
        <Kpi
          label="Достоверность"
          value={score10(metrics.faithfulness_avg)}
          tone={metrics.faithfulness_avg == null ? undefined : metrics.faithfulness_avg >= 8 ? "good" : "warn"}
          sub={`галлюцинации ${pct(metrics.hallucination_rate)}`}
        />
        <Kpi
          label="Доверие судьи"
          value={metrics.avg_judge_confidence == null ? "—" : `${(metrics.avg_judge_confidence * 100).toFixed(0)}%`}
          tone={
            metrics.avg_judge_confidence == null
              ? undefined
              : metrics.avg_judge_confidence >= 0.8
                ? "good"
                : metrics.avg_judge_confidence >= 0.6
                  ? "warn"
                  : "bad"
          }
          sub={`low-conf ${pct(metrics.low_confidence_rate)}`}
        />
        <Kpi label="Avg время агента" value={sec(data?.avg_agent_latency_sec)} sub={`p95 ${sec(data?.p95_agent_latency_sec)}`} />
        <Kpi label="Доля времени тулзов" value={metrics.tool_time_share == null ? "—" : pct(metrics.tool_time_share)} sub={`всего ${sec(metrics.total_tool_latency_sec)}`} />
        <Kpi label="Стоимость судьи" value={usd(metrics.judge_cost_usd)} sub={metrics.judge_trust ? `${metrics.judge_trust.llm_judged} LLM / ${metrics.judge_trust.heuristic_judged} эвр.` : undefined} />
        <Kpi label="Таймауты" value={data?.timeout_cases ?? 0} tone={(data?.timeout_cases ?? 0) > 0 ? "warn" : undefined} />
      </section>

      {metrics.diagnosis ? <DiagnosisPanel diagnosis={metrics.diagnosis} /> : null}

      <div className="page-grid" style={{ gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)" }}>
        <CriteriaPanel criteriaAvg={metrics.criteria_avg} />
        <FailureModesPanel analysis={metrics.analysis} />
      </div>

      <ToolLatencyPanel metrics={metrics} />
      <SuitePanel metrics={metrics} />

      {/* Cases */}
      <section className="surface">
        <div className="section-head">
          <div>
            <h2>Кейсы</h2>
            <p>{cases.data?.total ?? 0} всего</p>
          </div>
          <div className="row gap-2" style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <label className="checkbox" style={{ margin: 0 }}>
              <input type="checkbox" checked={onlyFailed} onChange={(e) => setOnlyFailed(e.target.checked)} />
              только провалы
            </label>
            <select value={suiteFilter} onChange={(e) => setSuiteFilter(e.target.value)}>
              <option value="">Все suites</option>
              {ALL_SUITES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
        </div>
        <table className="data-table">
          <thead>
            <tr>
              <th>Suite / сложность</th>
              <th>Статус</th>
              <th>Score</th>
              <th>Достов.</th>
              <th>Доверие</th>
              <th>Режимы отказа</th>
              <th>Время</th>
            </tr>
          </thead>
          <tbody>
            {(cases.data?.items ?? []).map((c) => (
              <tr key={c.id}>
                <td>
                  <Link to={`/eval/${runId}/cases/${c.id}`}>{c.suite}</Link>
                  <span className="muted"> · {c.difficulty}</span>
                </td>
                <td>{c.passed == null ? c.status : c.passed ? "✓ прошёл" : "✗ провал"}</td>
                <td>{c.weighted_score != null ? score10(c.weighted_score) : "—"}</td>
                <td>{c.faithfulness != null ? c.faithfulness.toFixed(1) : "—"}</td>
                <td>
                  <ConfidencePill value={c.confidence} low={c.low_confidence} />
                </td>
                <td>
                  <ModeChips modes={c.failure_modes} />
                  {!c.failure_modes?.length && c.main_error ? (
                    <span className="muted truncate">{c.main_error}</span>
                  ) : null}
                </td>
                <td>{sec(c.agent_latency_sec)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function DiagnosisPanel({ diagnosis }: { diagnosis: DiagnosisReport }) {
  const problems = diagnosis.top_problems ?? [];
  const improvements = diagnosis.improvements ?? [];
  return (
    <section className="surface">
      <h2 className="section-title">
        <AlertTriangle className="h-4 w-4" style={{ color: "var(--shturm)" }} /> Где агент тупит
      </h2>
      {diagnosis.summary ? <div className="diag-summary">{diagnosis.summary}</div> : null}

      {problems.length > 0 && (
        <div style={{ marginTop: 12 }}>
          {problems.map((p, i) => (
            <div key={i} className={`problem-card sev-${p.severity}`}>
              <div className="p-title">
                <span className={`verdict ${p.severity === "high" ? "bad" : p.severity === "medium" ? "warn" : "run"}`}>
                  {p.severity}
                </span>
                {p.title}
              </div>
              {p.evidence ? <div className="p-evidence">{p.evidence}</div> : null}
              {p.failure_modes?.length ? <div style={{ marginTop: 6 }}><ModeChips modes={p.failure_modes} /></div> : null}
            </div>
          ))}
        </div>
      )}

      {improvements.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <h3 className="section-title" style={{ fontSize: 14 }}>
            <Lightbulb className="h-4 w-4" style={{ color: "var(--warn)" }} /> Что поправить
          </h3>
          {improvements.map((imp, i) => (
            <div key={i} className="improvement">
              <span className={`prio prio-${imp.priority}`}>{imp.priority}</span>
              <div className="imp-body">
                <div className="imp-suggest">
                  <span className="area-chip">{imp.area}</span> {imp.suggestion}
                </div>
                {imp.rationale ? <div className="imp-why">{imp.rationale}</div> : null}
              </div>
            </div>
          ))}
        </div>
      )}
      {diagnosis.generated_by ? (
        <p className="section-note" style={{ marginTop: 12 }}>
          Диагноз сгенерирован {diagnosis.generated_by}
        </p>
      ) : null}
    </section>
  );
}

function CriteriaPanel({ criteriaAvg }: { criteriaAvg?: Record<string, number> }) {
  const entries = Object.entries(criteriaAvg ?? {});
  return (
    <section className="surface">
      <h2 className="section-title">Критерии судьи (среднее)</h2>
      {entries.length === 0 ? (
        <p className="muted">Нет оценок</p>
      ) : (
        entries.map(([name, avg]) => (
          <div className="bar-row" key={name}>
            <span className="bar-label">{CRITERION_LABELS[name] ?? name}</span>
            <ScoreBar value={avg} />
            <span className="bar-num">{avg.toFixed(1)}</span>
          </div>
        ))
      )}
    </section>
  );
}

function FailureModesPanel({ analysis }: { analysis?: EvalAnalysis }) {
  const modes = analysis?.failure_modes ?? [];
  const max = Math.max(1, ...modes.map((m) => m.count));
  return (
    <section className="surface">
      <h2 className="section-title">
        <Wrench className="h-4 w-4" style={{ color: "var(--shturm)" }} /> Режимы отказа
      </h2>
      {modes.length === 0 ? (
        <p className="muted">Провалов не зафиксировано 🎉</p>
      ) : (
        modes.map((m) => (
          <div className="bar-row" key={m.mode}>
            <span className="bar-label" title={m.mode}>
              {m.label ?? modeLabel(m.mode)}
            </span>
            <CountBar value={m.count} max={max} />
            <span className="bar-num">{m.count}</span>
          </div>
        ))
      )}
    </section>
  );
}

function ToolLatencyPanel({ metrics }: { metrics: EvalMetricsSummary }) {
  const ops = Object.entries(metrics.tool_latency_by_op ?? {});
  if (ops.length === 0) return null;
  const max = Math.max(1, ...ops.map(([, s]) => s.total_sec));
  return (
    <section className="surface">
      <h2 className="section-title">Латентность тулзов (симуляция фейк-трекера)</h2>
      <p className="section-note">
        Воспроизводит реальные задержки Трекера — поэтому время агента и таймауты имеют смысл.
      </p>
      {ops.map(([op, s]) => (
        <div className="bar-row" key={op}>
          <span className="bar-label">{op}</span>
          <CountBar value={s.total_sec} max={max} />
          <span className="bar-num">{s.avg_sec != null ? `${s.avg_sec.toFixed(2)}с` : "—"}</span>
        </div>
      ))}
    </section>
  );
}

function SuitePanel({ metrics }: { metrics: EvalMetricsSummary }) {
  const suites = Object.entries(metrics.suite_stats ?? {});
  if (suites.length === 0) return null;
  const latBySuite = metrics.agent_latency_by_suite ?? {};
  const critBySuite = metrics.criteria_by_suite ?? {};
  return (
    <section className="surface">
      <h2 className="section-title">По suite</h2>
      <table className="data-table">
        <thead>
          <tr>
            <th>Suite</th>
            <th>N</th>
            <th>Pass rate</th>
            <th>Avg score</th>
            <th>Action</th>
            <th>Avg время</th>
          </tr>
        </thead>
        <tbody>
          {suites.map(([suite, st]) => {
            const crit = critBySuite[suite];
            const lat = latBySuite[suite];
            return (
              <tr key={suite}>
                <td>{suite}</td>
                <td>{st.n}</td>
                <td>{pct(st.pass_rate)}</td>
                <td>{crit?.weighted_score != null ? score10(crit.weighted_score) : "—"}</td>
                <td>{crit?.action_correctness != null ? crit.action_correctness.toFixed(1) : "—"}</td>
                <td>{sec(lat?.avg ?? null)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}
