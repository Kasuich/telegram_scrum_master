import { useEffect, useState } from "react";

import { api, type TeamHealth, type User } from "../../lib/api";

function healthColor(score: number): string {
  if (score >= 75) return "#56cc82";
  if (score >= 50) return "#f5b301";
  return "#ff7a7a";
}

export function TgTeamScreen({ me }: { me: User }) {
  const [health, setHealth] = useState<TeamHealth | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!me.team_id) {
      setError("Команда не привязана");
      return;
    }
    api.teamHealth(me.team_id).then(setHealth).catch((e: Error) => setError(e.message));
  }, [me.team_id]);

  return (
    <div className="tg-screen">
      <h1 className="tg-h1">📊 Команда</h1>
      <p className="tg-sub">Сводка здоровья · подробности и графики — в полном UI</p>

      {error && <div className="tg-card tg-error">{error}</div>}

      {health && !health.available && (
        <div className="tg-card tg-muted">{health.note ?? "Данные недоступны"}</div>
      )}

      {health && health.available && (
        <>
          <div className="tg-card" style={{ textAlign: "center" }}>
            <div className="tg-small tg-muted">Индекс здоровья</div>
            <div
              style={{
                fontSize: 46,
                fontWeight: 800,
                color: healthColor(health.health_index ?? 0),
              }}
            >
              {Math.round(health.health_index ?? 0)}
            </div>
            <div className="tg-small tg-muted">за {health.window_days} дн.</div>
          </div>

          <div className="tg-card">
            <div className="tg-name" style={{ marginBottom: 8 }}>Составляющие</div>
            {health.breakdown.map((b) => (
              <div className="tg-stat" key={b.key}>
                <div className="tg-stat-label">
                  <span>{b.label}</span>
                  <span>{Math.round(b.score)}</span>
                </div>
                <div className="tg-bar">
                  <span style={{ width: `${Math.round(b.score)}%`, background: healthColor(b.score) }} />
                </div>
              </div>
            ))}
          </div>

          <div className="tg-card">
            <div className="tg-name" style={{ marginBottom: 8 }}>Участники</div>
            {health.members.map((m) => (
              <div className="tg-list-item" key={m.user_id ?? m.display_name}>
                <div style={{ flex: 1 }}>
                  <div className="tg-name" style={{ fontSize: 14 }}>{m.display_name ?? "—"}</div>
                  <div className="tg-small tg-muted">
                    в работе {m.in_progress} · закрыто {m.resolved}
                  </div>
                </div>
                {m.overdue > 0 && <span className="tg-pill" style={{ color: "#ff7a7a" }}>⏰ {m.overdue}</span>}
              </div>
            ))}
          </div>
        </>
      )}

      <button className="tg-btn" onClick={() => { window.location.href = "/team"; }}>
        🔍 Аудит и графики в полном UI →
      </button>
    </div>
  );
}
