import asyncio
import os
import re
import subprocess
import tempfile
import time
import unicodedata
from pathlib import Path
from threading import Lock
from typing import Any

import requests
from flask import Flask, jsonify, render_template, request, send_from_directory
from imageio_ffmpeg import get_ffmpeg_exe
from shazamio import Shazam


BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=None,
)

BUILD_ID = "indie88-vercel-v6-logica-cascais-20260612"

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


# Mesma lógica da versão funcional: ficheiro WAV real em /tmp.
CAPTURE_SECONDS = env_int("SHAZAM_SAMPLE_SECONDS", 18, 10, 22)
IDENTIFY_CACHE_SECONDS = env_int("IDENTIFY_CACHE_SECONDS", 55, 20, 180)
MIN_SAMPLE_BYTES = 180_000
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


def ffmpeg_path() -> str:
    """Devolve o FFmpeg incluído no wheel do imageio-ffmpeg."""
    binary = get_ffmpeg_exe()
    if not binary or not Path(binary).exists():
        raise RuntimeError("O binário FFmpeg incluído no pacote não foi encontrado.")
    return binary


def create_sample_path() -> Path:
    stamp = f"{os.getpid()}_{int(time.time() * 1000)}"
    return Path(tempfile.gettempdir()) / f"indie88_shazam_{stamp}.wav"


def capture_stream(output_file: Path, seconds: int = CAPTURE_SECONDS) -> dict[str, Any]:
    """
    Grava áudio real da rádio num WAV PCM em /tmp.

    Esta é a mesma estratégia da aplicação que funcionou:
    imageio-ffmpeg -> WAV mono 44.1 kHz -> ficheiro enviado ao Shazam.
    """
    errors: list[str] = []

    for attempt in range(1, 3):
        attempt_seconds = seconds if attempt == 1 else max(12, seconds - 3)
        output_file.unlink(missing_ok=True)

        command = [
            ffmpeg_path(),
            "-hide_banner",
            "-loglevel", "error",
            "-nostdin",
            "-y",
            "-user_agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/149 Safari/537.36",
            "-headers",
            "Accept: audio/aac,audio/mpeg,audio/*;q=0.9,*/*;q=0.8\r\n"
            "Icy-MetaData: 0\r\n"
            "Accept-Encoding: identity\r\n"
            "Cache-Control: no-cache\r\n",
            "-rw_timeout", "12000000",
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_on_network_error", "1",
            "-reconnect_on_http_error", "4xx,5xx",
            "-reconnect_delay_max", "3",
            "-i", RADIO_STREAM,
            "-t", str(attempt_seconds),
            "-vn",
            "-map_metadata", "-1",
            "-af", "highpass=f=80,lowpass=f=15000,loudnorm=I=-16:TP=-1.5:LRA=11",
            "-ac", "1",
            "-ar", "44100",
            "-c:a", "pcm_s16le",
            str(output_file),
        ]

        started = time.monotonic()
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=attempt_seconds + 15,
                check=False,
            )
            elapsed = round(time.monotonic() - started, 2)
            size = output_file.stat().st_size if output_file.exists() else 0

            if result.returncode == 0 and size >= MIN_SAMPLE_BYTES:
                return {
                    "attempt": attempt,
                    "seconds": attempt_seconds,
                    "sample_bytes": size,
                    "capture_elapsed": elapsed,
                    "format": "wav-pcm-s16le-mono-44100",
                }

            detail = (result.stderr or "O stream não forneceu áudio suficiente.").strip()
            errors.append(
                f"tentativa {attempt}: retorno={result.returncode}, bytes={size}, "
                f"detalhe={detail[-600:]}"
            )
        except subprocess.TimeoutExpired:
            errors.append(f"tentativa {attempt}: tempo limite da captura")
        except Exception as exc:
            errors.append(f"tentativa {attempt}: {type(exc).__name__}: {exc}")

        if attempt == 1:
            time.sleep(1.5)

    output_file.unlink(missing_ok=True)
    raise RuntimeError("Não consegui criar uma amostra WAV válida. " + " | ".join(errors))


async def recognize_file(audio_file: Path) -> dict[str, Any]:
    shazam = Shazam(
        language="en-US",
        endpoint_country="CA",
        segment_duration_seconds=min(10, CAPTURE_SECONDS),
    )
    return await asyncio.wait_for(shazam.recognize(str(audio_file)), timeout=30)


def run_shazam(audio_file: Path) -> dict[str, Any]:
    return asyncio.run(recognize_file(audio_file))


