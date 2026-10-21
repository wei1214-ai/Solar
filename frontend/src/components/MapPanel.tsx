"use client";

import { useEffect, useMemo, useRef } from "react";
import { MapContainer, TileLayer, GeoJSON, LayersControl, useMap, CircleMarker, Popup } from "react-leaflet";
import L from "leaflet";
import type { Feature, FeatureCollection, Geometry } from "geojson";

import "leaflet/dist/leaflet.css";

import type { BuildingAnalysis, RankingEntry } from "@/lib/types";

const SATELLITE_URL =
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
const OSM_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
const OSM_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
const ESRI_ATTRIBUTION = "Tiles &copy; Esri, Maxar, Earthstar Geographics";

const EMPTY: FeatureCollection = { type: "FeatureCollection", features: [] };

type Props = {
  center: [number, number];
  zoom?: number;
  entries: RankingEntry[];
  selected: BuildingAnalysis | null;
  selectedBuildingId: string | null;
  onSelectBuilding: (buildingId: string) => void;
  showFootprints: boolean;
  showObstacles: boolean;
  showUsable: boolean;
  showPanels: boolean;
};

function FitBounds({ center, zoom }: { center: [number, number]; zoom: number }) {
  const map = useMap();
  const lastCenter = useRef<string | null>(null);

  useEffect(() => {
    const key = `${center[0].toFixed(6)},${center[1].toFixed(6)}`;
    // Only re-centre when the target actually moved. Without this guard every
    // status poll re-applied the view and the user could never zoom out.
    if (lastCenter.current === key) return;
    lastCenter.current = key;
    map.setView(center, zoom, { animate: true });
  }, [center, zoom, map]);

  return null;
}

