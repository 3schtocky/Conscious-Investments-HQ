// Procedural pixel-art characters. Each avatar is drawn from parts, so the Settings panel can
// restyle anyone without image assets (and without third-party art licences).
//
// Sheet layout: 16x24 frames; rows of 3 walk frames for directions down, up, side (right;
// left is the side frames mirrored).

export type HairStyle = "short" | "long" | "bun" | "curly" | "bald" | "cap";
export type Accessory = "none" | "glasses" | "headset" | "tie" | "bowtie" | "earrings";

export interface AvatarParts {
  skin: string;
  hair: string;
  hairStyle: HairStyle;
  shirt: string;
  pants: string;
  accessory: Accessory;
}

export type AvatarSpec = string | Partial<AvatarParts> | { image: string } | null | undefined;

export const FRAME_W = 16;
export const FRAME_H = 24;
export const DIRS = ["down", "up", "side"] as const;
export type Dir = (typeof DIRS)[number];

export const HAIR_STYLES: HairStyle[] = ["short", "long", "bun", "curly", "bald", "cap"];
export const ACCESSORIES: Accessory[] = ["none", "glasses", "headset", "tie", "bowtie", "earrings"];
export const SKINS = ["#f6d7c3", "#eec09d", "#d9a47c", "#b97d56", "#8d5a3b", "#5e3b28"];
export const HAIRS = ["#2b2320", "#5a3b25", "#8a5a33", "#c99a4a", "#e2cf98", "#9c9c9c", "#b8452f", "#3f4f8a"];
export const CLOTHES = ["#2a68a8", "#d27030", "#3a9a7a", "#8a6db8", "#2E2C29", "#ECEAE1", "#b8452f",
  "#c9a227", "#46505e", "#7fa8d0", "#e8a0a8", "#f6f5f0"];

const DEFAULT: AvatarParts = { skin: SKINS[1], hair: HAIRS[1], hairStyle: "short",
  shirt: CLOTHES[0], pants: CLOTHES[8], accessory: "none" };

export const PRESETS: Record<string, AvatarParts> = {
  juno:   { skin: SKINS[3], hair: HAIRS[0], hairStyle: "bun",   shirt: "#2E2C29", pants: "#46505e", accessory: "earrings" },
  quill:  { skin: SKINS[0], hair: HAIRS[5], hairStyle: "short", shirt: "#2a68a8", pants: "#2E2C29", accessory: "glasses" },
  ledger: { skin: SKINS[2], hair: HAIRS[1], hairStyle: "curly", shirt: "#7fa8d0", pants: "#46505e", accessory: "none" },
  scout:  { skin: SKINS[1], hair: HAIRS[6], hairStyle: "cap",   shirt: "#d27030", pants: "#46505e", accessory: "none" },
  pip:    { skin: SKINS[4], hair: HAIRS[0], hairStyle: "short", shirt: "#c9a227", pants: "#2E2C29", accessory: "headset" },
  vera:   { skin: SKINS[2], hair: HAIRS[0], hairStyle: "long",  shirt: "#8a6db8", pants: "#2E2C29", accessory: "glasses" },
  tally:  { skin: SKINS[5], hair: HAIRS[0], hairStyle: "bald",  shirt: "#46505e", pants: "#2E2C29", accessory: "tie" },
  harbor: { skin: SKINS[1], hair: HAIRS[3], hairStyle: "long",  shirt: "#3a9a7a", pants: "#46505e", accessory: "earrings" },
  wren:   { skin: SKINS[0], hair: HAIRS[2], hairStyle: "bun",   shirt: "#e8a0a8", pants: "#46505e", accessory: "none" },
  sigma:  { skin: SKINS[3], hair: HAIRS[0], hairStyle: "short", shirt: "#3f4f8a", pants: "#2E2C29", accessory: "glasses" },
  delta:  { skin: SKINS[0], hair: HAIRS[7], hairStyle: "curly", shirt: "#7fa8d0", pants: "#46505e", accessory: "headset" },
  captain:{ skin: SKINS[1], hair: HAIRS[1], hairStyle: "short", shirt: "#f6f5f0", pants: "#2E2C29", accessory: "bowtie" },
};

/** Default look per agent id (used when a spec is partial, missing or an unknown preset). */
export const ID_PRESET: Record<string, string> = {
  chief_of_staff: "juno", er_lead: "quill", er_associate: "ledger", screen_lead: "scout",
  screen_associate: "pip", audit_lead: "vera", audit_associate: "tally", cr_lead: "harbor",
  cr_associate: "wren", quant_lead: "sigma", quant_associate: "delta", captain: "captain",
};

