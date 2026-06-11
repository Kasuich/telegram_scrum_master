// Скрамики — пиксель-спрайты (16×16, единый blob-силуэт + per-species фичи).
// Источник правды для фронта. Бэкенд-каталог (id/rarity/desc) — packages/core/src/core/pet.py.
// Стиль: чанковый пиксель-арт «buddy». Ключи базы: o контур, g тело, d тень, h блик,
// W белок, P зрачок(=контур). Оверлей объявляет свои ключи в opal.

export type Rarity = "common" | "uncommon" | "rare" | "epic" | "legendary";

export interface Sprite {
  id: string;
  name: string;
  rarity: Rarity;
  colors: { o: string; g: string; d: string; h: string };
  opal: Record<string, string>;
  overlay: string[] | null;
}

// Общий силуэт. Все строки ровно 16 символов.
export const SCRUMIK_BASE: string[] = [
  "................",
  "................",
  "......oooo......",
  ".....oggggo.....",
  "....ohggggo.....",
  "...ohgggggggo...",
  "...oggggggggo...",
  "..oggggggggggo..",
  "..oggWPggWPggo..",
  "..oggPPggPPggo..",
  "..oggggggggggo..",
  "...oggggggggo...",
  "...oggggggddo...",
  "....oggggddo....",
  ".....oooooo.....",
  "................",
];

const DOTS = "................";
const dots = (n: number): string[] => Array.from({ length: n }, () => DOTS);

export const RARITY: Record<Rarity, { label: string; color: string }> = {
  common: { label: "Common", color: "#9ca3af" },
  uncommon: { label: "Uncommon", color: "#22c55e" },
  rare: { label: "Rare", color: "#3b82f6" },
  epic: { label: "Epic", color: "#a855f7" },
  legendary: { label: "Legendary", color: "#f59e0b" },
};

const LIST: Sprite[] = [
  {
    id: "standupik", name: "Стендапик", rarity: "common",
    colors: { o: "#173a2a", g: "#7fdca6", d: "#4faf7e", h: "#b6f0cd" },
    opal: { b: "#ffd166", A: "#2f8f63" },
    overlay: ["........b.......", "........A.......", ...dots(14)],
  },
  {
    id: "dailyk", name: "Дейлик", rarity: "common",
    colors: { o: "#3a2415", g: "#a06f45", d: "#7d5331", h: "#c4905c" },
    opal: { S: "#e3eaf0", F: "#f3e0c2" },
    overlay: ["......S..S......", ".......SS.......", "................", "......FFFF......", ...dots(12)],
  },
  {
    id: "backlogik", name: "Бэклогик", rarity: "common",
    colors: { o: "#4a4030", g: "#e8dcc0", d: "#c5b591", h: "#f5eed8" },
    opal: { y: "#ffe08a", k: "#ffb3c6", c: "#9be0e8" },
    overlay: [...dots(11), ".....yy..kk.....", ".......cc.......", ...dots(3)],
  },
  {
    id: "sprintik", name: "Спринтик", rarity: "uncommon",
    colors: { o: "#3a2410", g: "#f59a4b", d: "#cf7426", h: "#ffc187" },
    opal: { R: "#e23b3b", L: "#f4a94b" },
    overlay: [...dots(6), "....RRRRRRRR....", "...R............", "LL..............", "LL..............", ...dots(6)],
  },
  {
    id: "retrik", name: "Ретрик", rarity: "uncommon",
    colors: { o: "#16302d", g: "#3fb8ad", d: "#2a8f86", h: "#7fd8cf" },
    opal: { C: "#e6f4f2" },
    overlay: ["............CCC.", "...........CCCC.", "............CC..", "...........C....", "..........C.....", ...dots(11)],
  },
  {
    id: "bagik", name: "Багик", rarity: "rare",
    colors: { o: "#2e1640", g: "#a45fd0", d: "#7e3fae", h: "#c89bea" },
    opal: { A: "#7a3fa0", b: "#c08ad8" },
    overlay: [".....b....b.....", ".....A....A.....", ...dots(13), "....A.A..A.A...."],
  },
  {
    id: "pokerik", name: "Покерик", rarity: "rare",
    colors: { o: "#172547", g: "#4a78d8", d: "#2f55ad", h: "#8fb0f0" },
    opal: { W: "#f4f1e8", p: "#3a2a55" },
    overlay: [...dots(11), ".....W.W.W......", ".....WWWWW......", ".....p.p.p......", ...dots(2)],
  },
  {
    id: "deployk", name: "Деплоик", rarity: "epic",
    colors: { o: "#34140f", g: "#d24a3a", d: "#a32f24", h: "#f08070" },
    opal: { M: "#aab4c0", N: "#cf4b2a", f: "#ffb13b", F: "#ffe26b" },
    overlay: ["................", "................", "......MMMM......", ...dots(6), ".N............N.", ...dots(4), ".......ff.......", "......fFFf......"],
  },
  {
    id: "velocik", name: "Велоцик", rarity: "epic",
    colors: { o: "#173a16", g: "#54b94a", d: "#379030", h: "#8fe07f" },
    opal: { K: "#2a7a3a", G: "#7fd06a" },
    overlay: ["................", ".....K.K.K......", ...dots(6), ".............GG.", ".............G..", ...dots(6)],
  },
  {
    id: "unikornik", name: "Юникорник", rarity: "legendary",
    colors: { o: "#3a3550", g: "#f3f0fb", d: "#d6d0ea", h: "#ffffff" },
    opal: {
      H: "#ffd24d", q: "#ffe9a3", "*": "#fff7c2",
      "1": "#ff5d5d", "2": "#ffa84d", "3": "#ffe24d", "4": "#5dd97a", "5": "#5db8ff", "6": "#b08aff",
    },
    overlay: [
      "........H.......", ".......qH.......", "................", "............*...",
      ".............*..", ".1..............", ".2..............", "1...............",
      "3...............", "4...............", ".5.........*....", ".6..............", ...dots(4),
    ],
  },
];

export const SCRUMIKS: Record<string, Sprite> = Object.fromEntries(LIST.map((s) => [s.id, s]));
export const SCRUMIK_LIST = LIST;

export function spriteFor(speciesId: string | null | undefined): Sprite {
  return (speciesId && SCRUMIKS[speciesId]) || LIST[0];
}
