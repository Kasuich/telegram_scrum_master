import { useEffect, useState } from "react";

import { api, type Pet, type Shop, type User } from "../../lib/api";
import { PixelSprite } from "../../lib/scrumiks/PixelSprite";
import { haptic, impact } from "../telegram";

const RARITY_COLOR: Record<string, string> = {
  common: "#9ca3af",
  uncommon: "#22c55e",
  rare: "#3b82f6",
  epic: "#a855f7",
  legendary: "#f59e0b",
};

export function TgPetScreen({ me }: { me: User }) {
  const [pet, setPet] = useState<Pet | null>(null);
  const [shop, setShop] = useState<Shop | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = () => {
    api.myPet().then(setPet).catch((e: Error) => setError(e.message));
    api.petShop().then(setShop).catch(() => undefined);
  };
  useEffect(reload, []);

  if (error) return <div className="tg-screen"><div className="tg-card tg-error">{error}</div></div>;
  if (!pet || !pet.available) {
    return (
      <div className="tg-screen">
        <h1 className="tg-h1">Скрамик</h1>
        <div className="tg-card tg-muted">{pet?.note ?? "Загрузка питомца…"}</div>
      </div>
    );
  }

  const buy = async (itemId: string) => {
    setBusy(itemId);
    try {
      await api.petBuy(itemId);
      impact("light");
      haptic("success");
      reload();
    } catch (e) {
      haptic("error");
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  const equip = async (slot: string, itemId: string, equipped: boolean) => {
    setBusy(itemId);
    try {
      await api.petEquip(slot, equipped ? null : itemId);
      impact("light");
      reload();
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="tg-screen">
      <h1 className="tg-h1">{me.display_name}</h1>
      <p className="tg-sub">Ваш скрамик растёт с каждой закрытой задачей</p>

      <div className="tg-card" style={{ textAlign: "center" }}>
        <PixelSprite speciesId={pet.species?.id} size={140} equipped={pet.equipped} />
        <div className="tg-row-between" style={{ marginTop: 12 }}>
          <span className="tg-name">{pet.species?.name}</span>
          <span
            className="tg-pill"
            style={{ color: RARITY_COLOR[pet.species?.rarity ?? "common"] }}
          >
            {pet.tier_name} · ур. {pet.level}
          </span>
        </div>
        <div className="tg-stat" style={{ marginTop: 10 }}>
          <div className="tg-stat-label">
            <span>XP {pet.xp_into_level}/{pet.xp_for_next}</span>
            <span>🪙 {pet.coins}</span>
          </div>
          <div className="tg-bar"><span style={{ width: `${Math.round(pet.progress * 100)}%` }} /></div>
        </div>
        <div className="tg-stat">
          <div className="tg-stat-label"><span>Настроение</span><span>{pet.mood}%</span></div>
          <div className="tg-bar">
            <span style={{ width: `${pet.mood}%`, background: "var(--tg-accent-2)" }} />
          </div>
        </div>
      </div>

      <div className="tg-card">
        <div className="tg-name" style={{ marginBottom: 8 }}>Характеристики</div>
        {Object.entries(pet.stats).map(([key, value]) => (
          <div className="tg-stat" key={key}>
            <div className="tg-stat-label">
              <span>{pet.stat_labels[key] ?? key}</span>
              <span>{value}</span>
            </div>
            <div className="tg-bar"><span style={{ width: `${value}%` }} /></div>
          </div>
        ))}
      </div>

      {shop && (
        <div className="tg-card">
          <div className="tg-row-between" style={{ marginBottom: 10 }}>
            <span className="tg-name">Магазин</span>
            <span className="tg-pill">🪙 {shop.coins}</span>
          </div>
          {shop.items.map((item) => (
            <div className="tg-list-item" key={item.id}>
              <PixelSprite speciesId={pet.species?.id} size={36} equipped={{ [item.slot]: item.id }} />
              <div style={{ flex: 1 }}>
                <div className="tg-name" style={{ fontSize: 14 }}>{item.name}</div>
                <div className="tg-small tg-muted">🪙 {item.price}</div>
              </div>
              {item.owned ? (
                <button
                  className="tg-btn small secondary"
                  disabled={busy === item.id}
                  onClick={() => equip(item.slot, item.id, item.equipped)}
                >
                  {item.equipped ? "Снять" : "Надеть"}
                </button>
              ) : (
                <button
                  className="tg-btn small"
                  disabled={busy === item.id || !item.affordable}
                  onClick={() => buy(item.id)}
                >
                  {item.affordable ? "Купить" : "Дорого"}
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      <button className="tg-btn secondary" onClick={() => { window.location.href = "/profile"; }}>
        Открыть профиль в полном интерфейсе →
      </button>
    </div>
  );
}
