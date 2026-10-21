"use client";

import { Building2, LayoutGrid, MapPin, Sun } from "lucide-react";

import type { GeoSearchResult, Preset } from "@/lib/types";

import AreaSearch, { type Radius } from "@/components/AreaSearch";

type Props = {
  presets: Preset[];
  selectedPresetId: string | null;
  onSelectPreset: (presetId: string) => void;
  customArea: GeoSearchResult | null;
  onClearCustomArea: () => void;
  radius: Radius;
  onRadiusChange: (radius: Radius) => void;
  onCustomArea: (result: GeoSearchResult, radius: Radius) => void;
  mode: "AREA" | "BUILDING";
  onModeChange: (mode: "AREA" | "BUILDING") => void;

};

export default function AreaBar({
  presets,
  selectedPresetId,
  onSelectPreset,
  customArea,
  onClearCustomArea,
  radius,
  onRadiusChange,
  onCustomArea,
  mode,
  onModeChange,

}: Props) {
  const grouped = presets.reduce<Record<string, Preset[]>>((acc, preset) => {
    (acc[preset.state] ??= []).push(preset);
    return acc;
  }, {});

  return (
    <div className="flex flex-col gap-3 border-b border-white/10 bg-slate-900/70 px-5 py-3 backdrop-blur lg:flex-row lg:items-center lg:justify-between">
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-1 rounded-lg bg-white/5 p-1">
          <ModeButton
            active={mode === "AREA"}
            onClick={() => onModeChange("AREA")}
            icon={<LayoutGrid size={14} />}
            label="Area ranking"
          />
          <ModeButton
            active={mode === "BUILDING"}
            onClick={() => onModeChange("BUILDING")}
            icon={<Building2 size={14} />}
            label="Single building"
          />
        </div>
        {mode === "BUILDING" && (
          <span className="text-xs text-slate-400">
            Click any building on the map to analyse one roof
          </span>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {Object.entries(grouped).map(([state, items]) => (
          <div key={state} className="flex items-center gap-1.5">
            <span className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
              <MapPin size={11} />
              {state}
            </span>
            {items.map((preset) => (
              <button
                key={preset.preset_id}
                type="button"
                onClick={() => onSelectPreset(preset.preset_id)}
                title={preset.description}
                className={`rounded-md border px-2.5 py-1.5 text-xs font-medium transition ${
                  selectedPresetId === preset.preset_id && !customArea
                    ? "border-solar-400 bg-solar-400/15 text-solar-300 shadow-glow"
                    : "border-white/10 bg-white/5 text-slate-300 hover:border-solar-400/40 hover:text-white"
                }`}
              >
                {preset.label.replace(", Kuala Lumpur", "").replace(", Selangor", "").replace(", Penang", "")}
              </button>
            ))}
          </div>
        ))}
      </div>

      <div className="relative flex items-center gap-2">
        {customArea && (
          <span className="flex items-center gap-1.5 rounded-md border border-solar-400/40 bg-solar-400/10 px-2 py-1 text-[11px] font-semibold text-solar-300">
            <MapPin size={11} />
            {customArea.short_label || customArea.label}
            <button type="button" onClick={onClearCustomArea} className="hover:text-white">
              脳
            </button>
          </span>
        )}
        <AreaSearch
          onPick={onCustomArea}
          radius={radius}
          onRadiusChange={onRadiusChange}

        />
      </div>

      <div className="hidden items-center gap-1.5 text-[11px] text-slate-500 2xl:flex">
        <Sun size={12} className="text-solar-400" />
        Overpass footprints 路 Solar roof planes 路 Inkling objects 路 MY CAPEX
      </div>
    </div>
  );
}

function ModeButton({
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
      className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-semibold transition ${
        active ? "bg-solar-400 text-slate-950" : "text-slate-400 hover:text-white"
      }`}
    >
      {icon}
      {label}
    </button>
  );
}
