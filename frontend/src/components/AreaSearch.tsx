"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2, MapPin, Search, X } from "lucide-react";

import { api } from "@/lib/api";
import type { GeoSearchResult } from "@/lib/types";

type Props = {
  onPick: (result: GeoSearchResult, radius: Radius) => void;
  radius: Radius;
  onRadiusChange: (radius: Radius) => void;
};

export type Radius = {
  id: string;
  label: string;
  halfSizeDeg: number;
  description: string;
};

export const RADII: Radius[] = [
  { id: "sm", label: "500 m", halfSizeDeg: 0.0045, description: "One or two blocks" },
  { id: "md", label: "1.5 km", halfSizeDeg: 0.0135, description: "A neighbourhood" },
  { id: "lg", label: "4 km", halfSizeDeg: 0.036, description: "A town district" },
];

export default function AreaSearch({ onPick, radius, onRadiusChange }: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<GeoSearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const onClickOutside = (event: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  async function runSearch(event?: React.FormEvent) {
    event?.preventDefault();
    const text = query.trim();
    if (text.length < 2) return;
    setLoading(true);
    setError(null);
    try {
      const payload = await api.geocode(text);
      setResults(payload.results);
      setOpen(true);
      if (payload.results.length === 0) {
        setError("No match. Try a district or landmark name (e.g. 'Bukit Jalil', 'Taman Melati').");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-1.5">
        <form onSubmit={runSearch} className="relative flex items-center">
          <Search
            size={13}
            className="pointer-events-none absolute left-2.5 text-slate-500"
            aria-hidden
          />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onFocus={() => results.length > 0 && setOpen(true)}
            placeholder="Search any Malaysian area..."
            className="w-56 rounded-md border border-white/10 bg-white/5 py-1.5 pl-7 pr-2 text-xs text-slate-100 outline-none placeholder:text-slate-600 focus:border-solar-400/50"
            aria-label="Search for an area"
          />
          {loading && (
            <Loader2 size={12} className="absolute right-2 animate-spin text-solar-400" />
          )}
        </form>

        <div className="flex items-center gap-0.5 rounded-md bg-white/5 p-0.5">
          {RADII.map((option) => (
            <button
              key={option.id}
              type="button"
              title={option.description}
              onClick={() => onRadiusChange(option)}
              className={`rounded px-1.5 py-1 text-[10px] font-semibold transition ${
                radius.id === option.id
                  ? "bg-solar-400 text-slate-950"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {error && <span className="text-[10px] text-rose-300">{error}</span>}

      {open && results.length > 0 && (
        <div
          ref={boxRef}
          className="absolute z-[900] mt-1 w-80 overflow-hidden rounded-lg border border-white/10 bg-slate-950/95 shadow-2xl backdrop-blur"
        >
          {results.map((result) => (
            <button
              key={`${result.osm_type}-${result.osm_id}`}
              type="button"
              onClick={() => {
                onPick(result, radius);
                setOpen(false);
                setResults([]);
                setQuery(result.short_label || result.label);
              }}
              className="flex w-full items-start gap-2 border-b border-white/5 px-3 py-2 text-left last:border-b-0 hover:bg-white/5"
            >
              <MapPin size={12} className="mt-0.5 shrink-0 text-solar-400" />
              <span className="min-w-0">
                <span className="block truncate text-[11px] font-medium text-slate-100">
                  {result.short_label || result.label}
                </span>
                <span className="block truncate text-[9px] text-slate-500">
                  {result.lat.toFixed(4)}, {result.lon.toFixed(4)} · {result.category || "place"}
                </span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function ClearSearchButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded border border-white/10 p-1 text-slate-500 hover:border-white/25 hover:text-white"
      title="Clear custom area"
    >
      <X size={11} />
    </button>
  );
}
