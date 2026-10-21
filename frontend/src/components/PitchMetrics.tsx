"use client";

import { BarChart3, Leaf, Wallet, Zap } from "lucide-react";

import { formatCompact, formatNumber, formatRM, formatYears } from "@/lib/format";
import type { RunSummary } from "@/lib/types";

type Props = {
  run: RunSummary | null;
};

export default function PitchMetrics({ run }: Props) {
  const energy = run?.total_annual_energy_dc_kwh ?? 0;
  const ac = energy * 0.82;
  const savings = ac * 0.5;
  const capex = run?.total_capex_base_rm ?? 0;
  const payback = savings > 0 ? capex / savings : 0;
  const co2 = (ac * 0.758) / 1000;
  const kwp = run?.total_installed_kwp ?? 0;

  return (
    <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-5">
      <Metric
        icon={<Zap size={15} className="text-solar-300" />}
        label="Annual DC yield"
        value={`${formatCompact(energy)} kWh`}
        sub={`${formatNumber(kwp, 1)} kWp installed · ${formatCompact(ac)} kWh AC`}
      />
      <Metric
        icon={<Wallet size={15} className="text-emerald-300" />}
        label="Installed CAPEX"
        value={formatRM(capex)}
        sub={`RM ${formatCompact(capex * 0.88)} – RM ${formatCompact(capex * 1.15)} (base ±)`}
      />
      <Metric
        icon={<BarChart3 size={15} className="text-sky-300" />}
        label="Bill savings / yr"
        value={formatRM(savings)}
        sub="at RM 0.50 / kWh reference tariff"
      />
      <Metric
        icon={<Wallet size={15} className="text-amber-300" />}
        label="Simple payback"
        value={formatYears(payback)}
        sub="CAPEX ÷ annual savings"
      />
      <Metric
        icon={<Leaf size={15} className="text-lime-300" />}
        label="CO₂ avoided / yr"
        value={`${formatNumber(co2, 1)} t`}
        sub="0.758 kg CO₂ / kWh grid factor"
      />
    </div>
  );
}

function Metric({
  icon,
  label,
  value,
  sub,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub: string;
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-gradient-to-b from-white/[0.07] to-white/[0.02] p-3">
      <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
        {icon}
        {label}
      </div>
      <div className="mt-1.5 text-lg font-bold tabular-nums text-white">{value}</div>
      <div className="mt-0.5 truncate text-[10px] text-slate-500" title={sub}>
        {sub}
      </div>
    </div>
  );
}
