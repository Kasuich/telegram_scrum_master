import { useQuery } from "@tanstack/react-query";
import { Sparkles, Trophy } from "lucide-react";

import { api, type Pet } from "../lib/api";

const ACHIEVEMENTS = ["Первая закрытая задача", "Неделя без просрочек", "10 задач за спринт", "Чистая доска"];

function moodFace(mood: number): string {
  if (mood >= 80) return "😺";
  if (mood >= 55) return "🙂";
  if (mood >= 30) return "😟";
  return "😿";
}

export function PetPage() {
  const pet = useQuery({ queryKey: ["my-pet"], queryFn: api.myPet });

  return (
    <div className="board-page">
      <section className="surface wide">
        <div className="section-head">
          <div>
            <h2>Скрамик</h2>
            <p>виртуальный питомец команды</p>
          </div>
          <span className="soon-badge">Скоро</span>
        </div>
        {pet.isLoading ? (
          <div className="empty">Загрузка</div>
        ) : pet.data?.available ? (
          <PetView pet={pet.data} />
        ) : (
          <div className="empty">{pet.data?.note ?? "Питомец пока недоступен"}</div>
        )}
      </section>

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

function PetView({ pet }: { pet: Pet }) {
  return (
    <div className="pet-view">
      <div className="pet-avatar">
        <span className="pet-face">{moodFace(pet.mood)}</span>
        <span className="pet-tier">{pet.tier_name}</span>
      </div>
      <div className="pet-stats">
        <div className="pet-level">
          Уровень <strong>{pet.level}</strong>
        </div>
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
        <p className="text-xs text-muted">
          Скрамик растёт от закрытых задач и расстраивается из-за просрочек. Полная механика — скоро.
        </p>
      </div>
    </div>
  );
}
