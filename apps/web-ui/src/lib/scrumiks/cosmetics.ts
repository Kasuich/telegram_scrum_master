// Скрамики — украшения (косметика). Прозрачные пиксельные оверлеи 16×16,
// рисуются поверх спрайта вида. Метаданные (цена/редкость) дублируют бэкенд
// (packages/core/src/core/pet.py COSMETICS); сетки/палитры — только тут.

import type { Rarity } from "./sprites";

export type Slot = "head" | "eyes" | "neck" | "hand" | "aura" | "background";

export interface Cosmetic {
  id: string;
  name: string;
  slot: Slot;
  rarity: Rarity;
  price: number;
  opal: Record<string, string>;
  overlay: string[];
}

const DOTS = "................";
const d = (n: number): string[] => Array.from({ length: n }, () => DOTS);

const LIST: Cosmetic[] = [
  {
    id: "cap_red", name: "Кепка", slot: "head", rarity: "common", price: 60,
    opal: { C: "#e23b3b", k: "#a31f1f" },
    overlay: ["................", "................", ".....CCCCCC.....", "....CkkkkkkC....", "...........CCC..", ...d(11)],
  },
  {
    id: "tophat", name: "Цилиндр", slot: "head", rarity: "rare", price: 220,
    opal: { K: "#15171f", r: "#e23b3b" },
    overlay: [".....KKKKKK.....", ".....KrrrrK.....", "....KKKKKKKK....", ...d(13)],
  },
  {
    id: "crown", name: "Корона", slot: "head", rarity: "legendary", price: 900,
    opal: { G: "#ffd24d", o: "#b8860b" },
    overlay: ["................", "....G.G.G.G.....", "....GGGGGGG.....", ...d(13)],
  },
  {
    id: "shades", name: "Очки «deal-with-it»", slot: "eyes", rarity: "uncommon", price: 120,
    opal: { K: "#15171f", w: "#ffffff" },
    overlay: [...d(8), ".....KKKKKK.....", ...d(7)],
  },
  {
    id: "chain_gold", name: "Золотая цепь", slot: "neck", rarity: "epic", price: 400,
    opal: { G: "#ffd24d" },
    overlay: [...d(11), "....G......G....", ".....G....G.....", "......GGGG......", ...d(2)],
  },
  {
    id: "tie", name: "Галстук", slot: "neck", rarity: "common", price: 50,
    opal: { T: "#c62f3a" },
    overlay: [...d(10), ".......TT.......", ".......TT.......", ".......TT.......", ...d(3)],
  },
  {
    id: "coffee", name: "Кружка кофе", slot: "hand", rarity: "common", price: 70,
    opal: { M: "#e6e2d6", c: "#6f4a2f" },
    overlay: [...d(9), "...........MMM..", "...........McM..", "...........MMM..", ...d(4)],
  },
  {
    id: "aura_spark", name: "Аура искр", slot: "aura", rarity: "epic", price: 500,
    opal: { "*": "#fff7c2" },
    overlay: ["................", "..*..........*..", ...d(4), "*..............*", ...d(4), "..*..........*..", ...d(4)],
  },
];

export const COSMETICS: Record<string, Cosmetic> = Object.fromEntries(LIST.map((c) => [c.id, c]));
export const COSMETIC_LIST = LIST;

// Слой украшений рисуется в этом порядке (фон/аура → тело → одежда → лицо/голова).
export const SLOT_Z: Slot[] = ["background", "aura", "hand", "neck", "eyes", "head"];

/** Resolve equipped {slot: itemId} into an ordered list of cosmetics to layer. */
export function equippedOverlays(equipped: Record<string, string> | undefined): Cosmetic[] {
  if (!equipped) return [];
  return SLOT_Z.map((slot) => equipped[slot])
    .filter((id): id is string => Boolean(id) && id in COSMETICS)
    .map((id) => COSMETICS[id]);
}
