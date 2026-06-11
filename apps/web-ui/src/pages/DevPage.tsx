import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, FileCode2, ListTree } from "lucide-react";
import { useMemo, useState } from "react";

import { AgentConfigPanel } from "../components/AgentConfigPanel";
import { AgentToolsPanel } from "../components/AgentToolsPanel";
import { EnabledBadge, RiskBadge, StatusBadge } from "../components/Badge";
import { TraceTimeline } from "../components/TraceTimeline";
import { api, type AgentListItem } from "../lib/api";
import { formatDate, shortId } from "../lib/format";

export function DevPage() {
  const queryClient = useQueryClient();
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const [selectedAction, setSelectedAction] = useState<string | null>(null);
  const agents = useQuery({ queryKey: ["agents"], queryFn: api.agents });
  const activeAgent = selectedAgent ?? agents.data?.[0]?.name ?? null;
  const actionParams = useMemo(() => new URLSearchParams({ limit: "40" }), []);
  const actions = useQuery({ queryKey: ["actions", "dev"], queryFn: () => api.actions(actionParams) });
  const config = useQuery({
    queryKey: ["agent-config", activeAgent],
    queryFn: () => api.agentConfig(activeAgent as string),
    enabled: Boolean(activeAgent),
  });
  const detail = useQuery({
    queryKey: ["action-detail", selectedAction],
    queryFn: () => api.actionDetail(selectedAction as string),
    enabled: Boolean(selectedAction),
  });

  const specMutation = useMutation({
    mutationFn: ({ name, body }: { name: string; body: { prompt: string; model: string } }) =>
      api.patchAgentSpec(name, body),
    onSuccess: (updated) => {
      queryClient.setQueryData(["agent-config", updated.name], updated);
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
  });
  const overlayMutation = useMutation({
    mutationFn: ({ name, body }: { name: string; body: Parameters<typeof api.patchAgentOverlay>[1] }) =>
      api.patchAgentOverlay(name, body),
    onSuccess: (updated) => {
      queryClient.setQueryData(["agent-config", updated.name], updated);
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
  });

  return (
    <div className="page-grid">
      <section className="surface">
        <div className="section-head">
          <div>
            <h2>Агенты</h2>
            <p>{agents.data?.length ?? 0} зарегистрировано</p>
          </div>
          <Bot className="h-5 w-5 text-muted" />
        </div>
        <div className="list">
          {(agents.data ?? []).map((agent) => (
            <AgentRow
              agent={agent}
              key={agent.name}
              selected={agent.name === activeAgent}
              onClick={() => setSelectedAgent(agent.name)}
            />
          ))}
        </div>
      </section>

      <div className="wide">
        <div className="section-head">
          <div>
            <h2>Конфигурация</h2>
            <p>{activeAgent ?? "агент не выбран"}</p>
          </div>
          <FileCode2 className="h-5 w-5 text-muted" />
        </div>
        {activeAgent && config.data ? (
          <>
            <AgentConfigPanel
              config={config.data}
              onSaveSpec={(body) => specMutation.mutate({ name: activeAgent, body })}
              onSaveOverlay={(body) => overlayMutation.mutate({ name: activeAgent, body })}
            />
            <div className="mt-4">
              <AgentToolsPanel agent={activeAgent} />
            </div>
          </>
        ) : (
          <div className="surface empty">Выберите агента</div>
        )}
      </div>

      <section className="surface">
        <div className="section-head">
          <div>
            <h2>Трейсы</h2>
            <p>последние действия</p>
          </div>
          <ListTree className="h-5 w-5 text-muted" />
        </div>
        <div className="list">
          {(actions.data ?? []).map((action) => (
            <button
              className={`list-row text-left ${selectedAction === action.id ? "selected" : ""}`}
              key={action.id}
              onClick={() => setSelectedAction(action.id)}
            >
              <div className="flex min-w-0 items-center gap-2">
                <span className="mono-chip">{shortId(action.id)}</span>
                <span className="truncate font-medium">{action.tool_name}</span>
              </div>
              <div className="mt-2 flex flex-wrap gap-2">
                <StatusBadge status={action.status} />
                <RiskBadge risk={action.risk_level} />
                <span className="text-xs text-muted">{formatDate(action.created_at)}</span>
              </div>
            </button>
          ))}
        </div>
      </section>

      <section className="surface wide">
        <div className="section-head">
          <div>
            <h2>Детали трейса</h2>
            <p>{selectedAction ? shortId(selectedAction) : "не выбрано"}</p>
          </div>
        </div>
        {detail.data?.trace ? <TraceTimeline steps={detail.data.trace.steps} /> : <div className="empty">Выберите трейс</div>}
      </section>
    </div>
  );
}

function AgentRow({
  agent,
  selected,
  onClick,
}: {
  agent: AgentListItem;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button className={`list-row text-left ${selected ? "selected" : ""}`} onClick={onClick}>
      <div className="flex items-center justify-between gap-2">
        <span className="truncate font-medium">{agent.name}</span>
        <EnabledBadge enabled={agent.enabled} />
      </div>
      <div className="mt-2 flex items-center gap-2 text-xs text-muted">
        <span className="truncate">{agent.model}</span>
        <span>{agent.has_spec ? "спека" : "класс"}</span>
      </div>
    </button>
  );
}
