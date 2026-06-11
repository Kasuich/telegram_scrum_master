import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Save, Wrench } from "lucide-react";
import { useEffect, useState } from "react";

import { api, type AgentTool } from "../lib/api";
import { RiskBadge } from "./Badge";

type ConfirmMode = "risk" | "always" | "never";

function confirmToMode(confirm: boolean | null): ConfirmMode {
  if (confirm === true) return "always";
  if (confirm === false) return "never";
  return "risk";
}

function modeToConfirm(mode: ConfirmMode): boolean | null {
  if (mode === "always") return true;
  if (mode === "never") return false;
  return null;
}

const MODE_LABEL: Record<ConfirmMode, string> = {
  risk: "По риску",
  always: "Всегда",
  never: "Никогда",
};

export function AgentToolsPanel({ agent }: { agent: string }) {
  const queryClient = useQueryClient();
  const tools = useQuery({ queryKey: ["agent-tools", agent], queryFn: () => api.agentTools(agent) });
  const [draft, setDraft] = useState<AgentTool[]>([]);

  useEffect(() => {
    setDraft(tools.data ?? []);
  }, [tools.data]);

  const save = useMutation({
    mutationFn: () =>
      api.patchAgentTools(
        agent,
        draft.map((tool) => ({ name: tool.name, enabled: tool.enabled, confirm: tool.confirm })),
      ),
    onSuccess: (updated) => {
      queryClient.setQueryData(["agent-tools", agent], updated);
      setDraft(updated);
    },
  });

  const dirty = JSON.stringify(draft) !== JSON.stringify(tools.data ?? []);

  function update(name: string, patch: Partial<AgentTool>) {
    setDraft((items) => items.map((tool) => (tool.name === name ? { ...tool, ...patch } : tool)));
  }

  return (
    <section className="surface">
      <div className="section-head">
        <div>
          <h2>Инструменты</h2>
          <p>{draft.length} тулов · подтверждение настраивается на тул</p>
        </div>
        <button className="primary-button" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
          <Save className="h-4 w-4" />
          Сохранить
        </button>
      </div>

      {tools.isLoading ? (
        <div className="empty">Загрузка</div>
      ) : !draft.length ? (
        <div className="empty">У агента нет объявленных тулов</div>
      ) : (
        <div className="tool-list">
          {draft.map((tool) => (
            <div className={`tool-row ${tool.enabled ? "" : "disabled"}`} key={tool.name}>
              <div className="tool-main">
                <label className="switch">
                  <input
                    checked={tool.enabled}
                    type="checkbox"
                    onChange={(event) => update(tool.name, { enabled: event.target.checked })}
                  />
                </label>
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-medium">{tool.name}</span>
                    <RiskBadge risk={tool.risk} />
                  </div>
                  {tool.description ? (
                    <div className="truncate text-xs text-muted">{tool.description}</div>
                  ) : null}
                </div>
              </div>
              <div className="segmented confirm-seg" role="group" aria-label={`Подтверждение ${tool.name}`}>
                {(["risk", "always", "never"] as const).map((mode) => (
                  <button
                    className={confirmToMode(tool.confirm) === mode ? "active" : ""}
                    disabled={!tool.enabled}
                    key={mode}
                    onClick={() => update(tool.name, { confirm: modeToConfirm(mode) })}
                  >
                    {MODE_LABEL[mode]}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
      <div className="mt-3 flex items-center gap-2 text-xs text-muted">
        <Wrench className="h-3.5 w-3.5" />
        «По риску» — подтверждение по уровню риска тула; «Всегда» / «Никогда» — жёсткий override.
      </div>
    </section>
  );
}
