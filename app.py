import asyncio
import os
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import requests
from aiohttp_retry import ExponentialRetry
from flask import (
    Flask,
    after_this_request,
    jsonify,
    render_template,
    send_file,
    send_from_directory,
)
from imageio_ffmpeg import get_ffmpeg_exe
from shazamio import Shazam
from shazamio.client import HTTPClient


BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=None,
)

BUILD_ID = "indie88-vercel-v8-logica-m80-20260612"
RADIO_NAME = os.getenv("RADIO_NAME", "Radio Indie88 FM").strip()
STREAM_URL = os.getenv(
    "RADIO_STREAM",
    "https://localradio.streamb.live/SB00348",
).strip()
DEFAULT_COVER = "/default-cover.webp"

# Estes valores são iguais aos que funcionam na aplicação M80 Ballads.
CAPTURE_SECONDS = 12
SHAZAM_SEGMENT_SECONDS = 10
MP3_BITRATE = "128k"
SAMPLE_RATE = 44_100
AUDIO_CHANNELS = 1
MIN_MP3_BYTES = 80_000

STREAM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/149.0.0.0 Safari/537.36"
    ),
    "Accept": "audio/aac,audio/mpeg,audio/*;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "identity",
    "Icy-MetaData": "0",
    "Cache-Control": "no-cache",
}


def ffmpeg_path() -> str:
    """Binário FFmpeg incluído no pacote imageio-ffmpeg."""
    binary = get_ffmpeg_exe()
    if not binary or not Path(binary).exists():
        raise RuntimeError("O binário FFmpeg do imageio-ffmpeg não foi encontrado.")
    return binary


