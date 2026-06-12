import { useEffect, useMemo, useState } from "react";

import { api, type User } from "../lib/api";
import { initData, tgReady } from "./telegram";
import { TgPetScreen } from "./screens/TgPetScreen";
import { TgBattleScreen } from "./screens/TgBattleScreen";
import { TgTeamScreen } from "./screens/TgTeamScreen";
import { TgMoreScreen } from "./screens/TgMoreScreen";
import "./tg.css";

type Tab = "pet" | "battle" | "team" | "more";
type AuthState = "loading" | "ready" | "error" | "no-tg";

interface TabDef {
  id: Tab;
  label: string;
  icon: string;
  minTeamlead?: boolean;
}

const TABS: TabDef[] = [
  { id: "pet", label: "Скрамик", icon: "🐾" },
  { id: "battle", label: "Битва", icon: "⚔️" },
  { id: "team", label: "Команда", icon: "📊", minTeamlead: true },
  { id: "more", label: "Ещё", icon: "⋯" },
];

export function TgApp() {
  const [auth, setAuth] = useState<AuthState>("loading");
  const [me, setMe] = useState<User | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("pet");

  useEffect(() => {
    tgReady();
    const data = initData();
    if (!data) {
      setAuth("no-tg");
      return;
    }
    api
      .authTelegramWebApp(data)
      .then(({ user }) => {
        setMe(user);
        setAuth("ready");
      })
      .catch((e: Error) => {
        setError(e.message);
        setAuth("error");
      });
  }, []);

  const tabs = useMemo(() => {
    const isLead = me?.ui_role === "teamlead" || me?.ui_role === "developer";
    return TABS.filter((t) => !t.minTeamlead || isLead);
  }, [me]);

  if (auth === "loading") {
    return (
      <div className="tg-root">
        <div className="tg-center">
          <div className="tg-spin" />
          <p style={{ marginTop: 14 }}>Загружаем скрамиков…</p>
        </div>
      </div>
    );
  }

  if (auth === "no-tg") {
    return (
      <div className="tg-root">
        <div className="tg-center">
          <div style={{ fontSize: 48 }}>🤖</div>
          <p className="tg-name" style={{ marginTop: 10 }}>Откройте приложение из Telegram</p>
          <p className="tg-small" style={{ marginTop: 6 }}>
            Нажмите кнопку «🎮 Скрамики» в меню бота.
          </p>
        </div>
      </div>
    );
  }

  if (auth === "error" || !me) {
    return (
      <div className="tg-root">
        <div className="tg-center">
          <div style={{ fontSize: 48 }}>🔒</div>
          <p className="tg-name tg-error" style={{ marginTop: 10 }}>Не удалось войти</p>
          <p className="tg-small" style={{ marginTop: 6 }}>{error}</p>
          <p className="tg-small" style={{ marginTop: 10 }}>
            Сначала пройдите онбординг у бота: отправьте ему <b>/start</b>.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="tg-root">
      {tab === "pet" && <TgPetScreen me={me} />}
      {tab === "battle" && <TgBattleScreen me={me} />}
      {tab === "team" && <TgTeamScreen me={me} />}
      {tab === "more" && <TgMoreScreen me={me} />}

      <nav className="tg-nav">
        {tabs.map((t) => (
          <button
            key={t.id}
            className={tab === t.id ? "active" : ""}
            onClick={() => setTab(t.id)}
          >
            <span className="ico">{t.icon}</span>
            {t.label}
          </button>
        ))}
      </nav>
    </div>
  );
}
