import { Save, Shield, ToggleLeft, ToggleRight } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import type { AgentConfig, Autonomy, RiskLevel } from "../lib/api";
import { riskLabel } from "../lib/format";

const riskLevels: RiskLevel[] = ["low", "medium", "high"];

function toggleList(list: RiskLevel[], value: RiskLevel): RiskLevel[] {
  return list.includes(value) ? list.filter((item) => item !== value) : [...list, value];
}

function riskOverlap(autonomy: Autonomy): RiskLevel[] {
  const confirmRisks = new Set(autonomy.confirm_risk);
  return autonomy.auto_risk.filter((risk) => confirmRisks.has(risk));
}

function normalizeAutonomy(autonomy: Autonomy): Autonomy {
  const confirmRisks = new Set(autonomy.confirm_risk);
  return {
    ...autonomy,
    auto_risk: autonomy.auto_risk.filter((risk) => !confirmRisks.has(risk)),
  };
}

function toggleAutoRisk(autonomy: Autonomy, value: RiskLevel): Autonomy {
  const auto_risk = toggleList(autonomy.auto_risk, value);
  return {
    ...autonomy,
    auto_risk,
    confirm_risk: auto_risk.includes(value)
      ? autonomy.confirm_risk.filter((risk) => risk !== value)
      : autonomy.confirm_risk,
  };
}

function toggleConfirmRisk(autonomy: Autonomy, value: RiskLevel): Autonomy {
  const confirm_risk = toggleList(autonomy.confirm_risk, value);
  return {
    ...autonomy,
    auto_risk: confirm_risk.includes(value)
      ? autonomy.auto_risk.filter((risk) => risk !== value)
      : autonomy.auto_risk,
    confirm_risk,
  };
}

export function AgentConfigPanel({
  config,
  onSaveSpec,
  onSaveOverlay,
}: {
  config: AgentConfig;
  onSaveSpec: (body: { prompt: string; model: string }) => void;
  onSaveOverlay: (body: { enabled: boolean; autonomy: Autonomy }) => void;
}) {
  const [prompt, setPrompt] = useState(config.prompt);
  const [model, setModel] = useState(config.model);
  const [enabled, setEnabled] = useState(config.enabled);
  const [autonomy, setAutonomy] = useState<Autonomy>(() => normalizeAutonomy(config.autonomy));

  useEffect(() => {
    setPrompt(config.prompt);
    setModel(config.model);
    setEnabled(config.enabled);
    setAutonomy(normalizeAutonomy(config.autonomy));
  }, [config]);

  const specDirty = prompt !== config.prompt || model !== config.model;
  const overlayDirty = useMemo(
    () => enabled !== config.enabled || JSON.stringify(autonomy) !== JSON.stringify(config.autonomy),
    [autonomy, config.autonomy, config.enabled, enabled],
  );
  const overlappingRisks = riskOverlap(autonomy);
  const hasRiskOverlap = overlappingRisks.length > 0;

  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
      <section className="surface">
        <div className="section-head">
          <div>
            <h2>{config.name}</h2>
            <p>{config.description || "runtime-агент"}</p>
          </div>
          <button className="primary-button" disabled={!specDirty} onClick={() => onSaveSpec({ prompt, model })}>
            <Save className="h-4 w-4" />
            Сохранить базу
          </button>
        </div>
        <label className="field">
          <span>Модель</span>
          <input value={model} onChange={(event) => setModel(event.target.value)} />
        </label>
        <label className="field mt-4">
          <span>
            Промпт{" "}
            <span className="mono-chip">
              {config.has_spec ? "переопределён" : "базовый промпт класса"}
            </span>{" "}
            <span className="text-xs text-muted">{prompt.length} символов</span>
          </span>
          <textarea
            value={prompt}
            rows={Math.min(28, Math.max(8, prompt.split("\n").length + 1))}
            onChange={(event) => setPrompt(event.target.value)}
          />
        </label>
      </section>

      <section className="surface">
        <div className="section-head">
          <div>
            <h2>Безопасность</h2>
            <p>оверлей команды</p>
          </div>
          <button
            className="icon-button"
            aria-label="Переключить агента"
            title="Переключить агента"
            onClick={() => setEnabled((value) => !value)}
          >
            {enabled ? <ToggleRight className="h-5 w-5 text-teal" /> : <ToggleLeft className="h-5 w-5 text-rose" />}
          </button>
        </div>

        <div className="control-row">
          <Shield className="h-4 w-4 text-muted" />
          <span className="font-medium">{enabled ? "Включён" : "Отключён"}</span>
        </div>

        <div className="mt-5 space-y-4">
          <RiskToggleGroup
            label="Автовыполнение"
            selected={autonomy.auto_risk}
            onChange={(value) => setAutonomy((current) => toggleAutoRisk(current, value))}
          />
          <RiskToggleGroup
            label="Подтверждение"
            selected={autonomy.confirm_risk}
            onChange={(value) => setAutonomy((current) => toggleConfirmRisk(current, value))}
          />
          <label className="field">
            <span>Всегда подтверждать инструменты</span>
            <input
              value={autonomy.always_confirm_tools.join(", ")}
              onChange={(event) =>
                setAutonomy((current) => ({
                  ...current,
                  always_confirm_tools: event.target.value
                    .split(",")
                    .map((item) => item.trim())
                    .filter(Boolean),
                }))
              }
            />
          </label>
          {hasRiskOverlap ? (
            <div className="error-line">
              Один уровень риска нельзя одновременно добавить в автовыполнение и подтверждение:{" "}
              {overlappingRisks.map((risk) => riskLabel[risk]).join(", ")}
            </div>
          ) : null}
        </div>

        <button
          className="primary-button mt-5 w-full justify-center"
          disabled={!overlayDirty || hasRiskOverlap}
          onClick={() => onSaveOverlay({ enabled, autonomy })}
        >
          <Save className="h-4 w-4" />
          Сохранить оверлей
        </button>
      </section>
    </div>
  );
}

function RiskToggleGroup({
  label,
  selected,
  onChange,
}: {
  label: string;
  selected: RiskLevel[];
  onChange: (risk: RiskLevel) => void;
}) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className="segmented mt-2" role="group" aria-label={label}>
        {riskLevels.map((risk) => (
          <button className={selected.includes(risk) ? "active" : ""} key={risk} onClick={() => onChange(risk)}>
            {riskLabel[risk]}
          </button>
        ))}
      </div>
    </div>
  );
}
