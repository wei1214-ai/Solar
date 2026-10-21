"use client";

import { CheckCircle2, Filter, Loader2, XCircle } from "lucide-react";

import { formatNumber } from "@/lib/format";
import type { RunSummary } from "@/lib/types";

type Props = {
  run: RunSummary | null;
};

export default function StatsPanel({ run }: Props) {
  const total = run?.candidate_count ?? 0;
  const ranked = run?.ranked_count ?? 0;
  const excluded = run?.excluded_count ?? 0;
  const noData = run?.unranked_no_data_count ?? 0;
  const failed = run?.failed_count ?? 0;
  const processed = run?.processed_count ?? 0;
  const coverage = total > 0 ? Math.min(100, (processed / total) * 100) : 0;

  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      <Stat
        label="Ranked"
        value={formatNumber(ranked)}
        tone="emerald"
        icon={<CheckCircle2 size={13} />}
        hint="Analysed, scored and published to the leaderboard"
      />
      <Stat
        label="Excluded by rule"
        value={formatNumber(excluded)}
        tone="amber"
        icon={<Filter size={13} />}
        hint="Failed a documented rule (e.g. no space for a panel after setbacks)"
      />
      <Stat
        label="No data · unranked"
        value={formatNumber(noData)}
        tone="rose"
        icon={<XCircle size={13} />}
        hint="Provider returned 404 / missing core roof fields - never scored as zero"
      />
      <Stat
        label="Failed"
        value={formatNumber(failed)}
        tone="slate"
        icon={<Loader2 size={13} />}
        hint="Provider error or timeout - retryable"
      />

      <div className="col-span-2 mt-1 rounded-lg border border-white/10 bg-slate-900/60 p-3 sm:col-span-4">
        <div className="mb-1.5 flex items-center justify-between text-[11px] text-slate-400">
          <span>
            Candidate footprints in area:{" "}
            <span className="font-semibold text-slate-200">{formatNumber(total)}</span>
          </span>
          <span>
            Analysed:{" "}
            <span className="font-semibold text-slate-200">{formatNumber(processed)}</span>
          </span>
        </div>
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/10">
          <div
            className="h-full rounded-full bg-gradient-to-r from-solar-500 to-solar-300 transition-all"
            style={{ width: `${coverage}%` }}
          />
        </div>
        <p className="mt-1.5 text-[10px] leading-relaxed text-slate-500">
          Coverage is reported honestly: buildings without provider data are counted separately and never
          displayed as &ldquo;zero potential&rdquo;.
        </p>
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
  icon,
  hint,
}: {
  label: string;
  value: string;
  tone: "emerald" | "amber" | "rose" | "slate";
  icon: React.ReactNode;
  hint: string;
}) {
  const tones: Record<string, string> = {
    emerald: "text-emerald-300 border-emerald-400/25 bg-emerald-400/10",
    amber: "text-amber-300 border-amber-400/25 bg-amber-400/10",
    rose: "text-rose-300 border-rose-400/25 bg-rose-400/10",
    slate: "text-slate-300 border-white/10 bg-white/5",
  };
  return (
    <div className={`rounded-lg border p-2.5 ${tones[tone]}`} title={hint}>
      <div className="flex items-center gap-1.5">
        {icon}
        <span className="text-[10px] font-semibold uppercase tracking-wider opacity-80">{label}</span>
      </div>
      <div className="mt-1 text-xl font-bold tabular-nums leading-none">{value}</div>
    </div>
  );
}
