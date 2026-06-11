import { equippedOverlays } from "./cosmetics";
import { SCRUMIK_BASE, spriteFor } from "./sprites";

const N = SCRUMIK_BASE.length; // 16

interface Props {
  speciesId: string | null | undefined;
  size?: number; // rendered px (square)
  showOverlay?: boolean;
  equipped?: Record<string, string>;
  className?: string;
}

/** Renders a 16×16 Скрамик as crisp SVG pixels: base + species feature + equipped cosmetics. */
export function PixelSprite({
  speciesId,
  size = 160,
  showOverlay = true,
  equipped,
  className,
}: Props) {
  const sp = spriteFor(speciesId);
  const px = size / N;
  const rects: JSX.Element[] = [];
  const cosmetics = equippedOverlays(equipped);

  // Top-most cosmetic pixel wins, then the species feature, then the base body.
  const cosmeticAt = (r: number, c: number): string | null => {
    for (let i = cosmetics.length - 1; i >= 0; i--) {
      const cos = cosmetics[i];
      const ch = cos.overlay[r]?.[c];
      if (ch && ch !== ".") return cos.opal[ch] ?? "#ff00ff";
    }
    return null;
  };

  for (let r = 0; r < N; r++) {
    const baseRow = SCRUMIK_BASE[r] ?? "";
    const ovRow = (showOverlay && sp.overlay?.[r]) || "";
    for (let c = 0; c < N; c++) {
      const cosFill = cosmeticAt(r, c);
      const ovCh = ovRow[c] && ovRow[c] !== "." ? ovRow[c] : null;
      const baseCh = baseRow[c] && baseRow[c] !== "." ? baseRow[c] : null;
      let fill: string | null = null;
      if (cosFill) {
        fill = cosFill;
      } else if (ovCh) {
        fill = sp.opal[ovCh] ?? "#ff00ff";
      } else if (baseCh === "W") {
        fill = "#ffffff";
      } else if (baseCh === "P") {
        fill = sp.colors.o;
      } else if (baseCh) {
        fill = (sp.colors as Record<string, string>)[baseCh] ?? "#ff00ff";
      }
      if (!fill) continue;
      rects.push(
        <rect key={`${r}-${c}`} x={c * px} y={r * px} width={px} height={px} fill={fill} />,
      );
    }
  }

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      shapeRendering="crispEdges"
      className={className}
      style={{ imageRendering: "pixelated" }}
      role="img"
      aria-label={sp.name}
    >
      {rects}
    </svg>
  );
}
