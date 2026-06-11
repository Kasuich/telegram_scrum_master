import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles, Trophy } from "lucide-react";
import { useState } from "react";

import { api, type Pet, type ShopItem } from "../lib/api";
import { PixelSprite } from "../lib/scrumiks/PixelSprite";
import { RARITY, SCRUMIK_LIST } from "../lib/scrumiks/sprites";

const ACHIEVEMENTS = ["Первая закрытая задача", "Неделя без просрочек", "10 задач за спринт", "Чистая доска"];
const STAT_ORDER = ["velocity", "focus", "reliability", "stamina"];
const STAT_COLOR: Record<string, string> = {
  velocity: "#6366f1",
  focus: "#0ea5e9",
  reliability: "#0d9488",
  stamina: "#f59e0b",
};

export function PetPage() {
  const pet = useQuery({ queryKey: ["my-pet"], queryFn: api.myPet });
  const me = useQuery({ queryKey: ["me"], queryFn: api.me });
  const isDev = me.data?.ui_role === "developer";

  return (
    <div className="board-page">
      <section className="surface wide">
        <div className="section-head">
          <div>
            <h2>Скрамик</h2>
            <p>виртуальный питомец команды</p>
          </div>
        </div>
        {pet.isLoading ? (
          <div className="empty">Загрузка</div>
        ) : pet.data?.available ? (
          <PetView pet={pet.data} />
        ) : (
          <div className="empty">{pet.data?.note ?? "Питомец пока недоступен"}</div>
        )}
      </section>

      {pet.data?.available && <StatsSection pet={pet.data} />}

      {pet.data?.available && <ShopSection />}

      {isDev && <DevPanel />}

      <section className="surface wide">
        <div className="section-head">
          <div>
            <h2>Ачивки</h2>
            <p>скоро здесь появятся награды</p>
          </div>
          <Trophy className="h-5 w-5 text-muted" />
        </div>
        <div className="tile-grid">
          {ACHIEVEMENTS.map((title) => (
            <div className="tile soon" key={title}>
              <span className="tile-title">{title}</span>
              <span className="tile-note">Скоро</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function RarityChip({ rarity }: { rarity: keyof typeof RARITY }) {
  const r = RARITY[rarity];
  return (
    <span className="pet-rarity" style={{ background: r.color }}>
      {r.label}
    </span>
  );
}

function PetView({ pet }: { pet: Pet }) {
  const species = pet.species;
  return (
    <div className="pet-view">
      <div className="pet-avatar">
        <PixelSprite speciesId={species?.id} size={128} equipped={pet.equipped} />
        <span className="pet-tier">{pet.tier_name}</span>
      </div>
      <div className="pet-stats">
        <div className="pet-species-head">
          <strong className="pet-species-name">{species?.name ?? "Скрамик"}</strong>
          {species && <RarityChip rarity={species.rarity} />}
          <span className="pet-level">
            Уровень <strong>{pet.level}</strong>
          </span>
          <span className="pet-coins">{pet.coins} 🪙</span>
        </div>
        {species?.desc && <p className="pet-desc">«{species.desc}»</p>}
        <div className="pet-bar-row">
          <span className="pet-bar-label">
            <Sparkles className="h-3.5 w-3.5" /> Опыт
          </span>
          <div className="pet-bar">
            <div className="pet-bar-fill xp" style={{ width: `${Math.round(pet.progress * 100)}%` }} />
          </div>
          <span className="pet-bar-value">
            {pet.xp_into_level}/{pet.xp_for_next}
          </span>
        </div>
        <div className="pet-bar-row">
          <span className="pet-bar-label">Настроение</span>
          <div className="pet-bar">
            <div className="pet-bar-fill mood" style={{ width: `${pet.mood}%` }} />
          </div>
          <span className="pet-bar-value">{pet.mood}</span>
        </div>
      </div>
    </div>
  );
}

function StatsSection({ pet }: { pet: Pet }) {
  const labels = pet.stat_labels ?? {};
  return (
    <section className="surface wide">
      <div className="section-head">
        <div>
          <h2>Показатели</h2>
          <p>растут с уровнем и отражают твою работу на доске</p>
        </div>
      </div>
      <div className="pet-stats">
        {STAT_ORDER.filter((key) => key in pet.stats).map((key) => (
          <div className="pet-bar-row" key={key}>
            <span className="pet-bar-label">{labels[key] ?? key}</span>
            <div className="pet-bar">
              <div
                className="pet-bar-fill"
                style={{ width: `${pet.stats[key]}%`, background: STAT_COLOR[key] ?? "#6366f1" }}
              />
            </div>
            <span className="pet-bar-value">{pet.stats[key]}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

function ShopSection() {
  const qc = useQueryClient();
  const shop = useQuery({ queryKey: ["pet-shop"], queryFn: api.petShop });
  const [error, setError] = useState<string | null>(null);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["pet-shop"] });
    qc.invalidateQueries({ queryKey: ["my-pet"] });
  };
  const onError = (e: Error) => setError(e.message);
  const buy = useMutation({ mutationFn: (id: string) => api.petBuy(id), onSuccess: invalidate, onError });
  const equip = useMutation({
    mutationFn: ({ slot, id }: { slot: string; id: string | null }) => api.petEquip(slot, id),
    onSuccess: invalidate,
    onError,
  });

  if (!shop.data) return null;

  return (
    <section className="surface wide">
      <div className="section-head">
        <div>
          <h2>Магазин</h2>
          <p>скрамкоины капают за закрытые задачи</p>
        </div>
        <span className="pet-coins pet-coins-lg">{shop.data.coins} 🪙</span>
      </div>
      {error && <p className="pet-dev-error">{error}</p>}
      <div className="shop-grid">
        {shop.data.items.map((item) => (
          <ShopCard
            key={item.id}
            item={item}
            busy={buy.isPending || equip.isPending}
            onBuy={() => { setError(null); buy.mutate(item.id); }}
            onEquip={() => { setError(null); equip.mutate({ slot: item.slot, id: item.equipped ? null : item.id }); }}
          />
        ))}
      </div>
    </section>
  );
}

function ShopCard({
  item,
  busy,
  onBuy,
  onEquip,
}: {
  item: ShopItem;
  busy: boolean;
  onBuy: () => void;
  onEquip: () => void;
}) {
  const previewSpecies = SCRUMIK_LIST[0].id;
  return (
    <div className={`shop-card${item.equipped ? " equipped" : ""}`}>
      <div className="shop-preview">
        <PixelSprite speciesId={previewSpecies} size={72} equipped={{ [item.slot]: item.id }} />
      </div>
      <div className="shop-card-name">{item.name}</div>
      <div className="shop-card-meta">
        <RarityChip rarity={item.rarity} />
        <span className="pet-coins">{item.price} 🪙</span>
      </div>
      {item.owned ? (
        <button className="secondary-button" disabled={busy} onClick={onEquip}>
          {item.equipped ? "Снять" : "Надеть"}
        </button>
      ) : (
        <button className="primary-button" disabled={busy || !item.affordable} onClick={onBuy}>
          {item.affordable ? "Купить" : "Не хватает"}
        </button>
      )}
    </div>
  );
}

function DevPanel() {
  const qc = useQueryClient();
  const [level, setLevel] = useState(5);
  const [species, setSpecies] = useState(SCRUMIK_LIST[0].id);
  const [error, setError] = useState<string | null>(null);

  const refresh = () => qc.invalidateQueries({ queryKey: ["my-pet"] });
  const run = (p: Promise<unknown>) => {
    setError(null);
    p.then(refresh).catch((e: Error) => setError(e.message));
  };

  const grantXp = useMutation({ mutationFn: (amount: number) => api.petGrantXp({ amount }) });
  const setLvl = useMutation({ mutationFn: (lvl: number) => api.petGrantXp({ level: lvl }) });
  const setSp = useMutation({ mutationFn: (id: string) => api.petSetSpecies(id) });
  const reset = useMutation({ mutationFn: () => api.petReset() });

  return (
    <section className="surface wide pet-dev">
      <div className="section-head">
        <div>
          <h2>Dev-панель</h2>
          <p>быстрый прогон уровней и видов (PET_DEV_TOOLS)</p>
        </div>
      </div>
      <div className="pet-dev-row">
        <button className="secondary-button" onClick={() => run(grantXp.mutateAsync(100))}>+100 XP</button>
        <button className="secondary-button" onClick={() => run(grantXp.mutateAsync(500))}>+500 XP</button>
        <span className="pet-dev-group">
          <input
            type="number"
            min={1}
            value={level}
            onChange={(e) => setLevel(Number(e.target.value))}
            className="pet-dev-input"
          />
          <button className="secondary-button" onClick={() => run(setLvl.mutateAsync(level))}>До уровня</button>
        </span>
        <span className="pet-dev-group">
          <select value={species} onChange={(e) => setSpecies(e.target.value)} className="pet-dev-input">
            {SCRUMIK_LIST.map((s) => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </select>
          <button className="secondary-button" onClick={() => run(setSp.mutateAsync(species))}>Примерить вид</button>
        </span>
        <button className="secondary-button" onClick={() => run(reset.mutateAsync())}>Сброс</button>
      </div>
      {error && <p className="pet-dev-error">Недоступно: {error}</p>}
    </section>
  );
}