@lru_cache(maxsize=1)
def ffmpeg_supports_mp3() -> bool:
    try:
        result = subprocess.run(
            [ffmpeg_path(), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        output = f"{result.stdout}\n{result.stderr}".lower()
        return "libmp3lame" in output
    except Exception:
        return False


def normalize_track(track: Any) -> dict[str, Any] | None:
    if not isinstance(track, dict):
        return None

    title = str(track.get("title") or "").strip()
    artist = str(track.get("subtitle") or track.get("artist") or "").strip()

    if not title:
        return None
    if not artist:
        artist = "Artista desconhecido"

    images = track.get("images") or {}
    cover = DEFAULT_COVER
    if isinstance(images, dict):
        cover = (
            images.get("coverarthq")
            or images.get("coverart")
            or images.get("background")
            or DEFAULT_COVER
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

    now = int(time.time())
    return {
        "title": title,
        "artist": artist,
        "album": album,
        "cover": str(cover),
        "shazam_url": str(track.get("url") or ""),
        "identified_at": now,
        "played_at": now,
    }


def build_capture_command(output_file: Path, seconds: int) -> list[str]:
    """Comando copiado da lógica funcional da M80 Ballads."""
    return [
        ffmpeg_path(),
        "-hide_banner",
        "-loglevel", "error",
        "-nostdin",
        "-y",

        "-user_agent", STREAM_HEADERS["User-Agent"],
        "-headers",
        "Accept: audio/aac,audio/mpeg,audio/*;q=0.9,*/*;q=0.8\r\n"
        "Icy-MetaData: 0\r\n"
        "Accept-Encoding: identity\r\n"
        "Connection: close\r\n",

        "-rw_timeout", "6500000",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_on_network_error", "1",
        "-reconnect_on_http_error", "4xx,5xx",
        "-reconnect_delay_max", "2",

        "-i", STREAM_URL,
        "-t", str(seconds),
        "-vn",

        # Filtros leves iguais aos da M80. Sem loudnorm e sem estéreo pesado.
        "-af", "highpass=f=70,lowpass=f=15000,volume=1.35",
        "-ac", str(AUDIO_CHANNELS),
        "-ar", str(SAMPLE_RATE),

        "-c:a", "libmp3lame",
        "-b:a", MP3_BITRATE,
        "-map_metadata", "-1",
        "-id3v2_version", "0",
        "-write_xing", "0",
        "-f", "mp3",
        str(output_file),
    ]


def capture_stream_mp3(output_file: Path) -> dict[str, Any]:
    """Grava uma única amostra MP3 de 12 segundos em /tmp."""
    output_file.unlink(missing_ok=True)
    command = build_capture_command(output_file, CAPTURE_SECONDS)

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except subprocess.TimeoutExpired:
        size = output_file.stat().st_size if output_file.exists() else 0
        if size >= MIN_MP3_BYTES:
            return {
                "format": "mp3",
                "bitrate": MP3_BITRATE,
                "sample_rate": SAMPLE_RATE,
                "channels": AUDIO_CHANNELS,
                "bytes": size,
                "seconds": CAPTURE_SECONDS,
                "ffmpeg_returncode": "timeout-com-amostra-valida",
            }
        raise RuntimeError(
            "A captura MP3 excedeu o tempo disponível e não produziu "
            f"áudio suficiente ({size} bytes)."
        )

    size = output_file.stat().st_size if output_file.exists() else 0

    # Tal como na M80: aceita o MP3 completo mesmo que o servidor feche a ligação
    # e o FFmpeg devolva um código diferente de zero.
    if size >= MIN_MP3_BYTES:
        return {
            "format": "mp3",
            "bitrate": MP3_BITRATE,
            "sample_rate": SAMPLE_RATE,
            "channels": AUDIO_CHANNELS,
            "bytes": size,
            "seconds": CAPTURE_SECONDS,
            "ffmpeg_returncode": result.returncode,
        }

    detail = (
        result.stderr
        or result.stdout
        or "O stream não enviou áudio MP3 suficiente."
    ).strip()

    raise RuntimeError(
        "Não foi possível criar uma amostra MP3 válida da Indie88. "
        f"Tamanho: {size} bytes. Detalhe: {detail[-600:]}"
    )


async def recognize_mp3(audio_file: Path) -> dict[str, Any] | None:
    """Reconhecimento com os mesmos limites e retries da M80 Ballads."""
    audio_bytes = audio_file.read_bytes()
    if len(audio_bytes) < MIN_MP3_BYTES:
        raise RuntimeError("A amostra MP3 ficou demasiado pequena para identificar.")

    retry_options = ExponentialRetry(
        attempts=2,
        max_timeout=2,
        statuses={429, 500, 502, 503, 504},
    )
    http_client = HTTPClient(retry_options=retry_options)
    shazam = Shazam(
        language="en-US",
        endpoint_country="CA",
        http_client=http_client,
        segment_duration_seconds=SHAZAM_SEGMENT_SECONDS,
    )

    result = await asyncio.wait_for(
        shazam.recognize(audio_bytes),
        timeout=14,
    )

    track = result.get("track") if isinstance(result, dict) else None
    return normalize_track(track)


@app.after_request
def add_response_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if response.content_type and "application/json" in response.content_type:
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@app.get("/")
def index():
    return render_template(
        "index.html",
        config={
            "name": RADIO_NAME,
            "stream": STREAM_URL,
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
        "stream": STREAM_URL,
        "cover": DEFAULT_COVER,
        "status": "online",
        "build": BUILD_ID,
    })


@app.post("/api/identify")
def identify():
    started = time.perf_counter()
    timings: dict[str, float] = {}
    stage = "preparar"
    stamp = f"{os.getpid()}_{int(time.time() * 1000)}"
    audio_file = Path(tempfile.gettempdir()) / f"indie88_{stamp}.mp3"

    try:
        stage = "capturar_mp3"
        phase = time.perf_counter()
        sample = capture_stream_mp3(audio_file)
        timings["capture"] = round(time.perf_counter() - phase, 3)

        stage = "shazam"
        phase = time.perf_counter()
        track = asyncio.run(recognize_mp3(audio_file))
        timings["shazam"] = round(time.perf_counter() - phase, 3)
        timings["total"] = round(time.perf_counter() - started, 3)

        if not track:
            return jsonify({
                "ok": False,
                "track": None,
                "stage": "shazam_sem_correspondencia",
                "sample": sample,
                "timings": timings,
                "build": BUILD_ID,
                "error": (
                    "O Shazam recebeu a amostra MP3, mas não reconheceu esta "
                    "parte da emissão. Tenta novamente dentro de alguns segundos."
                ),
            }), 422

        return jsonify({
            "ok": True,
            "track": track,
            "sample": sample,
            "timings": timings,
            "build": BUILD_ID,
        })

    except asyncio.TimeoutError:
        timings["total"] = round(time.perf_counter() - started, 3)
        return jsonify({
            "ok": False,
            "track": None,
            "stage": stage,
            "timings": timings,
            "build": BUILD_ID,
            "error": "O pedido ao Shazam excedeu o tempo disponível.",
        }), 504

    except Exception as exc:
        timings["total"] = round(time.perf_counter() - started, 3)
        return jsonify({
            "ok": False,
            "track": None,
            "stage": stage,
            "timings": timings,
            "build": BUILD_ID,
            "error": f"{type(exc).__name__}: {exc}",
        }), 503

    finally:
        try:
            audio_file.unlink(missing_ok=True)
        except Exception:
            pass


@app.get("/api/sample")
def download_sample():
    """Cria a mesma amostra usada pelo Shazam para ser ouvida no navegador."""
    stamp = f"{os.getpid()}_{int(time.time() * 1000)}"
    audio_file = Path(tempfile.gettempdir()) / f"indie88_test_{stamp}.mp3"

    try:
        capture_stream_mp3(audio_file)

        @after_this_request
        def remove_file(response):
            try:
                audio_file.unlink(missing_ok=True)
            except Exception:
                pass
            return response

        return send_file(
            audio_file,
            mimetype="audio/mpeg",
            as_attachment=True,
            download_name="indie88-amostra-shazam.mp3",
            max_age=0,
        )
    except Exception as exc:
        try:
            audio_file.unlink(missing_ok=True)
        except Exception:
            pass
        return jsonify({
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "build": BUILD_ID,
        }), 503


@app.get("/api/stream-check")
def stream_check():
    response = None
    try:
        response = requests.get(
            STREAM_URL,
            headers=STREAM_HEADERS,
            stream=True,
            timeout=(7, 8),
            allow_redirects=True,
        )
        response.raise_for_status()
        chunk = next(response.iter_content(chunk_size=256), b"")
        return jsonify({
            "ok": bool(chunk),
            "status": response.status_code,
            "content_type": response.headers.get("Content-Type", ""),
            "bytes_received": len(chunk),
            "final_url": response.url,
            "build": BUILD_ID,
        }), (200 if chunk else 502)
    except Exception as exc:
        return jsonify({
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "build": BUILD_ID,
        }), 502
    finally:
        if response is not None:
            response.close()


@app.get("/api/identify-diagnostics")
def identify_diagnostics():
    temp_test = Path(tempfile.gettempdir()) / f"indie88_tmp_test_{os.getpid()}.txt"
    tmp_writable = False
    tmp_error = None

    try:
        temp_test.write_text("ok", encoding="utf-8")
        tmp_writable = temp_test.read_text(encoding="utf-8") == "ok"
    except Exception as exc:
        tmp_error = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            temp_test.unlink(missing_ok=True)
        except Exception:
            pass

    try:
        binary = ffmpeg_path()
        ffmpeg_available = bool(binary and Path(binary).exists())
    except Exception as exc:
        binary = None
        ffmpeg_available = False
        tmp_error = tmp_error or f"FFmpeg: {type(exc).__name__}: {exc}"

    return jsonify({
        "ok": ffmpeg_available and tmp_writable and ffmpeg_supports_mp3(),
        "platform": "vercel" if os.getenv("VERCEL") else "local",
        "tmp_directory": tempfile.gettempdir(),
        "tmp_writable": tmp_writable,
        "tmp_error": tmp_error,
        "ffmpeg_available": ffmpeg_available,
        "ffmpeg_path": binary,
        "mp3_encoder": ffmpeg_supports_mp3(),
        "sample_format": "mp3",
        "capture_seconds": CAPTURE_SECONDS,
        "shazam_segment_seconds": SHAZAM_SEGMENT_SECONDS,
        "mp3_bitrate": MP3_BITRATE,
        "sample_rate": SAMPLE_RATE,
        "channels": AUDIO_CHANNELS,
        "minimum_sample_bytes": MIN_MP3_BYTES,
        "build": BUILD_ID,
    })


@app.get("/api/warmup")
def warmup():
    started = time.perf_counter()
    try:
        binary = ffmpeg_path()
        available = bool(binary and Path(binary).exists())
        return jsonify({
            "ok": available,
            "ffmpeg_available": available,
            "elapsed": round(time.perf_counter() - started, 3),
            "build": BUILD_ID,
        })
    except Exception as exc:
        return jsonify({
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed": round(time.perf_counter() - started, 3),
            "build": BUILD_ID,
        }), 503


@app.get("/api/health")
@app.get("/health")
def health():
    try:
        binary = ffmpeg_path()
        ffmpeg_available = bool(binary and Path(binary).exists())
    except Exception:
        binary = None
        ffmpeg_available = False

    return jsonify({
        "ok": True,
        "platform": "vercel" if os.getenv("VERCEL") else "local",
        "radio": RADIO_NAME,
        "stream": STREAM_URL,
        "identification_sample": "mp3",
        "identification_logic": "same-as-m80-ballads",
        "ffmpeg_available": ffmpeg_available,
        "ffmpeg_path": binary,
        "capture_seconds": CAPTURE_SECONDS,
        "shazam_segment_seconds": SHAZAM_SEGMENT_SECONDS,
        "mp3_bitrate": MP3_BITRATE,
        "sample_rate": SAMPLE_RATE,
        "channels": AUDIO_CHANNELS,
        "tmp_directory": tempfile.gettempdir(),
        "build": BUILD_ID,
    })


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
        threaded=True,
    )