def extract_shazam_track(data: dict[str, Any] | None) -> dict[str, str] | None:
    if not isinstance(data, dict):
        return None

    track = data.get("track")
    if not isinstance(track, dict):
        return None

    title = str(track.get("title") or "").strip()
    artist = str(track.get("subtitle") or track.get("artist") or "").strip()
    if not title:
        return None
    if not artist:
        artist = "Artista desconhecido"

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
            if key in {"album", "álbum"} or "album" in key:
                if value:
                    album = value
                    break
        if album:
            break

    return {
        "title": title,
        "artist": artist,
        "album": album,
        "shazam_cover": shazam_cover,
        "shazam_url": str(track.get("url") or ""),
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


def failed_identification(message: str, **details: Any) -> dict[str, Any]:
    response = {
        "ts": time.time(),
        "ok": False,
        "title": RADIO_NAME,
        "artist": "Não identificado",
        "album": "",
        "cover": DEFAULT_COVER,
        "shazam_cover": "",
        "itunes_cover": "",
        "source": "default",
        "message": message,
        "build": BUILD_ID,
    }
    response.update(details)
    return response


def identify_current_song(force: bool = False) -> dict[str, Any]:
    global last_identification

    now = time.time()
    if not force and now - float(last_identification.get("ts", 0)) < IDENTIFY_CACHE_SECONDS:
        return last_identification

    with identify_lock:
        now = time.time()
        if not force and now - float(last_identification.get("ts", 0)) < IDENTIFY_CACHE_SECONDS:
            return last_identification

        audio_file = create_sample_path()
        sample_info: dict[str, Any] = {}

        try:
            sample_info = capture_stream(audio_file)
            shazam_raw = run_shazam(audio_file)
            track = extract_shazam_track(shazam_raw)

            # Em identificação manual, tenta uma segunda parte da emissão se a primeira falhar.
            if not track and force:
                audio_file.unlink(missing_ok=True)
                time.sleep(1.5)
                second_info = capture_stream(audio_file, seconds=max(12, CAPTURE_SECONDS - 3))
                sample_info = {
                    **second_info,
                    "recognition_attempt": 2,
                }
                shazam_raw = run_shazam(audio_file)
                track = extract_shazam_track(shazam_raw)

            if not track:
                last_identification = failed_identification(
                    "O Shazam não reconheceu esta parte da emissão. Tenta novamente dentro de alguns segundos.",
                    **sample_info,
                )
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
                "shazam_url": track.get("shazam_url", ""),
                "source": source,
                "message": "Música identificada pelo Shazam a partir de uma amostra WAV real.",
                "build": BUILD_ID,
                **sample_info,
            }
            return last_identification

        except asyncio.TimeoutError:
            last_identification = failed_identification(
                "O Shazam excedeu o tempo disponível para analisar a amostra.",
                **sample_info,
            )
        except subprocess.TimeoutExpired:
            last_identification = failed_identification(
                "A captura do áudio excedeu o tempo disponível.",
                **sample_info,
            )
        except Exception as exc:
            last_identification = failed_identification(
                f"Erro ao identificar: {type(exc).__name__}: {exc}",
                **sample_info,
            )
        finally:
            try:
                audio_file.unlink(missing_ok=True)
            except Exception:
                pass

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
            "identifySeconds": CAPTURE_SECONDS,
            "build": BUILD_ID,
        },
    )


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
    return jsonify({
        "ok": True,
        "name": RADIO_NAME,
        "stream": RADIO_STREAM,
        "cover": DEFAULT_COVER,
        "status": "online",
        "build": BUILD_ID,
    })


@app.get("/api/stream-check")
def api_stream_check():
    try:
        response = requests.get(
            RADIO_STREAM,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Icy-MetaData": "0",
                "Accept-Encoding": "identity",
            },
            stream=True,
            allow_redirects=True,
            timeout=(7, 7),
        )
        response.raise_for_status()
        chunk = next(response.iter_content(chunk_size=512), b"")
        result = {
            "ok": bool(chunk),
            "status": response.status_code,
            "content_type": response.headers.get("Content-Type", ""),
            "bytes_received": len(chunk),
            "final_url": response.url,
            "build": BUILD_ID,
        }
        response.close()
        return jsonify(result), (200 if result["ok"] else 502)
    except Exception as exc:
        return jsonify({
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "build": BUILD_ID,
        }), 502


@app.get("/api/health")
def api_health():
    try:
        binary = ffmpeg_path()
        available = bool(binary and Path(binary).exists())
    except Exception as exc:
        binary = ""
        available = False
        ffmpeg_error = f"{type(exc).__name__}: {exc}"
    else:
        ffmpeg_error = ""

    return jsonify({
        "ok": available,
        "app": RADIO_NAME,
        "platform": "vercel" if os.getenv("VERCEL") else "local",
        "stream_configured": bool(RADIO_STREAM),
        "capture_seconds": CAPTURE_SECONDS,
        "identification_mode": "tmp-wav-imageio-ffmpeg",
        "ffmpeg_available": available,
        "ffmpeg_path": binary,
        "ffmpeg_error": ffmpeg_error,
        "build": BUILD_ID,
        "recognizer": "shazamio-file-wav",
    }), (200 if available else 503)


@app.route("/api/identify", methods=["GET", "POST"])
def api_identify():
    force = (
        request.method == "POST"
        or request.args.get("force", "").lower() in {"1", "true", "sim", "yes"}
    )
    return jsonify(identify_current_song(force=force))


@app.route("/api/identify/force", methods=["GET", "POST"])
def api_identify_force():
    return jsonify(identify_current_song(force=True))


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
        threaded=True,
    )
