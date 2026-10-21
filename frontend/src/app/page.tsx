"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Layers,
  Loader2,
  Map as MapIcon,
  RefreshCw,
  SunMedium,
  X,
} from "lucide-react";

import { api } from "@/lib/api";
import { formatStatus } from "@/lib/format";
import type { BuildingAnalysis, GeoSearchResult, Health, Preset, RankingsResponse, RunSummary } from "@/lib/types";

import AreaBar from "@/components/AreaBar";
import AreaSearch, { RADII, type Radius } from "@/components/AreaSearch";
import BuildingDetail from "@/components/BuildingDetail";
import Leaderboard from "@/components/Leaderboard";
import PitchMetrics from "@/components/PitchMetrics";
import StatsPanel from "@/components/StatsPanel";

const MapPanel = dynamic(() => import("@/components/MapPanel"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full w-full items-center justify-center bg-slate-950 text-xs text-slate-500">
      <Loader2 className="mr-2 animate-spin" size={14} /> Loading map…
    </div>
  ),
});

const DEFAULT_CENTER: [number, number] = [3.149, 101.711];

export default function Dashboard() {
  const [health, setHealth] = useState<Health | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [presetId, setPresetId] = useState<string | null>(null);
  const [customArea, setCustomArea] = useState<GeoSearchResult | null>(null);
  const [radius, setRadius] = useState<Radius>(RADII[0]);
  const [mode, setMode] = useState<"AREA" | "BUILDING">("AREA");
  const [run, setRun] = useState<RunSummary | null>(null);
  const [rankings, setRankings] = useState<RankingsResponse | null>(null);
  const [rankMode, setRankMode] = useState<"energy" | "cost_adjusted">("cost_adjusted");
  const [selected, setSelected] = useState<BuildingAnalysis | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [layers, setLayers] = useState({
    footprints: true,
    obstacles: true,
    usable: true,
    panels: true,
  });
  const [layerOpen, setLayerOpen] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const activeRunRef = useRef<string | null>(null);

  const mapEntries = useMemo(() => rankings?.entries ?? [], [rankings]);

  // --- bootstrap ------------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [healthRes, presetRes] = await Promise.all([api.health(), api.presets()]);
        if (cancelled) return;
        setHealth(healthRes);
        setPresets(presetRes);
      } catch (err) {
        if (!cancelled) setError(describeError(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // --- polling --------------------------------------------------------------
  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  const refreshRun = useCallback(
    async (runId: string) => {
      try {
        const summary = await api.run(runId);
        if (activeRunRef.current !== runId) return; // a newer run took over
        setRun(summary);
        if (["COMPLETED", "COMPLETED_WITH_EXCLUSIONS", "FAILED"].includes(summary.status)) {
          stopPolling();
          setBusy(false);
        }
        // Leaderboard fills up live while the batch is still running.
        const board = await api.rankings(runId, rankMode);
        if (activeRunRef.current !== runId) return;
        setRankings(board);
      } catch (err) {
        setError(describeError(err));
        setBusy(false);
        stopPolling();
      }
    },
    [rankMode, stopPolling],
  );

  // --- actions --------------------------------------------------------------
  const startAreaRun = useCallback(
    async (id: string) => {
      setBusy(true);
      setError(null);
      setSelected(null);
      setSelectedId(null);
      setRankings(null);
      try {
        const created = await api.createAreaRun(id);
        activeRunRef.current = created.run_id;
        const summary = await api.run(created.run_id);
        setRun(summary);
        stopPolling();
        pollRef.current = setInterval(() => void refreshRun(created.run_id), 2000);
      } catch (err) {
        setError(describeError(err));
        setBusy(false);
      }
    },
    [refreshRun, stopPolling],
  );

  const startBuildingRun = useCallback(
    async (lat: number, lon: number, label?: string) => {
      setBusy(true);
      setError(null);
      setSelected(null);
      setSelectedId(null);
      setRankings(null);
      try {
        const created = await api.createBuildingRun(lat, lon, label);
        activeRunRef.current = created.run_id;
        const summary = await api.run(created.run_id);
        setRun(summary);
        stopPolling();
        pollRef.current = setInterval(() => void refreshRun(created.run_id), 2000);
      } catch (err) {
        setError(describeError(err));
        setBusy(false);
      }
    },
    [refreshRun, stopPolling],
  );

  const handlePreset = useCallback(
    (id: string) => {
      setPresetId(id);
      setCustomArea(null);
      const preset = presets.find((p) => p.preset_id === id);
      if (mode === "AREA") {
        void startAreaRun(id);
      } else if (preset) {
        void startBuildingRun(preset.lat, preset.lon, preset.label);
      }
    },
    [mode, presets, startAreaRun, startBuildingRun],
  );

  const handleCustomArea = useCallback(
    (result: GeoSearchResult, areaRadius: Radius) => {
      setCustomArea(result);
      setPresetId(null);
      const label = result.short_label || result.label;
      if (mode === "BUILDING") {
        void startBuildingRun(result.lat, result.lon, label);
        return;
      }
      setBusy(true);
      setError(null);
      setSelected(null);
      setSelectedId(null);
      setRankings(null);
      void (async () => {
        try {
          const created = await api.createCustomAreaRun(
            result.lat,
            result.lon,
            areaRadius.halfSizeDeg,
            label,
          );
          activeRunRef.current = created.run_id;
          const summary = await api.run(created.run_id);
          setRun(summary);
          stopPolling();
          pollRef.current = setInterval(() => void refreshRun(created.run_id), 2000);
        } catch (err) {
          setError(describeError(err));
          setBusy(false);
        }
      })();
    },
    [mode, refreshRun, startBuildingRun, stopPolling],
  );

  const handleModeChange = useCallback(
    (next: "AREA" | "BUILDING") => {
      setMode(next);
      if (next === "AREA" && presetId) {
        void startAreaRun(presetId);
      }
    },
    [presetId, startAreaRun],
  );

  const handleSelectBuilding = useCallback(
    async (buildingId: string) => {
      setSelectedId(buildingId);
      const entry = mapEntries.find((item) => item.building_id === buildingId);
      try {
        if (mode === "BUILDING" && entry) {
          // Single-building mode: re-run the pipeline for just this roof.
          void startBuildingRun(entry.centroid_lat, entry.centroid_lon, entry.name ?? undefined);
        }
        const detail = await api.building(buildingId, run?.run_id);
        setSelected(detail);
      } catch (err) {
        setError(describeError(err));
      }
    },
    [mapEntries, mode, run?.run_id, startBuildingRun],
  );

  // refresh leaderboard when the ranking mode changes
  useEffect(() => {
    if (!run?.run_id) return;
    if (!["COMPLETED", "COMPLETED_WITH_EXCLUSIONS"].includes(run.status)) return;
    let cancelled = false;
    api
      .rankings(run.run_id, rankMode)
      .then((board) => {
        if (!cancelled) setRankings(board);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [rankMode, run?.run_id, run?.status]);

  // auto-start the default area run once the presets are known
  const autoStarted = useRef(false);
  useEffect(() => {
    if (autoStarted.current || presets.length === 0) return;
    autoStarted.current = true;
    const first = presets[0];
    setPresetId(first.preset_id);
    void startAreaRun(first.preset_id);
  }, [presets, startAreaRun]);

  const center = useMemo<[number, number]>(() => {
    if (selected) return [selected.centroid_lat, selected.centroid_lon];
    if (customArea) return [customArea.lat, customArea.lon];
    const preset = presets.find((p) => p.preset_id === presetId);
    if (preset) return [preset.lat, preset.lon];
    if (run?.center_lat && run?.center_lon) return [run.center_lat, run.center_lon];
    return DEFAULT_CENTER;
  }, [customArea, presets, presetId, run?.center_lat, run?.center_lon, selected]);

  return (
    <main className="flex h-screen flex-col overflow-hidden bg-slate-950 text-slate-100">
      <header className="flex items-center justify-between gap-4 border-b border-white/10 bg-slate-950/90 px-5 py-2.5">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-solar-300 to-solar-600 shadow-glow">
            <SunMedium size={20} className="text-slate-950" />
          </div>
          <div>
            <h1 className="text-sm font-black tracking-tight text-white">
              SolarSync
              <span className="ml-2 text-[10px] font-medium uppercase tracking-widest text-slate-500">
                Malaysia rooftop potential mapper
              </span>
            </h1>
            <p className="text-[10px] text-slate-500">
              City-scale rooftop solar potential, feasibility and ranking for Selangor
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {health && (
            <span
              className={`rounded-md border px-2 py-1 text-[10px] font-semibold uppercase tracking-wider ${
                health.data_mode === "live"
                  ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-300"
                  : "border-solar-400/30 bg-solar-400/10 text-solar-300"
              }`}
              title={
                health.data_mode === "live"
                  ? "Live Google Solar + Inkling data"
                  : "Demo fallback: real OSM footprints + Malaysia-physics simulation"
              }
            >
              {health.data_mode === "live" ? "● Live providers" : "● Demo fallback"}
            </span>
          )}
          <button
            type="button"
            onClick={() => presetId && void startAreaRun(presetId)}
            disabled={busy || !presetId}
            className="flex items-center gap-1.5 rounded-md border border-white/10 bg-white/5 px-2.5 py-1.5 text-[11px] font-semibold text-slate-200 transition hover:border-solar-400/40 hover:text-white disabled:opacity-40"
          >
            <RefreshCw size={12} className={busy ? "animate-spin" : ""} />
            Re-run
          </button>
        </div>
      </header>

      <AreaBar
        presets={presets}
        selectedPresetId={presetId}
        onSelectPreset={handlePreset}
        customArea={customArea}
        onClearCustomArea={() => {
          setCustomArea(null);
          const first = presets[0];
          if (!first) return;
          setPresetId(first.preset_id);
          void startAreaRun(first.preset_id);
        }}
        radius={radius}
        onRadiusChange={setRadius}
        onCustomArea={handleCustomArea}
        mode={mode}
        onModeChange={handleModeChange}
      />

      {error && (
        <div className="flex items-center gap-2 border-b border-rose-500/30 bg-rose-500/10 px-5 py-2 text-[11px] text-rose-200">
          <AlertTriangle size={13} />
          {error}
          <button type="button" className="ml-auto" onClick={() => setError(null)}>
            <X size={13} />
          </button>
        </div>
      )}

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 p-3 xl:grid-cols-[1fr_22rem]">
        <div className="flex min-h-0 flex-col gap-3">
          <div className="relative min-h-[22rem] flex-1 overflow-hidden rounded-xl border border-white/10 bg-slate-900">
            <MapPanel
              center={center}
              zoom={mode === "BUILDING" ? 19 : 17}
              entries={mapEntries}
              selected={selected}
              selectedBuildingId={selectedId}
              onSelectBuilding={handleSelectBuilding}
              showFootprints={layers.footprints}
              showObstacles={layers.obstacles}
              showUsable={layers.usable}
              showPanels={layers.panels}
            />

            <div className="pointer-events-none absolute left-3 top-3 z-[500] flex flex-col gap-1.5">
              <div className="pointer-events-auto rounded-lg border border-white/10 bg-slate-950/85 px-3 py-2 backdrop-blur">
                <div className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-solar-300">
                  <Activity size={11} />
                  {run ? formatStatus(run.status) : "Idle"}
                </div>
                {run && (
                  <>
                    <div className="mt-1 text-[10px] text-slate-400">
                      {run.scope_label} · alg v{run.algorithm_version} · scoring v{run.scoring_version}
                    </div>
                    <div className="mt-1 h-1 w-40 overflow-hidden rounded-full bg-white/10">
                      <div
                        className="h-full rounded-full bg-solar-400 transition-all"
                        style={{ width: `${run.progress}%` }}
                      />
                    </div>
                    <div className="mt-1 max-w-[13rem] truncate text-[10px] text-slate-500" title={run.message}>
                      {run.message}
                    </div>
                    {run.pvgis_reference_kwh_per_kwp != null && (
                      <div
                        className="mt-1.5 max-w-[15rem] rounded border border-white/10 bg-white/5 px-1.5 py-1 text-[9px] leading-tight text-slate-400"
                        title="Independent EU JRC PVGIS reference yield for this location (no API key). SolarSync's own model is shown for comparison."
                      >
                        <span className="font-semibold text-sky-300">PVGIS check</span>{" "}
                        {run.pvgis_reference_kwh_per_kwp.toLocaleString()} kWh/kWp/yr AC
                        {run.pvgis_delta_pct != null && (
                          <span className="text-slate-500"> · our model {run.pvgis_delta_pct > 0 ? "+" : ""}{run.pvgis_delta_pct.toFixed(0)}%</span>
                        )}
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>

            <div className="absolute right-3 top-3 z-[500]">
              <div className="relative">
                <button
                  type="button"
                  onClick={() => setLayerOpen((v) => !v)}
                  className="flex items-center gap-1.5 rounded-lg border border-white/10 bg-slate-950/85 px-2.5 py-1.5 text-[10px] font-semibold text-slate-200 backdrop-blur hover:border-solar-400/40"
                >
                  <Layers size={12} /> Layers
                </button>
                {layerOpen && (
                  <div className="absolute right-0 mt-1 w-52 rounded-lg border border-white/10 bg-slate-950/95 p-2 backdrop-blur">
                    <LayerToggle
                      label="Ranked building outlines"
                      color="#fbbf24"
                      checked={layers.footprints}
                      onChange={(v) => setLayers((s) => ({ ...s, footprints: v }))}
                    />
                    <LayerToggle
                      label="Usable roof (green)"
                      color="#22c55e"
                      checked={layers.usable}
                      onChange={(v) => setLayers((s) => ({ ...s, usable: v }))}
                    />
                    <LayerToggle
                      label="Obstacles (red)"
                      color="#ef4444"
                      checked={layers.obstacles}
                      onChange={(v) => setLayers((s) => ({ ...s, obstacles: v }))}
                    />
                    <LayerToggle
                      label="Panel layout (blue)"
                      color="#38bdf8"
                      checked={layers.panels}
                      onChange={(v) => setLayers((s) => ({ ...s, panels: v }))}
                    />
                    <div className="mt-1.5 border-t border-white/10 pt-1.5 text-[9px] leading-relaxed text-slate-500">
                      Base map: Esri World Imagery (satellite) or OpenStreetMap street layer — no API key
                      required.
                    </div>
                  </div>
                )}
              </div>
            </div>

            {mode === "BUILDING" && (
              <div className="absolute bottom-3 left-1/2 z-[500] -translate-x-1/2 rounded-lg border border-white/10 bg-slate-950/85 px-3 py-1.5 text-[10px] text-slate-300 backdrop-blur">
                <span className="font-semibold text-solar-300">Single building mode:</span> click a
                marker or leaderboard row to analyse that roof.
              </div>
            )}
          </div>

          <PitchMetrics run={run} />
        </div>

        <aside className="flex min-h-0 flex-col gap-3">
          <div className="rounded-xl border border-white/10 bg-slate-900/60 p-3">
            <div className="mb-2 flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-400">
              <MapIcon size={12} />
              Area screening statistics
            </div>
            <StatsPanel run={run} />
          </div>

          {selected ? (
            <BuildingDetail building={selected} onClose={() => setSelected(null)} />
          ) : (
            <div className="flex min-h-[14rem] flex-1 flex-col overflow-hidden rounded-xl border border-white/10 bg-slate-900/60">
              <Leaderboard
                rankings={rankings}
                rankMode={rankMode}
                onRankModeChange={setRankMode}
                selectedBuildingId={selectedId}
                onSelectBuilding={handleSelectBuilding}
                loading={busy}
              />
            </div>
          )}
        </aside>
      </div>
    </main>
  );
}

function LayerToggle({
  label,
  color,
  checked,
  onChange,
}: {
  label: string;
  color: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-[11px] text-slate-300 hover:bg-white/5">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="h-3 w-3 accent-solar-400"
      />
      <span className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: color }} />
      {label}
    </label>
  );
}

function describeError(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}
