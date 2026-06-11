import { Sparkles } from "lucide-react";

import type { UiRole } from "../lib/api";

const ROLE_LABEL: Record<UiRole, string> = {
  developer: "разработчик",
  teamlead: "тимлид",
  user: "пользователь",
};

type Tile = { title: string; note: string; roles: UiRole[] };

const TILES: Tile[] = [
  { title: "Моя доска", note: "Скоро", roles: ["user", "teamlead", "developer"] },
  { title: "Моя статистика", note: "Скоро", roles: ["user", "teamlead", "developer"] },
  { title: "Скрамик", note: "Скоро", roles: ["user", "teamlead", "developer"] },
  { title: "Ачивки", note: "Скоро", roles: ["user", "teamlead", "developer"] },
  { title: "Здоровье команды", note: "Скоро", roles: ["teamlead", "developer"] },
  { title: "Кроны команды", note: "Скоро", roles: ["teamlead", "developer"] },
];

export function HomePage({ role, name }: { role: UiRole; name: string }) {
  const tiles = TILES.filter((tile) => tile.roles.includes(role));

  return (
    <div className="page-grid">
      <section className="surface wide">
        <div className="section-head">
          <div>
            <h2>Привет, {name}</h2>
            <p>Роль: {ROLE_LABEL[role]}</p>
          </div>
          <Sparkles className="h-5 w-5 text-muted" />
        </div>
        <div className="tile-grid">
          {tiles.map((tile) => (
            <div className="tile soon" key={tile.title}>
              <span className="tile-title">{tile.title}</span>
              <span className="tile-note">{tile.note}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