export function resolveParts(spec: AvatarSpec, fallbackKey?: string): AvatarParts {
  if (typeof spec === "string") return PRESETS[spec] ?? PRESETS[fallbackKey ?? ""] ?? DEFAULT;
  if (spec && !("image" in spec)) {
    const base = PRESETS[fallbackKey ?? ""] ?? DEFAULT;
    return { ...base, ...spec } as AvatarParts;
  }
  return PRESETS[fallbackKey ?? ""] ?? DEFAULT;
}

export function imageOf(spec: AvatarSpec): string | null {
  return spec && typeof spec === "object" && "image" in spec ? spec.image : null;
}

// ---------------------------------------------------------------------------------------------
function shade(hex: string, amt: number): string {
  const n = parseInt(hex.slice(1), 16);
  const f = (c: number) => Math.max(0, Math.min(255, Math.round(c + amt * 255)));
  const r = f(n >> 16), g = f((n >> 8) & 255), b = f(n & 255);
  return `#${((r << 16) | (g << 8) | b).toString(16).padStart(6, "0")}`;
}

type Px = (x: number, y: number, c: string) => void;

function drawFrame(px: Px, p: AvatarParts, dir: Dir, frame: number) {
  const skin = p.skin, skinD = shade(p.skin, -0.12);
  const shirt = p.shirt, shirtD = shade(p.shirt, -0.14);
  const pants = p.pants, pantsD = shade(p.pants, -0.12);
  const hair = p.hair, hairD = shade(p.hair, -0.1);
  const shoe = "#2b2320";
  const rect = (x: number, y: number, w: number, h: number, c: string) => {
    for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) px(x + i, y + j, c);
  };
  const step = frame === 0 ? 0 : frame === 1 ? 1 : -1;

  if (dir === "side") {
    // legs (walk: one forward, one back)
    rect(6, 17, 2, 5 - (step > 0 ? 1 : 0), pantsD);
    rect(8, 17, 2, 5 - (step < 0 ? 1 : 0), pants);
    rect(6 + (step > 0 ? 1 : 0), step > 0 ? 21 : 22, 2, 1, shoe);
    rect(8 + (step < 0 ? 1 : 0), step < 0 ? 21 : 22, 3, 1, shoe);
    // body
    rect(5, 10, 6, 7, shirt);
    rect(5, 16, 6, 1, shirtD);
    // arm swings
    rect(7 + step, 11, 2, 5, shirtD);
    rect(7 + step, 16, 2, 1, skin);
    // head
    rect(5, 2, 7, 8, skin);
    rect(11, 6, 1, 2, skin);           // nose
    px(9, 6, "#2b2320");               // eye
    rect(7, 10, 2, 1, skinD);          // neck
    // hair (back of head on the left)
    if (p.hairStyle !== "bald") {
      rect(5, 1, 7, 2, hair);
      rect(5, 3, 3, p.hairStyle === "long" ? 8 : 4, hair);
      if (p.hairStyle === "bun") rect(3, 2, 2, 3, hairD);
      if (p.hairStyle === "curly") { rect(4, 2, 1, 5, hairD); rect(5, 0, 6, 1, hair); }
      if (p.hairStyle === "cap") { rect(5, 1, 7, 2, hairD); rect(11, 3, 3, 1, hairD); }
    }
    if (p.accessory === "glasses") { rect(8, 6, 3, 1, "#2b2320"); }
    if (p.accessory === "headset") { rect(6, 1, 1, 1, "#2b2320"); rect(6, 5, 2, 3, "#2b2320"); rect(8, 8, 3, 1, "#2b2320"); }
    if (p.accessory === "earrings") px(7, 8, "#c9a227");
    return;
  }

  const back = dir === "up";
  // legs
  const lh = 5 - (step > 0 ? 1 : 0), rh = 5 - (step < 0 ? 1 : 0);
  rect(5, 17, 3, lh, pants);
  rect(8, 17, 3, rh, pantsD);
  rect(5, 17 + lh, 3, 1, shoe);
  rect(8, 17 + rh, 3, 1, shoe);
  // body + arms
  rect(4, 10, 8, 7, shirt);
  rect(11, 10, 1, 7, shirtD);
  rect(4, 16, 8, 1, shirtD);
  rect(3, 11 - step, 1, 5, shirtD);
  rect(12, 11 + step, 1, 5, shirtD);
  px(3, 16 - step, skin);
  px(12, 16 + step, skin);
  // neck + head
  rect(7, 9, 2, 1, skinD);
  rect(4, 2, 8, 7, skin);
  rect(4, 8, 8, 1, skinD);
  if (!back) {
    px(6, 5, "#2b2320"); px(9, 5, "#2b2320");
    px(7, 7, skinD); px(8, 7, skinD);
  }
  // hair
  const hs = p.hairStyle;
  if (hs !== "bald") {
    if (back) {
      rect(4, 1, 8, hs === "long" ? 10 : 6, hair);
      rect(4, 1 + (hs === "long" ? 9 : 5), 8, 1, hairD);
    } else {
      rect(4, 1, 8, 2, hair);
      rect(4, 3, 1, 2, hair); rect(11, 3, 1, 2, hair);
      if (hs === "long") { rect(3, 3, 1, 8, hair); rect(12, 3, 1, 8, hair); rect(4, 3, 1, 6, hairD); rect(11, 3, 1, 6, hairD); }
    }
    if (hs === "bun") rect(6, 0, 4, 2, hairD);
    if (hs === "curly") { rect(3, 1, 10, 2, hair); px(3, 3, hairD); px(12, 3, hairD); rect(5, 0, 6, 1, hairD); }
    if (hs === "cap") { rect(4, 1, 8, 2, hairD); if (!back) rect(3, 3, 10, 1, shade(hair, -0.25)); }
  } else {
    px(9, 3, shade(skin, 0.08));
  }
  // accessories
  const a = p.accessory;
  if (a === "glasses" && !back) { rect(5, 5, 2, 1, "#2b2320"); rect(8, 5, 2, 1, "#2b2320"); px(7, 5, "#2b2320"); }
  if (a === "headset") { rect(4, 0, 8, 1, "#2b2320"); rect(3, 4, 1, 3, "#2b2320"); rect(12, 4, 1, 3, "#2b2320"); if (!back) rect(10, 7, 2, 1, "#2b2320"); }
  if (a === "tie" && !back) { rect(7, 10, 2, 1, "#8e2a2a"); rect(7, 11, 2, 4, "#b8452f"); }
  if (a === "bowtie" && !back) { rect(6, 10, 4, 1, "#2E2C29"); px(7, 10, "#46505e"); }
  if (a === "earrings") { px(3, 7, "#c9a227"); px(12, 7, "#c9a227"); }
}

