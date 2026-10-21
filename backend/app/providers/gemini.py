"""Gemini vision backend (Google AI Studio, free tier).

Used for rooftop obstacle detection: one generateContent call per satellite
mosaic, returning structured JSON with 0-1000 normalised boxes.

Three things keep this fast on a free key:

* **Quota sharding** - free quota is per Google project, so `GEMINI_API_KEYS`
  may hold several keys; each key is paced independently and the caller always
  takes the token that becomes available first (N keys ~ N x RPM).
* **Response cache** - identical image bytes + model + prompt are answered from
  `.cache/vision/` without touching the network, so re-running an area (or
  showing it twice during a demo) costs nothing.
* **Downscaling** - boxes are normalised [0,1000], so the image can be sent at
  a smaller resolution without changing the coordinate mapping.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import time
from pathlib import Path

import httpx

from ..config import BACKEND_DIR, Settings, get_settings
from .inkling import InklingError, InklingResponse

log = logging.getLogger("solarsync.gemini")

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Free-tier quota is per minute (15 RPM on Flash-Lite). Each key is paced
# separately so parallel workers never trip 429 on a single key.
MIN_CALL_INTERVAL_S = 4.2
KEY_COOLDOWN_S = 60.0
MAX_IMAGE_SIDE_PX = 512

CACHE_DIR = BACKEND_DIR / ".cache" / "vision"

_pace_lock = asyncio.Lock()
_key_next_at: dict[str, float] = {}


async def _acquire_key(keys: tuple[str, ...]) -> str:
    """Reserve the next free slot of the key that becomes available first."""
    async with _pace_lock:
        now = time.monotonic()
        candidates = {key: _key_next_at.get(key, 0.0) for key in keys}
        key = min(candidates, key=candidates.get)
        wait = candidates[key] - now
        if wait > 0:
            await asyncio.sleep(wait)
            now = time.monotonic()
        _key_next_at[key] = now + MIN_CALL_INTERVAL_S
        return key


def _cool_down(key: str, seconds: float = KEY_COOLDOWN_S) -> None:
    _key_next_at[key] = time.monotonic() + seconds


OBJECT_SCHEMA = {
    "type": "object",
    "properties": {
        "objects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "class": {
                        "type": "string",
                        "enum": [
                            "existing_pv",
                            "hvac_unit",
                            "water_tank",
                            "skylight",
                            "vent",
                            "other_obstacle",
                        ],
                    },
                    "bbox_norm_1000": {"type": "array", "items": {"type": "number"}},
                    "polygon_norm_1000": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "number"}},
                    },
                    "confidence": {"type": "number"},
                    "notes": {"type": "string"},
                },
                "required": ["class", "bbox_norm_1000", "confidence"],
            },
        },
        "needs_review": {"type": "boolean"},
    },
    "required": ["objects"],
}


def _text_from_payload(payload: dict) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        feedback = payload.get("promptFeedback") or {}
        raise InklingError(f"Gemini returned no candidates: {feedback}")
    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    chunks = [p.get("text", "") for p in parts if isinstance(p, dict)]
    return "".join(chunks)


def _cache_path(image_bytes: bytes, model: str, prompt: str, content_type: str) -> Path:
    digest = hashlib.sha1()
    digest.update(model.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(prompt.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(content_type.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(image_bytes)
    return CACHE_DIR / f"gemini_{digest.hexdigest()[:20]}.json"


def _read_cache(path: Path) -> InklingResponse | None:
    if not path.exists():
        return None
    try:
        return InklingResponse.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def _write_cache(path: Path, response: InklingResponse) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(response.model_dump_json(), encoding="utf-8")
    except OSError as exc:  # pragma: no cover
        log.warning("Could not write vision cache: %s", exc)


def _shrink(image_bytes: bytes, content_type: str) -> tuple[bytes, str]:
    """Downscale before upload; normalised boxes stay valid."""
    if content_type != "image/jpeg":
        return image_bytes, content_type
    try:
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as img:
            if max(img.size) <= MAX_IMAGE_SIDE_PX:
                return image_bytes, content_type
            img = img.convert("RGB")
            img.thumbnail((MAX_IMAGE_SIDE_PX, MAX_IMAGE_SIDE_PX))
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=88, optimize=True)
            return buffer.getvalue(), content_type
    except Exception as exc:  # noqa: BLE001 - never fail a run over resizing
        log.warning("Could not downscale image: %s", exc)
        return image_bytes, content_type


async def call_gemini(
    image_bytes: bytes,
    content_type: str = "image/jpeg",
    settings: Settings | None = None,
    tile_id: str = "tile_001",
    prompt: str | None = None,
) -> InklingResponse:
    settings = settings or get_settings()
    keys = settings.gemini_api_keys
    if not keys:
        raise InklingError("Gemini is not configured")

    from .imagery import encode_data_url

    prompt_text = prompt or "Detect rooftop equipment and return JSON."
    cached = _read_cache(_cache_path(image_bytes, settings.gemini_model, prompt_text, content_type))
    if cached is not None:
        log.info("Vision cache hit for %s", tile_id)
        return cached

    payload_bytes, payload_type = _shrink(image_bytes, content_type)
    data_url = encode_data_url(payload_bytes, payload_type)
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt_text},
                    {"inline_data": {"mime_type": payload_type, "data": data_url.split(",", 1)[1]}},
                ],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": OBJECT_SCHEMA,
            "temperature": 0,
            "thinkingConfig": {"thinkingLevel": settings.gemini_thinking_level},
        },
    }
    url = GEMINI_ENDPOINT.format(model=settings.gemini_model)

    last_error: Exception | None = None
    for attempt in range(4):
        key = await _acquire_key(keys)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
                response = await client.post(url, headers={"x-goog-api-key": key}, json=body)
        except httpx.HTTPError as exc:
            last_error = exc
            await asyncio.sleep(1.5 * (attempt + 1))
            continue

        if response.status_code == 429:
            delay = float(response.headers.get("retry-after", 0) or 0) or 20.0
            _cool_down(key, max(delay, KEY_COOLDOWN_S))
            last_error = InklingError(f"Gemini rate limited (429), retry in {delay:.0f}s")
            log.warning("Gemini 429 on %s, cooling key for %.0fs", tile_id, delay)
            continue
        if response.status_code >= 500:
            last_error = InklingError(f"Gemini server error {response.status_code}")
            await asyncio.sleep(1.5 * (attempt + 1))
            continue
        if response.status_code >= 400:
            raise InklingError(f"Gemini error {response.status_code}: {response.text[:300]}")

        try:
            parsed = json.loads(_text_from_payload(response.json()))
            result = InklingResponse.model_validate(parsed)
        except (ValueError, InklingError, TypeError) as exc:
            last_error = exc
            continue

        _write_cache(_cache_path(image_bytes, settings.gemini_model, prompt_text, content_type), result)
        return result

    raise InklingError(f"Gemini request failed: {last_error}")
