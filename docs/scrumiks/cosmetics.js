/*
 * Скрамики — украшения (косметика). Прозрачные пиксельные оверлеи 16×16,
 * рисуются ПОВЕРХ спрайта вида (и поверх его фичи-оверлея).
 *
 * slot — куда надевается (один предмет на слот). price — в скрамкоинах.
 * Ключи объявлены в opal каждого предмета.
 */

const DOTS = "................";
const d = (n) => Array.from({ length: n }, () => DOTS);

const COSMETICS = [
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

// Нейтральный демо-блоб для превью предметов (вне контекста конкретного вида).
const DEMO_COLORS = { o: "#2a2f45", g: "#aab4e8", d: "#7e8bd0", h: "#cdd5f5" };

const RARITY = {
  common: { label: "Common", color: "#9ca3af" },
  uncommon: { label: "Uncommon", color: "#22c55e" },
  rare: { label: "Rare", color: "#3b82f6" },
  epic: { label: "Epic", color: "#a855f7" },
  legendary: { label: "Legendary", color: "#f59e0b" },
};

if (typeof window !== "undefined") {
  window.COSMETICS = COSMETICS;
  window.COSMETIC_DEMO = DEMO_COLORS;
  window.COSMETIC_RARITY = RARITY;
}
if (typeof module !== "undefined") {
  module.exports = { COSMETICS, DEMO_COLORS, RARITY };
}