/** Draw the full sheet (3 dirs x 3 frames) with a dark outline and a soft shadow. */
export function drawSheet(parts: AvatarParts): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = FRAME_W * 3;
  canvas.height = FRAME_H * 3;
  const ctx = canvas.getContext("2d")!;
  DIRS.forEach((dir, row) => {
    for (let f = 0; f < 3; f++) {
      const ox = f * FRAME_W, oy = row * FRAME_H;
      const buf: (string | null)[][] = Array.from({ length: FRAME_H }, () => Array(FRAME_W).fill(null));
      drawFrame((x, y, c) => { if (x >= 0 && y >= 0 && x < FRAME_W && y < FRAME_H) buf[y][x] = c; }, parts, dir, f);
      // shadow
      ctx.fillStyle = "rgba(46,44,41,0.22)";
      ctx.fillRect(ox + 4, oy + 22, 8, 2);
      ctx.fillRect(ox + 3, oy + 23, 10, 1);
      // outline
      ctx.fillStyle = "#2E2C29";
      for (let y = 0; y < FRAME_H; y++) for (let x = 0; x < FRAME_W; x++) {
        if (buf[y][x]) continue;
        const n = [[1, 0], [-1, 0], [0, 1], [0, -1]].some(([dx, dy]) => buf[y + dy]?.[x + dx]);
        if (n) ctx.fillRect(ox + x, oy + y, 1, 1);
      }
      for (let y = 0; y < FRAME_H; y++) for (let x = 0; x < FRAME_W; x++) {
        const c = buf[y][x];
        if (c) { ctx.fillStyle = c; ctx.fillRect(ox + x, oy + y, 1, 1); }
      }
    }
  });
  return canvas;
}

/** A single front-facing frame, scaled, for UI previews (desk cards, settings). */
export function portrait(parts: AvatarParts, scale = 3): HTMLCanvasElement {
  const sheet = drawSheet(parts);
  const c = document.createElement("canvas");
  c.width = FRAME_W * scale;
  c.height = 14 * scale;   // head and shoulders
  const ctx = c.getContext("2d")!;
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(sheet, 0, 0, FRAME_W, 14, 0, 0, FRAME_W * scale, 14 * scale);
  return c;
}
