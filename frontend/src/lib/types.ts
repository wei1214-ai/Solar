export type Preset = {
  preset_id: string;
  label: string;
  region: string;
  state: string;
  lat: number;
  lon: number;
  half_size_deg: number;
  description: string;
};

export type Health = {
  status: string;
  app: string;
  version: string;
  data_mode: "live" | "demo";
  google_key_configured: boolean;
  inkling_configured: boolean;
  database: string;
  algorithm_version: string;
  scoring_version: string;
};

export type GeoSearchResult = {
  label: string;
  short_label: string;
  lat: number;
  lon: number;
  osm_type: string;
  osm_id: string;
  category: string;
  bbox: [number, number, number, number] | null;
};

export type RunSummary = {
  run_id: string;
  scope_type: "BUILDING" | "AREA";
  scope_label: string;
  preset_id: string | null;
  status: string;
  progress: number;
  message: string;
  candidate_count: number;
  processed_count: number;
  ranked_count: number;
  excluded_count: number;
  unranked_no_data_count: number;
  failed_count: number;
  total_annual_energy_dc_kwh: number;
  total_installed_kwp: number;
  total_capex_base_rm: number;
  data_mode: string;
  solar_baseline_kwh_per_kwp: number;
  pvgis_reference_kwh_per_kwp: number | null;
  pvgis_delta_pct: number | null;
  center_lat: number | null;
  center_lon: number | null;
  bbox: string | null;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  algorithm_version: string;
  model_version: string;
  scoring_version: string;
  source_policy_version: string;
};

export type RankingEntry = {
  rank: number;
  building_id: string;
  source_feature_id: string;
  name: string | null;
  building_type: string;
  centroid_lat: number;
  centroid_lon: number;
  status: string;
  panel_count: number;
  installed_kwp: number;
  annual_energy_dc_kwh: number;
  annual_energy_ac_kwh: number;
  capex_base_rm: number;
  capex_low_rm: number;
  capex_high_rm: number;
  score_kwh_per_year_per_rm: number;
  annual_savings_rm: number;
  payback_years: number;
  co2_offset_tons: number;
  footprint_area_m2: number;
  height_m: number | null;
  footprint: Geometry | null;
  recommendation: Recommendation | null;
}

export type Recommendation = {
  tier: string;
  headline: string;
  reasons: string[];
  confidence: string;
};;

export type RankingsResponse = {
  run_id: string;
  rank_mode: "energy" | "cost_adjusted";
  scope_label: string;
  generated_at: string | null;
  evaluated_count: number;
  candidate_count: number;
  excluded_count: number;
  unranked_no_data_count: number;
  failed_count: number;
  data_mode: string;
  algorithm_version: string;
  scoring_version: string;
  entries: RankingEntry[];
};

import type { Feature, FeatureCollection, Geometry } from "geojson";

export type GeoJsonFeature = Feature;
export type GeoJsonCollection = FeatureCollection;
export type GeoJsonGeometry = Geometry;

export type BuildingAnalysis = {
  building_id: string;
  run_id: string;
  status: string;
  exclusion_reason: string | null;
  source: string;
  source_feature_id: string;
  name: string | null;
  building_type: string;
  centroid_lat: number;
  centroid_lon: number;
  footprint_area_m2: number;
  roof_area_m2: number;
  height_m: number | null;
  num_levels: number | null;
  panel_count: number;
  installed_kwp: number;
  panel_area_m2: number;
  usable_area_m2: number;
  annual_energy_dc_kwh: number;
  annual_energy_ac_kwh: number;
  capex_base_rm: number;
  capex_low_rm: number;
  capex_high_rm: number;
  potential_score: number;
  annual_savings_rm: number;
  payback_years: number;
  co2_offset_tons: number;
  rank_energy: number | null;
  rank_cost_adjusted: number | null;
  footprint: Geometry | null;
  roof_segments: GeoJsonFeature[];
  roof_objects: GeoJsonFeature[];
  usable_regions: GeoJsonFeature[];
  panel_layout: GeoJsonCollection | null;
  metrics: Record<string, unknown> | null;
  recommendation: Recommendation | null;
};

export type CostBenchmark = {
  version: string;
  effective_date: string;
  fixed_base_rm: number;
  formula: string;
  included_scope: string;
  source_url: string;
  tiers: {
    capacity_min_kwp: number;
    capacity_max_kwp: number | null;
    low_rm_per_kwp: number;
    base_rm_per_kwp: number;
    high_rm_per_kwp: number;
    confidence_level: string;
    sample_count: number;
  }[];
};
