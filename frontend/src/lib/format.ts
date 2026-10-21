export function formatNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-MY", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function formatRM(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `RM ${value.toLocaleString("en-MY", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

export function formatCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return value.toFixed(abs < 10 ? 1 : 0);
}

export function formatYears(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (value > 100) return ">100 yrs";
  return `${value.toFixed(1)} yrs`;
}

export function formatStatus(status: string): string {
  const map: Record<string, string> = {
    QUEUED: "Queued",
    INGESTING_BUILDINGS: "Ingesting footprints",
    SOLAR_DATA: "Fetching solar data",
    ROOF_VISION: "Roof vision",
    GEOMETRY: "Usable roof geometry",
    LAYOUT_OPTIMIZATION: "Panel layout",
    SCORING: "Scoring",
    COMPLETED: "Completed",
    COMPLETED_WITH_EXCLUSIONS: "Completed with exclusions",
    FAILED: "Failed",
  };
  return map[status] ?? status;
}
