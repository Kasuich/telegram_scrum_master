import { useEffect, useState } from "react";

import { api, type AgentListItem, type Stats, type User } from "../../lib/api";
import { agentLogo } from "../../lib/agentLogos";

export function TgMoreScreen({ me }: { me: User }) {
  const [stats, setStats] = useState<Stats | null>(null);
  const [agents, setAgents] = useState<AgentListItem[]>([]);
  const isLead = me.ui_role === "teamlead" || me.ui_role === "developer";

  useEffect(() => {
    api.myStats().then(setStats).catch(() => undefined);
    api.agents().then(setAgents).catch(() => undefined);
  }, []);

  const counts = stats?.counts ?? {};

  return (
    <div className="tg-screen">
      <h1 className="tg-h1">Ещё</h1>
      <p className="tg-sub">{me.display_name} · {roleLabel(me.ui_role)}</p>

      <div className="tg-card">
        <div className="tg-name" style={{ marginBottom: 10 }}>Моя доска</div>
        <div className="tg-row" style={{ flexWrap: "wrap", gap: 8 }}>
          <span className="tg-pill">📥 назначено {counts.assigned ?? 0}</span>
          <span className="tg-pill">⚙️ в работе {counts.in_progress ?? 0}</span>
          <span className="tg-pill">✅ закрыто {counts.resolved ?? 0}</span>
          {(counts.overdue ?? 0) > 0 && (
            <span className="tg-pill" style={{ color: "#ff7a7a" }}>⏰ просрочено {counts.overdue}</span>
          )}
        </div>
        <button className="tg-btn secondary" style={{ marginTop: 12 }} onClick={() => { window.location.href = "/board"; }}>
          Открыть доску →
        </button>
      </div>

      {agents.length > 0 && (
        <div className="tg-card">
          <div className="tg-name" style={{ marginBottom: 8 }}>AI-агенты команды</div>
          {agents.map((a) => (
            <div className="tg-list-item" key={a.name}>
              <img src={agentLogo(a.name)} alt={a.name} width={36} height={36} style={{ borderRadius: 10 }} />
              <div style={{ flex: 1 }}>
                <div className="tg-name" style={{ fontSize: 14 }}>{a.description || a.name}</div>
                <div className="tg-small tg-muted">{a.enabled ? "включён" : "выключен"}</div>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="tg-card">
        <div className="tg-name" style={{ marginBottom: 10 }}>Полный интерфейс</div>
        <button className="tg-btn secondary" onClick={() => { window.location.href = "/profile"; }}>👤 Профиль</button>
        <div style={{ height: 8 }} />
        <button className="tg-btn secondary" onClick={() => { window.location.href = "/board"; }}>📋 Моя доска</button>
        {isLead && (
          <>
            <div style={{ height: 8 }} />
            <button className="tg-btn secondary" onClick={() => { window.location.href = "/team"; }}>📊 Команда</button>
          </>
        )}
        <div style={{ height: 8 }} />
        <button className="tg-btn" onClick={() => { window.location.href = "/"; }}>Открыть полный UI →</button>
      </div>
    </div>
  );
}

function roleLabel(role: string): string {
  if (role === "developer") return "разработчик";
  if (role === "teamlead") return "тимлид";
  return "участник";
}
