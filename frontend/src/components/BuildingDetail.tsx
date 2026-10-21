"use client";

import { Box, Droplets, Factory, Layers, Ruler, Sun, Thermometer, Wind } from "lucide-react";

import { formatNumber, formatRM, formatYears } from "@/lib/format";
import type { BuildingAnalysis } from "@/lib/types";

type Props = {
  building: BuildingAnalysis | null;
  onClose: () => void;
};

export default function BuildingDetail({ building, onClose }: Props) {
  if (!building) return null;

  const metrics = (building.metrics ?? {}) as Record<string, unknown>;
  const panelProfile = (metrics.panel_profile ?? {}) as Record<string, number | string>;
  const cost = (metrics.cost ?? {}) as Record<string, number | string>;
  const orientation = (metrics.orientation_used ?? []) as string[];

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-white/10 bg-slate-900/70">
      <div className="flex items-start justify-between gap-2 border-b border-white/10 px-3 py-2">
        <div className="min-w-0">
          <h2 className="truncate text-sm font-bold text-white">
            {building.name ?? building.source_feature_id}
          </h2>
          <p className="truncate text-[10px] uppercase tracking-wide text-slate-500">
            {building.building_type} · {building.source_feature_id} ·{" "}
            {building.status === "RANKED" ? "ranked" : building.status}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md border border-white/10 px-2 py-1 text-[10px] text-slate-400 hover:border-white/25 hover:text-white"
        >
          Close
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        {building.recommendation && (
          <div
            className={`rounded-lg border p-3 ${
              TIER_STYLES[building.recommendation.tier]?.box ??
              "border-white/10 bg-white/[0.03]"
            }`}
          >
            <div className="flex items-center gap-2">
              <span
                className={`rounded px-1.5 py-0.5 text-[9px] font-black uppercase tracking-wider ${
                  TIER_STYLES[building.recommendation.tier]?.chip ?? "bg-white/10 text-slate-300"
                }`}
              >
                {building.recommendation.tier}
              </span>
              <span className="text-[11px] font-semibold text-slate-100">
                {building.recommendation.headline}
              </span>
            </div>
            <ul className="mt-1.5 space-y-0.5">
              {building.recommendation.reasons.map((reason) => (
                <li key={reason} className="flex items-start gap-1.5 text-[10px] text-slate-400">
                  <span className="mt-1 h-1 w-1 shrink-0 rounded-full bg-slate-500" />
                  {reason}
                </li>
              ))}
            </ul>
            <p className="mt-1.5 text-[9px] italic text-slate-500">
              {building.recommendation.confidence}
            </p>
          </div>
        )}

        {building.status !== "RANKED" && (
          <div className="rounded-lg border border-rose-400/25 bg-rose-500/10 p-2.5 text-[11px] text-rose-200">
            <div className="font-semibold">Not ranked · {building.status}</div>
            <div className="mt-0.5 text-rose-200/80">{building.exclusion_reason}</div>
          </div>
        )}

        <div className="grid grid-cols-2 gap-2">
          <Metric label="Panels" value={formatNumber(building.panel_count)} icon={<Layers size={12} />} />
          <Metric
            label="Installed"
            value={`${building.installed_kwp.toFixed(2)} kWp`}
            icon={<Factory size={12} />}
          />
          <Metric
            label="DC energy"
            value={`${formatNumber(building.annual_energy_dc_kwh)} kWh`}
            icon={<Sun size={12} />}
          />
          <Metric
            label="AC energy"
            value={`${formatNumber(building.annual_energy_ac_kwh)} kWh`}
            icon={<Wind size={12} />}
          />
          <Metric
            label="CAPEX (base)"
            value={formatRM(building.capex_base_rm)}
            icon={<Box size={12} />}
          />
          <Metric
            label="Cost range"
            value={`${formatRM(building.capex_low_rm)} – ${formatRM(building.capex_high_rm)}`}
            icon={<Box size={12} />}
          />
          <Metric
            label="Potential score"
            value={`${building.potential_score.toFixed(3)} kWh/RM`}
            icon={<Thermometer size={12} />}
          />
          <Metric
            label="Payback"
            value={formatYears(building.payback_years)}
            icon={<Ruler size={12} />}
          />
        </div>

        <div className="rounded-lg border border-white/10 bg-white/[0.03] p-2.5">
          <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
            Geometry
          </div>
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
            <Row label="Footprint" value={`${formatNumber(building.footprint_area_m2)} m²`} />
            <Row label="Usable roof" value={`${formatNumber(building.usable_area_m2)} m²`} />
            <Row label="Panel area" value={`${formatNumber(building.panel_area_m2)} m²`} />
            <Row label="Height" value={building.height_m ? `${building.height_m.toFixed(1)} m` : "—"} />
            <Row label="Obstacles" value={`${building.roof_objects.length}`} />
            <Row label="Roof planes" value={`${building.roof_segments.length}`} />
            <Row
              label="Energy rank"
              value={building.rank_energy ? `#${building.rank_energy}` : "—"}
            />
            <Row
              label="Score rank"
              value={building.rank_cost_adjusted ? `#${building.rank_cost_adjusted}` : "—"}
            />
          </dl>
        </div>

        {building.roof_segments.length > 0 && (
          <div className="rounded-lg border border-white/10 bg-white/[0.03] p-2.5">
            <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
              Roof planes
            </div>
            <div className="space-y-1">
              {building.roof_segments.map((segment, index) => {
                const props = (segment.properties ?? {}) as Record<string, number | string>;
                return (
                  <div key={index} className="flex items-center justify-between text-[11px]">
                    <span className="truncate text-slate-300">
                      {String(props.segment_id ?? `plane ${index + 1}`).slice(-8)}
                    </span>
                    <span className="tabular-nums text-slate-500">
                      pitch {Number(props.pitch_deg ?? 0).toFixed(0)}° · az{" "}
                      {Number(props.azimuth_deg ?? 0).toFixed(0)}° ·{" "}
                      {Number(props.area_m2 ?? 0).toFixed(0)} m² ·{" "}
                      {Number(props.flux_kwh_per_kwp ?? 0).toFixed(0)} kWh/kWp
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {building.roof_objects.length > 0 && (
          <div className="rounded-lg border border-white/10 bg-white/[0.03] p-2.5">
            <div className="mb-1.5 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
              <Droplets size={11} className="text-rose-400" />
              Rooftop obstacles
            </div>
            <div className="space-y-1">
              {building.roof_objects.map((object, index) => {
                const props = (object.properties ?? {}) as Record<string, number | string>;
                return (
                  <div key={index} className="flex items-center justify-between text-[11px]">
                    <span className="text-rose-200">{String(props.class ?? "object")}</span>
                    <span className="tabular-nums text-slate-500">
                      {Number(props.height_m ?? 0).toFixed(1)} m ·{" "}
                      {Number(props.area_m2 ?? 0).toFixed(1)} m² ·{" "}
                      {String(props.source_model ?? "-")}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {panelProfile.width_m && (
          <div className="rounded-lg border border-white/10 bg-white/[0.03] p-2.5 text-[10px] text-slate-500">
            <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
              Method
            </div>
            Reference panel {String(panelProfile.width_m)} × {String(panelProfile.height_m)} m @{" "}
            {String(panelProfile.rated_power_wp)} Wp · gap {String(panelProfile.gap_m)} m · setback{" "}
            {String(panelProfile.edge_setback_m)} m · obstacle clearance{" "}
            {String(panelProfile.obstacle_clearance_m)} m · walkway{" "}
            {String(panelProfile.walkway_width_m)} m
            {orientation.length > 0 ? ` · ${orientation.join(" / ")}` : ""}
            <div className="mt-1.5">
              CAPEX = RM {String(cost.fixed_base_rm)} + kWp × RM {String(cost.rate_rm_per_kwp)} (
              {String(cost.tier_label)}) · Potential score = E_AC ÷ CAPEX
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  icon,
}: {
  label: string;
  value: string;
  icon: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-2">
      <div className="flex items-center gap-1 text-[9px] font-semibold uppercase tracking-wider text-slate-500">
        {icon}
        {label}
      </div>
      <div className="mt-0.5 text-[13px] font-bold tabular-nums text-slate-100">{value}</div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <dt className="text-slate-500">{label}</dt>
      <dd className="tabular-nums text-slate-200">{value}</dd>
    </div>
  );
}

const TIER_STYLES: Record<string, { box: string; chip: string }> = {
  "STRONG CANDIDATE": {
    box: "border-emerald-400/30 bg-emerald-400/10",
    chip: "bg-emerald-400 text-slate-950",
  },
  "VIABLE CANDIDATE": {
    box: "border-sky-400/30 bg-sky-400/10",
    chip: "bg-sky-400 text-slate-950",
  },
  MARGINAL: {
    box: "border-amber-400/30 bg-amber-400/10",
    chip: "bg-amber-400 text-slate-950",
  },
  "LOW PRIORITY": {
    box: "border-white/10 bg-white/[0.03]",
    chip: "bg-white/15 text-slate-300",
  },
};
