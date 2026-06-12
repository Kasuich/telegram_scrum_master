import { useMutation } from "@tanstack/react-query";
import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, type CreateEvalRunBody } from "../lib/api";

const SUITES = [
  "create_task",
  "update_task",
  "multi_task",
  "hierarchy",
  "duplicate_search",
  "no_task",
];

export function EvalNewPage() {
  const navigate = useNavigate();
  const [form, setForm] = useState<CreateEvalRunBody>({
    name: "PMAgent eval",
    n_cases: 50,
    suites: SUITES,
    scenario_generation_concurrency: 20,
    user_text_generation_concurrency: 20,
    agent_concurrency: 20,
    judge_concurrency: 20,
    timeout_sec_per_case: 180,
    generator_model: "google/gemini-3.1-flash-lite",
    judge_model: "google/gemini-3.1-pro-preview",
    use_llm_judge: true,
    use_real_tracker: false,
  });

  const create = useMutation({
    mutationFn: () => api.createEvalRun(form),
    onSuccess: (data) => navigate(`/eval/${data.run_id}`),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate();
  }

  return (
    <div className="page-grid">
      <section className="surface">
        <div className="section-head">
          <h2>Новый eval run</h2>
        </div>
        <form className="form-grid" onSubmit={submit}>
          <label>
            Название
            <input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              required
            />
          </label>
          <label>
            Количество кейсов
            <input
              type="number"
              min={1}
              max={500}
              value={form.n_cases}
              onChange={(e) => setForm({ ...form, n_cases: Number(e.target.value) })}
            />
          </label>
          <label>
            Generator model
            <input
              value={form.generator_model}
              onChange={(e) => setForm({ ...form, generator_model: e.target.value })}
            />
          </label>
          <label>
            Judge model
            <input
              value={form.judge_model}
              onChange={(e) => setForm({ ...form, judge_model: e.target.value })}
            />
          </label>
          <label>
            Agent concurrency
            <input
              type="number"
              value={form.agent_concurrency}
              onChange={(e) => setForm({ ...form, agent_concurrency: Number(e.target.value) })}
            />
          </label>
          <label>
            Timeout per case (sec)
            <input
              type="number"
              value={form.timeout_sec_per_case}
              onChange={(e) => setForm({ ...form, timeout_sec_per_case: Number(e.target.value) })}
            />
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={form.use_llm_judge}
              onChange={(e) => setForm({ ...form, use_llm_judge: e.target.checked })}
            />
            LLM judge
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={form.use_real_tracker}
              onChange={(e) => setForm({ ...form, use_real_tracker: e.target.checked })}
            />
            Real Tracker smoke (опасно)
          </label>
          {form.use_real_tracker && (
            <p className="warning">
              Внимание: запись в реальный Яндекс Трекер. Требуется ALLOW_REAL_TRACKER_EVAL=true.
            </p>
          )}
          <fieldset>
            <legend>Suites</legend>
            {SUITES.map((suite) => (
              <label key={suite} className="checkbox">
                <input
                  type="checkbox"
                  checked={form.suites.includes(suite)}
                  onChange={(e) => {
                    const suites = e.target.checked
                      ? [...form.suites, suite]
                      : form.suites.filter((s) => s !== suite);
                    setForm({ ...form, suites });
                  }}
                />
                {suite}
              </label>
            ))}
          </fieldset>
          {create.error && <p className="error">{create.error.message}</p>}
          <button className="button primary" type="submit" disabled={create.isPending}>
            Start
          </button>
        </form>
      </section>
    </div>
  );
}
