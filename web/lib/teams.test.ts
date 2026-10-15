import { describe, expect, it } from "vitest";
import { headshotArt, headshotUrl, logoArt, logoUrl } from "./teams";

describe("ESPN image URLs", () => {
  it("serves the original file when no width is asked", () => {
    expect(logoUrl("LV")).toBe("https://a.espncdn.com/i/teamlogos/nfl/500/lv.png");
    expect(logoUrl("LV", "dark")).toBe("https://a.espncdn.com/i/teamlogos/nfl/500-dark/lv.png");
    expect(headshotUrl(3916433)).toBe("https://a.espncdn.com/i/headshots/nfl/players/full/3916433.png");
  });

  it("asks ESPN's resizer for the smallest bucket that covers the width", () => {
    expect(logoUrl("GB", "light", 14)).toBe("https://a.espncdn.com/combiner/i?img=/i/teamlogos/nfl/500/gb.png&w=48");
    expect(headshotUrl(3916433, 120)).toBe("https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/3916433.png&w=160");
  });

  it("falls back to the original past the largest bucket", () => {
    expect(headshotUrl(3916433, 600)).toBe("https://a.espncdn.com/i/headshots/nfl/players/full/3916433.png");
  });

  it("has no portrait for a defence", () => {
    expect(headshotUrl(-16001, 40)).toBeNull();
    expect(headshotArt(-16001, 40)).toBeNull();
  });

  it("draws art at a 3x phone's pixels, one URL and no srcset", () => {
    expect(logoArt("KC", "dark", 36)).toBe("https://a.espncdn.com/combiner/i?img=/i/teamlogos/nfl/500-dark/kc.png&w=160");
    expect(headshotArt(1, 80)).toBe("https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/1.png&w=240");
    expect(headshotArt(1, 200)).toBe("https://a.espncdn.com/i/headshots/nfl/players/full/1.png");
  });
});
