"use client";

import { Coins, Gauge, Trophy } from "lucide-react";

import { formatNumber, formatRM, formatYears } from "@/lib/format";
import type { RankingEntry, RankingsResponse } from "@/lib/types";

type Props = {
  rankings: RankingsResponse | null;
  rankMode: "energy" | "cost_adjusted";
  onRankModeChange: (mode: "energy" | "cost_adjusted") => void;
  selectedBuildingId: string | null;
  onSelectBuilding: (buildingId: string) => void;
  loading: boolean;
};

export default function Leaderboard({
  rankings,
  rankMode,
  onRankModeChange,
  selectedBuildingId,
  onSelectBuilding,
  loading,
}: Props) {
  const entries = rankings?.entries ?? [];

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-white/10 bg-slate-900/60">
      <div className="flex items-center justify-between gap-2 border-b border-white/10 px-3 py-2">
        <div className="flex items-center gap-1.5">
          <Trophy size={14} className="text-solar-400" />
          <h2 className="text-xs font-bold uppercase tracking-wider text-slate-200">Leaderboard</h2>
        </div>
        <div className="flex items-center gap-1 rounded-lg bg-white/5 p-0.5">
          <TabButton
            active={rankMode === "cost_adjusted"}
            onClick={() => onRankModeChange("cost_adjusted")}
            icon={<Gauge size={12} />}
            label="Potential Score"
          />
          <TabButton
            active={rankMode === "energy"}
            onClick={() => onRankModeChange("energy")}
            icon={<Coins size={12} />}
            label="Annual Energy"
          />
        </div>
      </div>

      <div className="grid grid-cols-[1.6rem_0.6rem_1fr_4.2rem_4.6rem_3.6rem] gap-1.5 border-b border-white/5 px-3 py-1.5 text-[9px] font-semibold uppercase tracking-wider text-slate-500">
        <span>#</span>
        <span />
        <span>Building</span>
        <span className="text-right">kWp</span>
        <span className="text-right">{rankMode === "energy" ? "kWh/yr" : "Score"}</span>
        <span className="text-right">Payback</span>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading && (
          <div className="p-4 text-center text-xs text-slate-500">Loading rankings…</div>
        )}
        {!loading && entries.length === 0 && (
          <div className="p-4 text-center text-xs text-slate-500">
            No ranked buildings yet — run an analysis to populate the leaderboard.
          </div>
        )}
        {entries.map((entry) => (
          <Row
            key={entry.building_id}
            entry={entry}
            rankMode={rankMode}
            active={entry.building_id === selectedBuildingId}
            onClick={() => onSelectBuilding(entry.building_id)}
          />
        ))}
      </div>

      {rankings && (
        <div className="border-t border-white/10 px-3 py-1.5 text-[10px] text-slate-500">
          {rankings.scope_label} · {formatNumber(rankings.evaluated_count)} evaluated of{" "}
          {formatNumber(rankings.candidate_count)} candidates · alg v{rankings.algorithm_version} · scoring v
          {rankings.scoring_version}
        </div>
      )}
    </div>
  );
}

function Row({
  entry,
  rankMode,
  active,
  onClick,
}: {
  entry: RankingEntry;
  rankMode: "energy" | "cost_adjusted";
  active: boolean;
  onClick: () => void;
}) {
  const value =
    rankMode === "energy"
      ? formatNumber(entry.annual_energy_dc_kwh)
      : entry.score_kwh_per_year_per_rm.toFixed(3);

  return (
    <button
      type="button"
      data-rank-row={entry.building_id}
      onClick={onClick}
      title={entry.recommendation?.headline}
      className={`grid w-full grid-cols-[1.6rem_0.6rem_1fr_4.2rem_4.6rem_3.6rem] items-center gap-1.5 border-b border-white/5 px-3 py-1.5 text-left text-[11px] transition ${
        active ? "bg-solar-400/15" : "hover:bg-white/5"
      }`}
    >
      <span
        className={`font-bold tabular-nums ${
          entry.rank <= 3 ? "text-solar-300" : "text-slate-500"
        }`}
      >
        {entry.rank}
      </span>
      <span
        className={`h-2 w-2 rounded-full ${TIER_COLORS[entry.recommendation?.tier ?? ""] ?? "bg-slate-600"}`}
        title={entry.recommendation?.tier ?? "unrated"}
      />
      <span className="truncate" title={entry.name ?? entry.source_feature_id}>
        <span className="block truncate font-medium text-slate-200">
          {entry.name ?? entry.source_feature_id}
        </span>
        <span className="block truncate text-[9px] uppercase tracking-wide text-slate-500">
          {entry.building_type} · {entry.panel_count} panels · {formatRM(entry.capex_base_rm)}
        </span>
      </span>
      <span className="text-right tabular-nums text-slate-300">{entry.installed_kwp.toFixed(1)}</span>
      <span className="text-right font-semibold tabular-nums text-solar-300">{value}</span>
      <span className="text-right tabular-nums text-slate-400">{formatYears(entry.payback_years)}</span>
    </button>
  );
}

const TIER_COLORS: Record<string, string> = {
  "STRONG CANDIDATE": "bg-emerald-400",
  "VIABLE CANDIDATE": "bg-sky-400",
  MARGINAL: "bg-amber-400",
  "LOW PRIORITY": "bg-slate-500",
};

function TabButton({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex items-center gap-1 rounded-md px-2 py-1 text-[10px] font-semibold transition ${
        active ? "bg-solar-400 text-slate-950" : "text-slate-400 hover:text-white"
      }`}
    >
      {icon}
      {label}
    </button>
  );
}
