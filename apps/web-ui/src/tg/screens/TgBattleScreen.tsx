import { useEffect, useState } from "react";

import {
  api,
  type BattleCombatant,
  type BattleRoyale,
  type Duel,
  type DuelRow,
  type User,
} from "../../lib/api";
import { PixelSprite } from "../../lib/scrumiks/PixelSprite";
import { haptic, impact } from "../telegram";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

type View = "menu" | "running" | "royale" | "duel";

export function TgBattleScreen({ me }: { me: User }) {
  const [board, setBoard] = useState<BattleCombatant[]>([]);
  const [duels, setDuels] = useState<DuelRow[]>([]);
  const [view, setView] = useState<View>("menu");
  const [status, setStatus] = useState<string>("");
  const [royale, setRoyale] = useState<BattleRoyale | null>(null);
  const [duel, setDuel] = useState<Duel | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = () => {
    api.battleLeaderboard().then(setBoard).catch(() => undefined);
    api.duelLeaderboard().then(setDuels).catch(() => undefined);
  };
  useEffect(reload, []);

  const playFrames = async (frames: string[]) => {
    for (const f of frames) {
      setStatus(f);
      await sleep(850);
    }
  };

  const runTeam = async () => {
    setView("running");
    setError(null);
    setStatus("⚔️ Скрамики выходят на арену…");
    impact("medium");
    try {
      const res = await api.battleTeam();
      await playFrames(res.status_frames);
      setRoyale(res);
      setView("royale");
      haptic("success");
      reload();
    } catch (e) {
      setError((e as Error).message);
      setView("menu");
      haptic("error");
    }
  };

  const runDuel = async (opponent: BattleCombatant) => {
    if (!opponent.user_id) return;
    setView("running");
    setError(null);
    setStatus(`⚔️ Вызов: ${opponent.name}…`);
    impact("medium");
    try {
      const res = await api.battleDuel(opponent.user_id);
      await playFrames(res.status_frames);
      setDuel(res);
      setView("duel");
      haptic("success");
      reload();
    } catch (e) {
      setError((e as Error).message);
      setView("menu");
      haptic("error");
    }
  };

  if (view === "running") {
    return (
      <div className="tg-screen">
        <h1 className="tg-h1">Магическая битва</h1>
        <div className="tg-card">
          <div className="tg-spin" />
          <div className="tg-status">{status}</div>
        </div>
      </div>
    );
  }

  if (view === "royale" && royale) {
    return (
      <div className="tg-screen">
        <h1 className="tg-h1">🏆 {royale.team_name}</h1>
        <p className="tg-sub">Результат турнира — картинка ушла и в чат</p>
        <img className="tg-battle-img" src={`data:image/png;base64,${royale.image_base64}`} alt="leaderboard" />
        <button className="tg-btn" style={{ marginTop: 14 }} onClick={() => setView("menu")}>
          ← Назад к арене
        </button>
        <button className="tg-btn gold" style={{ marginTop: 10 }} onClick={runTeam}>
          🔄 Реванш
        </button>
      </div>
    );
  }

  if (view === "duel" && duel) {
    return (
      <div className="tg-screen">
        <h1 className="tg-h1">⚔️ Дуэль</h1>
        <img className="tg-battle-img" src={`data:image/png;base64,${duel.image_base64}`} alt="duel" />
        <div className="tg-card" style={{ marginTop: 14 }}>
          <div className="tg-log">
            {duel.log.map((line, i) => (
              <div key={i}>{line}</div>
            ))}
          </div>
        </div>
        <button className="tg-btn" onClick={() => setView("menu")}>← Назад к арене</button>
      </div>
    );
  }

  const opponents = board.filter((c) => c.user_id && c.user_id !== me.id);

  return (
    <div className="tg-screen">
      <h1 className="tg-h1">⚔️ Арена скрамиков</h1>
      <p className="tg-sub">Уровень и аксессуары дают преимущество. Магия + рандом решают исход.</p>

      {error && <div className="tg-card tg-error">{error}</div>}

      <button className="tg-btn gold" onClick={runTeam}>⚔️ Битва всей команды</button>

      <div className="tg-card" style={{ marginTop: 14 }}>
        <div className="tg-name" style={{ marginBottom: 8 }}>Рейтинг силы</div>
        {board.length === 0 && <div className="tg-muted tg-small">Пока некому сражаться</div>}
        {board.slice(0, 10).map((c) => (
          <div className="tg-list-item" key={c.user_id ?? c.name}>
            <span className={`tg-rank ${c.rank === 1 ? "g1" : c.rank === 2 ? "g2" : c.rank === 3 ? "g3" : ""}`}>
              {c.rank}
            </span>
            <PixelSprite speciesId={c.species_id} size={32} equipped={c.equipped} />
            <div style={{ flex: 1 }}>
              <div className="tg-name" style={{ fontSize: 14 }}>{c.name}</div>
              <div className="tg-small tg-muted">{c.species_name} · ур. {c.level}</div>
            </div>
            <span className="tg-pill">⚡{c.power}</span>
          </div>
        ))}
      </div>

      <div className="tg-card">
        <div className="tg-name" style={{ marginBottom: 8 }}>Вызвать на дуэль</div>
        {opponents.length === 0 && <div className="tg-muted tg-small">Нет соперников</div>}
        {opponents.map((c) => (
          <div className="tg-list-item" key={c.user_id}>
            <PixelSprite speciesId={c.species_id} size={32} equipped={c.equipped} />
            <div style={{ flex: 1 }}>
              <div className="tg-name" style={{ fontSize: 14 }}>{c.name}</div>
              <div className="tg-small tg-muted">{c.species_name} · ур. {c.level}</div>
            </div>
            <button className="tg-btn small" onClick={() => runDuel(c)}>Вызвать</button>
          </div>
        ))}
      </div>

      {duels.length > 0 && (
        <div className="tg-card">
          <div className="tg-name" style={{ marginBottom: 8 }}>Лидерборд дуэлей</div>
          {duels.map((d, i) => (
            <div className="tg-list-item" key={d.user_id}>
              <span className={`tg-rank ${i === 0 ? "g1" : i === 1 ? "g2" : i === 2 ? "g3" : ""}`}>{i + 1}</span>
              <div style={{ flex: 1 }}>
                <div className="tg-name" style={{ fontSize: 14 }}>{d.name}</div>
                <div className="tg-small tg-muted">{d.battles} боёв</div>
              </div>
              <span className="tg-pill">🏆 {d.wins} · 💀 {d.losses}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
