import type {
  BuildingAnalysis,
  CostBenchmark,
  GeoSearchResult,
  Health,
  Preset,
  RankingsResponse,
  RunSummary,
} from "./types";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://127.0.0.1:8000/api/v1";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* keep status text */
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => request<Health>("/health"),
  presets: () => request<Preset[]>("/presets"),
  geocode: (query: string) =>
    request<{ query: string; results: GeoSearchResult[] }>(
      `/geocode?q=${encodeURIComponent(query)}`,
    ),
  reverseGeocode: (lat: number, lon: number) =>
    request<{ label: string | null; short_label: string; lat: number; lon: number }>(
      `/reverse-geocode?lat=${lat}&lon=${lon}`,
    ),
  createCustomAreaRun: (lat: number, lon: number, halfSizeDeg: number, label: string) =>
    request<{ run_id: string; status: string; scope_label: string }>("/analyses", {
      method: "POST",
      body: JSON.stringify({
        scope_type: "AREA",
        lat,
        lon,
        radius_deg: halfSizeDeg,
        label,
      }),
    }),
  costBenchmarks: () => request<CostBenchmark>("/cost-benchmarks"),
  run: (runId: string) => request<RunSummary>(`/analyses/${runId}`),
  runs: () => request<RunSummary[]>("/analyses"),
  createAreaRun: (presetId: string) =>
    request<{ run_id: string; status: string; scope_label: string }>("/analyses", {
      method: "POST",
      body: JSON.stringify({ scope_type: "AREA", preset_id: presetId }),
    }),
  createBuildingRun: (lat: number, lon: number, label?: string) =>
    request<{ run_id: string; status: string; scope_label: string }>("/analyses", {
      method: "POST",
      body: JSON.stringify({ scope_type: "BUILDING", lat, lon, label }),
    }),
  rankings: (runId: string, rankMode: "energy" | "cost_adjusted", limit = 200) =>
    request<RankingsResponse>(
      `/areas/${runId}/rankings?rank_mode=${rankMode}&limit=${limit}`,
    ),
  building: (buildingId: string, runId?: string) =>
    request<BuildingAnalysis>(
      `/buildings/${buildingId}/analysis${runId ? `?run_id=${runId}` : ""}`,
    ),
};
