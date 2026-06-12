import asyncio
import os
import re
import time
import unicodedata
from pathlib import Path
from threading import Lock
from typing import Any

import requests
from flask import Flask, jsonify, render_template, send_from_directory
from shazamio import Shazam


BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=None,
)

BUILD_ID = "indie88-vercel-v5-20260612"

RADIO_NAME = os.getenv("RADIO_NAME", "Radio Indie88 FM").strip()
RADIO_STREAM = os.getenv(
    "RADIO_STREAM",
    "https://localradio.streamb.live/SB00348",
).strip()


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


IDENTIFY_SECONDS = env_int("IDENTIFY_SECONDS", 12, 7, 18)
IDENTIFY_CACHE_SECONDS = env_int("IDENTIFY_CACHE_SECONDS", 45, 20, 180)
MAX_AUDIO_BYTES = env_int("MAX_AUDIO_BYTES", 2_000_000, 250_000, 4_000_000)
MIN_AUDIO_BYTES = 20_000
DEFAULT_COVER = "/default-cover.webp"

identify_lock = Lock()
last_identification: dict[str, Any] = {
    "ts": 0.0,
    "ok": False,
    "title": RADIO_NAME,
    "artist": "Stream ao vivo",
    "album": "",
    "cover": DEFAULT_COVER,
    "shazam_cover": "",
    "itunes_cover": "",
    "source": "default",
    "message": "Ainda não identificado",
    "build": BUILD_ID,
}


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"[^a-zA-Z0-9]+", " ", value).lower()
    return " ".join(value.split())


def capture_stream_bytes() -> tuple[bytes, str]:
    """Recolhe uma pequena amostra do stream sem usar FFmpeg."""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; Indie88-Vercel/1.0)",
        "Accept": "audio/aac,audio/mpeg,audio/*;q=0.9,*/*;q=0.5",
        "Icy-MetaData": "0",
        "Cache-Control": "no-cache",
    }

    started = time.monotonic()
    audio = bytearray()

    with requests.get(
        RADIO_STREAM,
        headers=headers,
        stream=True,
        allow_redirects=True,
        timeout=(8, IDENTIFY_SECONDS + 12),
    ) as response:
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "audio/unknown").split(";", 1)[0]

        for chunk in response.iter_content(chunk_size=16_384):
            if chunk:
                audio.extend(chunk)

            elapsed = time.monotonic() - started
            if elapsed >= IDENTIFY_SECONDS or len(audio) >= MAX_AUDIO_BYTES:
                break

    if len(audio) < MIN_AUDIO_BYTES:
        raise RuntimeError(
            f"A amostra ficou demasiado pequena ({len(audio)} bytes)."
        )

    return bytes(audio), content_type


async def recognize_with_shazam(audio_bytes: bytes) -> dict[str, Any]:
    shazam = Shazam(
        language="en-US",
        endpoint_country="CA",
        segment_duration_seconds=min(10, IDENTIFY_SECONDS),
    )
    return await asyncio.wait_for(shazam.recognize(audio_bytes), timeout=25)


def run_shazam(audio_bytes: bytes) -> dict[str, Any]:
    return asyncio.run(recognize_with_shazam(audio_bytes))


def extract_shazam_track(data: dict[str, Any] | None) -> dict[str, str] | None:
    if not isinstance(data, dict):
        return None

    track = data.get("track")
    if not isinstance(track, dict):
        return None

    title = str(track.get("title") or "").strip()
    artist = str(track.get("subtitle") or "").strip()
    if not title or not artist:
        return None

    images = track.get("images") or {}
    shazam_cover = ""
    if isinstance(images, dict):
        shazam_cover = str(
            images.get("coverarthq")
            or images.get("coverart")
            or images.get("background")
            or ""
        )

    album = ""
    for section in track.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for item in section.get("metadata") or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("title") or "").lower()
            value = str(item.get("text") or "").strip()
            if "album" in key and value:
                album = value
                break
        if album:
            break

    return {
        "title": title,
        "artist": artist,
        "album": album,
        "shazam_cover": shazam_cover,
    }


def artwork_600(url: str) -> str:
    if not url:
        return ""
    return re.sub(r"\d+x\d+bb", "600x600bb", url)


def get_itunes_cover(title: str, artist: str) -> str:
    response = requests.get(
        "https://itunes.apple.com/search",
        params={
            "term": f"{artist} {title}",
            "media": "music",
            "entity": "song",
            "limit": 8,
            "country": "CA",
        },
        headers={"User-Agent": "Mozilla/5.0 (compatible; Indie88-Vercel/1.0)"},
        timeout=8,
    )
    response.raise_for_status()
    results = response.json().get("results") or []
    if not results:
        return ""

    wanted_title = normalize_text(title)
    wanted_artist = normalize_text(artist)
    best_item: dict[str, Any] | None = None
    best_score = -1

    for item in results:
        if not isinstance(item, dict):
            continue

        found_title = normalize_text(str(item.get("trackName") or ""))
        found_artist = normalize_text(str(item.get("artistName") or ""))
        score = 0

        if found_title == wanted_title:
            score += 6
        elif wanted_title in found_title or found_title in wanted_title:
            score += 3

        if found_artist == wanted_artist:
            score += 6
        elif wanted_artist in found_artist or found_artist in wanted_artist:
            score += 3

        if score > best_score:
            best_score = score
            best_item = item

    if not best_item:
        return ""

    return artwork_600(str(best_item.get("artworkUrl100") or ""))


