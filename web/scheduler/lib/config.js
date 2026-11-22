// Mirrors config/warmup.json at the repo root. Day 1 = 2026-11-21.
export const WARMUP = {
  startDate: "2026-11-21",
  bands: [
    { from: 1, to: 1, cap: 2 },   // owner raised day 1 from 0 to 2
    { from: 2, to: 3, cap: 2 },
    { from: 4, to: 6, cap: 4 },
    { from: 7, to: 13, cap: 6 },
    { from: 14, to: null, cap: 8 },
  ],
  minGapMinutes: 150,
  maxPerHour: 2,
};
export const PLATFORMS = ["tiktok", "youtube"];
export const TZ = "America/Toronto";
export const MEDIA_HOST = "https://database.blotato.io/";
