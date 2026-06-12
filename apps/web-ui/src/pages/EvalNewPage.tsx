import { useMutation } from "@tanstack/react-query";
import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ShturmHero } from "../components/shturm";
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
    name: "Штурм PMAgent",
    n_cases: 50,
    suites: SUITES,
    scenario_generation_concurrency: 16,
    user_text_generation_concurrency: 16,
    agent_concurrency: 10,
    judge_concurrency: 6,
    timeout_sec_per_case: 180,
    generator_model: "google/gemini-3.1-flash-lite",
    judge_model: "google/gemini-3.1-pro-preview",
    use_llm_judge: true,
    use_real_tracker: false,
    judge_samples: 3,
    simulate_tool_latency: true,
    simulate_tracker_errors: false,
    tool_latency_scale: 1.0,
  });

  const create = useMutation({
    mutationFn: () => api.createEvalRun(form),
    onSuccess: (data) => navigate(`/eval/${data.run_id}`),
  });

  function set<K extends keyof CreateEvalRunBody>(key: K, value: CreateEvalRunBody[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  function num(key: keyof CreateEvalRunBody) {
    return (e: React.ChangeEvent<HTMLInputElement>) =>
      set(key, Number(e.target.value) as never);
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate();
  }

  return (
    <div className="shturm-page">
      <ShturmHero
        title="Новый прогон"
        subtitle="Сгенерируем сценарии, прогоним агента в изолированной фейк-доске и осудим панелью «Штурм»"
      />

      <section className="surface">
        <form className="form-grid" style={{ maxWidth: "100%" }} onSubmit={submit}>
          <fieldset className="cfg-section" style={{ border: 0, padding: 0, margin: 0 }}>
            <div className="cfg-legend">Основное</div>
            <div className="cfg-grid">
              <label className="field">
                <span>Название</span>
                <input value={form.name} onChange={(e) => set("name", e.target.value)} required />
              </label>
              <label className="field">
                <span>Количество кейсов</span>
                <input type="number" min={1} max={500} value={form.n_cases} onChange={num("n_cases")} />
              </label>
              <label className="field">
                <span>Таймаут на кейс, с</span>
                <input
                  type="number"
                  min={30}
                  max={600}
                  value={form.timeout_sec_per_case}
                  onChange={num("timeout_sec_per_case")}
                />
              </label>
            </div>
          </fieldset>

          <fieldset className="cfg-section" style={{ border: 0 }}>
            <legend className="cfg-legend">Модели</legend>
            <div className="cfg-grid">
              <label className="field">
                <span>Генератор сценариев</span>
                <input
                  value={form.generator_model}
                  onChange={(e) => set("generator_model", e.target.value)}
                />
              </label>
              <label className="field">
                <span>Судья «Штурм»</span>
                <input value={form.judge_model} onChange={(e) => set("judge_model", e.target.value)} />
              </label>
            </div>
          </fieldset>

          <fieldset className="cfg-section" style={{ border: 0 }}>
            <legend className="cfg-legend">Судья (доверие)</legend>
            <div className="cfg-grid">
              <label className="field">
                <span>Самосогласование: прогонов судьи</span>
                <input
                  type="number"
                  min={1}
                  max={7}
                  value={form.judge_samples}
                  onChange={num("judge_samples")}
                />
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={form.use_llm_judge}
                  onChange={(e) => set("use_llm_judge", e.target.checked)}
                />
                LLM-судья (иначе эвристика)
              </label>
            </div>
            <p className="section-note">
              К&gt;1 — панель: судья голосует несколько раз, итог берётся по медиане, разброс даёт
              доверие. Это дороже по судье (≈K×), но метрикам можно верить.
            </p>
          </fieldset>

          <fieldset className="cfg-section" style={{ border: 0 }}>
            <legend className="cfg-legend">Фейк-трекер (реализм)</legend>
            <div className="cfg-grid">
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={form.simulate_tool_latency}
                  onChange={(e) => set("simulate_tool_latency", e.target.checked)}
                />
                Реалистичные задержки тулзов
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={form.simulate_tracker_errors}
                  onChange={(e) => set("simulate_tracker_errors", e.target.checked)}
                />
                Симуляция 429 / тормозов
              </label>
              <label className="field">
                <span>Масштаб задержек (1.0 = реально)</span>
                <input
                  type="number"
                  step={0.1}
                  min={0}
                  max={10}
                  value={form.tool_latency_scale}
                  onChange={num("tool_latency_scale")}
                />
              </label>
            </div>
            <p className="section-note">
              У каждого кейса своя изолированная доска (seed по кейсу). Задержки берутся из
              лог-нормального распределения по реальным латентностям Трекера.
            </p>
          </fieldset>

          <fieldset className="cfg-section" style={{ border: 0 }}>
            <legend className="cfg-legend">Конкурентность (по стадиям)</legend>
            <div className="cfg-grid">
              <label className="field">
                <span>Генерация сценариев</span>
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={form.scenario_generation_concurrency}
                  onChange={num("scenario_generation_concurrency")}
                />
              </label>
              <label className="field">
                <span>Генерация запросов</span>
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={form.user_text_generation_concurrency}
                  onChange={num("user_text_generation_concurrency")}
                />
              </label>
              <label className="field">
                <span>Агент</span>
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={form.agent_concurrency}
                  onChange={num("agent_concurrency")}
                />
              </label>
              <label className="field">
                <span>Судья (pro — узко)</span>
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={form.judge_concurrency}
                  onChange={num("judge_concurrency")}
                />
              </label>
            </div>
            <p className="section-note">
              flash-lite стадии — широко, судья на pro — узко (дороже, ниже TPM в OpenRouter).
              429 само-залечивается ретраями с jitter.
            </p>
          </fieldset>

          <fieldset className="cfg-section" style={{ border: 0 }}>
            <legend className="cfg-legend">Suites</legend>
            <div className="cfg-grid">
              {SUITES.map((suite) => (
                <label key={suite} className="checkbox">
                  <input
                    type="checkbox"
                    checked={form.suites.includes(suite)}
                    onChange={(e) =>
                      set(
                        "suites",
                        e.target.checked
                          ? [...form.suites, suite]
                          : form.suites.filter((s) => s !== suite),
                      )
                    }
                  />
                  {suite}
                </label>
              ))}
            </div>
          </fieldset>

          <label className="checkbox">
            <input
              type="checkbox"
              checked={form.use_real_tracker}
              onChange={(e) => set("use_real_tracker", e.target.checked)}
            />
            Real Tracker smoke (опасно — запись в реальный Трекер)
          </label>
          {form.use_real_tracker && (
            <p className="warning">
              Внимание: запись в реальный Яндекс Трекер. Требуется ALLOW_REAL_TRACKER_EVAL=true.
            </p>
          )}

          {create.error && <p className="error">{create.error.message}</p>}
          <button className="primary-button" type="submit" disabled={create.isPending}>
            {create.isPending ? "Запускаю…" : "Запустить «Штурм»"}
          </button>
        </form>
      </section>
    </div>
  );
}