export default function MapPanel({
  center,
  zoom = 17,
  entries,
  selected,
  selectedBuildingId,
  onSelectBuilding,
  showFootprints,
  showObstacles,
  showUsable,
  showPanels,
}: Props) {
  const footprints = useMemo<FeatureCollection>(() => {
    const features: Feature[] = entries
      .filter((entry) => entry.footprint)
      .map((entry) => ({
        type: "Feature",
        geometry: entry.footprint as Geometry,
        properties: {
          building_id: entry.building_id,
          rank: entry.rank,
          energy: entry.annual_energy_dc_kwh,
          score: entry.score_kwh_per_year_per_rm,
          kwp: entry.installed_kwp,
          panels: entry.panel_count,
        },
      }));
    return { type: "FeatureCollection", features };
  }, [entries]);

  const markers = useMemo<FeatureCollection>(() => {
    const features: Feature[] = entries.map((entry) => ({
      type: "Feature",
      geometry: {
        type: "Point",
        coordinates: [entry.centroid_lon, entry.centroid_lat],
      },
      properties: {
        building_id: entry.building_id,
        rank: entry.rank,
        energy: entry.annual_energy_dc_kwh,
        score: entry.score_kwh_per_year_per_rm,
        kwp: entry.installed_kwp,
      },
    }));
    return { type: "FeatureCollection", features };
  }, [entries]);

  const selectedFootprint = useMemo<FeatureCollection>(() => {
    if (!selected?.footprint) return EMPTY;
    return {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          geometry: selected.footprint as Geometry,
          properties: {},
        },
      ],
    };
  }, [selected]);

  const selectedSegments = useMemo<FeatureCollection>(
    () => ({ type: "FeatureCollection", features: selected?.roof_segments ?? [] }),
    [selected],
  );

  const selectedObjects = useMemo<FeatureCollection>(
    () => ({ type: "FeatureCollection", features: selected?.roof_objects ?? [] }),
    [selected],
  );

  const selectedUsable = useMemo<FeatureCollection>(
    () => ({ type: "FeatureCollection", features: selected?.usable_regions ?? [] }),
    [selected],
  );

  const selectedPanels = useMemo<FeatureCollection>(
    () => selected?.panel_layout ?? EMPTY,
    [selected],
  );

  return (
    <MapContainer
      center={center}
      zoom={zoom}
      scrollWheelZoom
      className="h-full w-full bg-slate-950"
      attributionControl={false}
    >
      <LayersControl position="topright">
        <LayersControl.BaseLayer checked name="Satellite (Esri)">
          <TileLayer url={SATELLITE_URL} attribution={ESRI_ATTRIBUTION} maxZoom={19} />
        </LayersControl.BaseLayer>
        <LayersControl.BaseLayer name="Street (OSM)">
          <TileLayer url={OSM_URL} attribution={OSM_ATTRIBUTION} maxZoom={19} />
        </LayersControl.BaseLayer>
      </LayersControl>

      <TileLayer url={SATELLITE_URL} attribution={ESRI_ATTRIBUTION} maxZoom={19} />

      <FitBounds center={center} zoom={selected ? 19 : zoom} />

      {showFootprints && footprints.features.length > 0 && (
        <GeoJSON
          key={`footprints-${footprints.features.length}`}
          data={footprints}
          style={(feature) => {
            const rank = Number(feature?.properties?.rank ?? 999);
            const top = rank <= 10;
            return {
              color: top ? "#fbbf24" : "#94a3b8",
              weight: top ? 2 : 1,
              fillColor: top ? "#fbbf24" : "#94a3b8",
              fillOpacity: top ? 0.18 : 0.07,
            };
          }}
          onEachFeature={(feature, layer) => {
            const props = feature.properties ?? {};
            layer.bindTooltip(
              `#${props.rank} · ${Number(props.kwp).toFixed(1)} kWp · ${props.panels} panels · ${Number(props.energy).toLocaleString()} kWh/yr`,
              { direction: "top", opacity: 0.95 },
            );
            layer.on({
              click: () => onSelectBuilding(String(props.building_id)),
            });
          }}
        />
      )}

      {showFootprints && markers.features.length > 0 && (
        <GeoJSON
          key={`markers-${markers.features.length}`}
          data={markers}
          pointToLayer={(feature, latlng) =>
            L.circleMarker(latlng, {
              radius: 5,
              fillColor: "#fbbf24",
              fillOpacity: 0.5,
              color: "#fde68a",
              weight: 1,
            })
          }
          onEachFeature={(feature, layer) => {
            const props = feature.properties ?? {};
            layer.bindTooltip(
              `#${props.rank} · ${Number(props.kwp).toFixed(1)} kWp · ${Number(props.energy).toLocaleString()} kWh/yr`,
              { direction: "top", opacity: 0.95 },
            );
            layer.on({
              click: () => onSelectBuilding(String(props.building_id)),
            });
          }}
        />
      )}

      {selected && selectedFootprint.features.length > 0 && (
        <GeoJSON
          key={`sel-footprint-${selected.building_id}`}
          data={selectedFootprint}
          style={{
            color: "#fbbf24",
            weight: 2.5,
            fillColor: "#fbbf24",
            fillOpacity: 0.12,
          }}
        />
      )}

      {selected && selectedSegments.features.length > 0 && (
        <GeoJSON
          key={`sel-segments-${selected.building_id}`}
          data={selectedSegments}
          style={{
            color: "#a5b4fc",
            weight: 1.5,
            fillColor: "#6366f1",
            fillOpacity: 0.08,
            dashArray: "4 3",
          }}
        />
      )}

      {selected && showObstacles && selectedObjects.features.length > 0 && (
        <GeoJSON
          key={`sel-objects-${selected.building_id}`}
          data={selectedObjects}
          style={{
            color: "#ef4444",
            weight: 2,
            fillColor: "#dc2626",
            fillOpacity: 0.55,
          }}
          onEachFeature={(feature, layer) => {
            const props = feature.properties ?? {};
            layer.bindTooltip(`${String(props.class)} · ${Number(props.height_m).toFixed(1)} m tall`, {
              direction: "top",
              opacity: 0.95,
            });
          }}
        />
      )}

      {selected && showUsable && selectedUsable.features.length > 0 && (
        <GeoJSON
          key={`sel-usable-${selected.building_id}`}
          data={selectedUsable}
          style={{
            color: "#22c55e",
            weight: 1.5,
            fillColor: "#16a34a",
            fillOpacity: 0.28,
          }}
        />
      )}

      {selected && showPanels && selectedPanels.features.length > 0 && (
        <GeoJSON
          key={`sel-panels-${selected.building_id}`}
          data={selectedPanels}
          style={{
            color: "#38bdf8",
            weight: 0.6,
            fillColor: "#0ea5e9",
            fillOpacity: 0.85,
          }}
        />
      )}

      {selected && (
        <CircleMarker
          center={[selected.centroid_lat, selected.centroid_lon]}
          radius={9}
          pathOptions={{ color: "#fbbf24", weight: 3, fillColor: "#fbbf24", fillOpacity: 0.2 }}
        >
          <Popup>
            <div className="text-xs">
              <div className="font-bold">{selected.name ?? selected.source_feature_id}</div>
              <div>
                {selected.panel_count} panels · {selected.installed_kwp.toFixed(2)} kWp
              </div>
            </div>
          </Popup>
        </CircleMarker>
      )}
    </MapContainer>
  );
}
