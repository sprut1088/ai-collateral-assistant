const SUFFIX_MULTIPLIERS: Record<string, number> = {
  m: 1_000_000,
  mn: 1_000_000,
  mln: 1_000_000,
  mil: 1_000_000,
  milion: 1_000_000,
  million: 1_000_000,
  mm: 1_000_000,
  b: 1_000_000_000,
  bn: 1_000_000_000,
  bln: 1_000_000_000,
  bil: 1_000_000_000,
  bilion: 1_000_000_000,
  billion: 1_000_000_000,
};

const AMOUNT_TOKEN_RE = /([-+]?\d[\d,]*(?:\.\d+)?)(?:\s*([a-zA-Z]+))?/;

export function parseAmountLike(value: string | number | null | undefined): number | null {
  if (value == null) return null;

  const text = String(value).trim();
  if (!text) return null;

  const match = text.match(AMOUNT_TOKEN_RE);
  if (!match) return null;

  const rawNumber = match[1]?.replace(/,/g, "");
  if (!rawNumber) return null;

  const base = Number(rawNumber);
  if (!Number.isFinite(base)) return null;

  const rawSuffix = (match[2] || "").toLowerCase();
  const multiplier = SUFFIX_MULTIPLIERS[rawSuffix] ?? 1;
  return base * multiplier;
}

export function formatAmountDisplay(value: string | number | null | undefined): string {
  if (value == null) return "";

  const parsed = parseAmountLike(value);
  if (parsed == null) {
    return String(value);
  }

  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(parsed);
}

export function normalizeAmountForSave(value: string | number | null | undefined): string {
  if (value == null) return "";

  const parsed = parseAmountLike(value);
  if (parsed == null) {
    return String(value).trim();
  }

  if (Math.abs(parsed - Math.round(parsed)) < 1e-9) {
    return String(Math.round(parsed));
  }

  return parsed.toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
}