def failed_identification(message: str, artist: str = "Não identificado") -> dict[str, Any]:
    return {
        "ts": time.time(),
        "ok": False,
        "title": RADIO_NAME,
        "artist": artist,
        "album": "",
        "cover": DEFAULT_COVER,
        "shazam_cover": "",
        "itunes_cover": "",
        "source": "default",
        "message": message,
        "build": BUILD_ID,
    }


def identify_current_song(force: bool = False) -> dict[str, Any]:
    global last_identification

    now = time.time()
    if not force and now - float(last_identification.get("ts", 0)) < IDENTIFY_CACHE_SECONDS:
        return last_identification

    with identify_lock:
        now = time.time()
        if not force and now - float(last_identification.get("ts", 0)) < IDENTIFY_CACHE_SECONDS:
            return last_identification

        try:
            audio_bytes, content_type = capture_stream_bytes()
            shazam_raw = run_shazam(audio_bytes)
            track = extract_shazam_track(shazam_raw)

            if not track:
                last_identification = failed_identification(
                    "O Shazam não conseguiu identificar a música neste momento."
                )
                last_identification["sample_bytes"] = len(audio_bytes)
                last_identification["content_type"] = content_type
                return last_identification

            try:
                itunes_cover = get_itunes_cover(track["title"], track["artist"])
            except Exception:
                itunes_cover = ""

            shazam_cover = track.get("shazam_cover", "")
            cover = itunes_cover or shazam_cover or DEFAULT_COVER
            source = "itunes" if itunes_cover else "shazam" if shazam_cover else "default"

            last_identification = {
                "ts": time.time(),
                "ok": True,
                "title": track["title"],
                "artist": track["artist"],
                "album": track.get("album", ""),
                "cover": cover,
                "shazam_cover": shazam_cover,
                "itunes_cover": itunes_cover,
                "source": source,
                "message": "Música identificada com sucesso.",
                "build": BUILD_ID,
                "sample_bytes": len(audio_bytes),
                "content_type": content_type,
            }
            return last_identification

        except requests.Timeout:
            last_identification = failed_identification(
                "O servidor da rádio demorou demasiado tempo a fornecer a amostra."
            )
        except requests.RequestException as exc:
            last_identification = failed_identification(
                f"Não foi possível aceder ao stream: {exc}"
            )
        except asyncio.TimeoutError:
            last_identification = failed_identification(
                "A identificação do Shazam excedeu o tempo disponível."
            )
        except Exception as exc:
            last_identification = failed_identification(
                f"Erro ao identificar: {type(exc).__name__}: {exc}"
            )

        return last_identification


@app.after_request
def add_response_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    if response.content_type and "application/json" in response.content_type:
        response.headers["Cache-Control"] = "no-store, max-age=0"

    return response


@app.get("/")
def index():
    return render_template(
        "index.html",
        config={
            "name": RADIO_NAME,
            "stream": RADIO_STREAM,
            "defaultCover": DEFAULT_COVER,
            "identifySeconds": IDENTIFY_SECONDS,
            "build": BUILD_ID,
        },
    )


# Estas rotas tornam a mesma pasta public/ utilizável ao correr localmente.
# No Vercel, os ficheiros de public/ são servidos diretamente pela CDN.
@app.get("/style.css")
def public_style():
    return send_from_directory(PUBLIC_DIR, "style.css")


@app.get("/script.js")
def public_script():
    return send_from_directory(PUBLIC_DIR, "script.js")


@app.get("/default-cover.webp")
def public_default_cover():
    response = send_from_directory(PUBLIC_DIR, "default-cover.webp")
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


@app.get("/api/radio")
def api_radio():
    return jsonify(
        {
            "ok": True,
            "name": RADIO_NAME,
            "stream": RADIO_STREAM,
            "cover": DEFAULT_COVER,
            "status": "online",
            "build": BUILD_ID,
        }
    )


@app.get("/api/health")
def api_health():
    return jsonify(
        {
            "ok": True,
            "app": RADIO_NAME,
            "platform": "vercel" if os.getenv("VERCEL") else "local",
            "stream_configured": bool(RADIO_STREAM),
            "identify_seconds": IDENTIFY_SECONDS,
            "identification_mode": "raw-stream-bytes",
            "ffmpeg_required": False,
            "build": BUILD_ID,
            "recognizer": "shazamio-core/raw-stream-bytes",
        }
    )


@app.get("/api/identify")
def api_identify():
    return jsonify(identify_current_song(force=False))


@app.get("/api/identify/force")
def api_identify_force():
    return jsonify(identify_current_song(force=True))


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
        threaded=True,
    )
