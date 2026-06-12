import html
import os
import re
import time
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from threading import Lock
from typing import Any

import requests
from flask import Flask, jsonify, render_template, send_from_directory


BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=None,
)

BUILD_ID = "indie88-vercel-v9-metadata-oficial-20260612"
RADIO_NAME = os.getenv("RADIO_NAME", "Radio Indie88 FM").strip()
STREAM_URL = os.getenv(
    "RADIO_STREAM",
    "https://localradio.streamb.live/SB00348",
).strip()
OFFICIAL_PLAYER_URL = os.getenv(
    "INDIE88_PLAYER_URL",
    "https://www.indie88.com/player/",
).strip()
DEFAULT_COVER = "/default-cover.webp"

METADATA_CACHE_SECONDS = 20
STALE_CACHE_SECONDS = 180

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/149.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-CA,en;q=0.9,pt-PT;q=0.7,pt;q=0.6",
    "Accept-Encoding": "identity",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

metadata_lock = Lock()
metadata_cache: dict[str, Any] = {
    "ts": 0.0,
    "track": None,
}


class VisibleTextParser(HTMLParser):
    """Extrai texto visível sem depender de BeautifulSoup."""

    IGNORED_TAGS = {"script", "style", "noscript", "svg", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self.items: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in self.IGNORED_TAGS:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.IGNORED_TAGS and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        value = re.sub(r"\s+", " ", html.unescape(data or "")).strip()
        if value:
            self.items.append(value)


TIME_PATTERN = re.compile(r"^(?:0?[1-9]|1[0-2]):[0-5]\d\s*(?:AM|PM)$", re.I)
SKIP_TEXTS = {
    "play",
    "stop",
    "image",
    "facebook",
    "instagram",
    "youtube",
    "tiktok",
    "privacy policy",
    "terms of service",
    "indie 88 website",
    "indie88 website",
}


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_search_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"[^a-zA-Z0-9]+", " ", value).lower()
    return " ".join(value.split())


def is_song_text_candidate(value: str) -> bool:
    value = normalize_space(value)
    lowered = value.lower()

    if not value or len(value) > 160:
        return False
    if TIME_PATTERN.fullmatch(value):
        return False
    if lowered in SKIP_TEXTS:
        return False
    if lowered.startswith(("http://", "https://", "copyright", "your program will resume")):
        return False
    if lowered in {"indie 88", "indie88", "radio indie88 fm"}:
        return False
    if re.fullmatch(r"[\W_]+", value):
        return False

    return True


def parse_official_player(page_html: str) -> dict[str, Any]:
    parser = VisibleTextParser()
    parser.feed(page_html)
    tokens = [normalize_space(item) for item in parser.items if normalize_space(item)]

    # O player oficial apresenta cada registo na ordem:
    # hora -> artista -> título. O primeiro registo é a música atual.
    for index, token in enumerate(tokens):
        if not TIME_PATTERN.fullmatch(token):
            continue

        candidates: list[str] = []
        for next_token in tokens[index + 1:index + 12]:
            if is_song_text_candidate(next_token):
                candidates.append(next_token)
            if len(candidates) == 2:
                break

        if len(candidates) == 2:
            artist, title = candidates
            return {
                "title": title,
                "artist": artist,
                "album": "",
                "cover": DEFAULT_COVER,
                "official_time": token.upper(),
                "source": "indie88-official-player",
                "identified_at": int(time.time()),
                "played_at": int(time.time()),
            }

    raise RuntimeError(
        "A página oficial abriu, mas não foi possível localizar artista e título."
    )


def artwork_600(url: str) -> str:
    if not url:
        return ""
    return re.sub(r"\d+x\d+bb", "600x600bb", url)


def get_itunes_data(title: str, artist: str) -> tuple[str, str]:
    """Procura capa e álbum sem bloquear a identificação em caso de falha."""
    try:
        response = requests.get(
            "https://itunes.apple.com/search",
            params={
                "term": f"{artist} {title}",
                "media": "music",
                "entity": "song",
                "limit": 8,
                "country": "CA",
            },
            headers={"User-Agent": BROWSER_HEADERS["User-Agent"]},
            timeout=(3.5, 6),
        )
        response.raise_for_status()
        results = response.json().get("results") or []
    except Exception:
        return "", ""

    wanted_title = normalize_search_text(title)
    wanted_artist = normalize_search_text(artist)
    best_item: dict[str, Any] | None = None
    best_score = -1

    for item in results:
        if not isinstance(item, dict):
            continue

        found_title = normalize_search_text(str(item.get("trackName") or ""))
        found_artist = normalize_search_text(str(item.get("artistName") or ""))
        score = 0

        if found_title == wanted_title:
            score += 8
        elif wanted_title and (wanted_title in found_title or found_title in wanted_title):
            score += 4

        if found_artist == wanted_artist:
            score += 8
        elif wanted_artist and (wanted_artist in found_artist or found_artist in wanted_artist):
            score += 4

        if score > best_score:
            best_score = score
            best_item = item

    if not best_item or best_score < 4:
        return "", ""

    cover = artwork_600(str(best_item.get("artworkUrl100") or ""))
    album = str(best_item.get("collectionName") or "").strip()
    return cover, album


def fetch_official_now_playing(force: bool = False) -> dict[str, Any]:
    global metadata_cache

    now = time.time()
    cached_track = metadata_cache.get("track")
    cached_ts = float(metadata_cache.get("ts") or 0)

    if not force and cached_track and now - cached_ts < METADATA_CACHE_SECONDS:
        return dict(cached_track)

    with metadata_lock:
        now = time.time()
        cached_track = metadata_cache.get("track")
        cached_ts = float(metadata_cache.get("ts") or 0)

        if not force and cached_track and now - cached_ts < METADATA_CACHE_SECONDS:
            return dict(cached_track)

        try:
            response = requests.get(
                OFFICIAL_PLAYER_URL,
                params={"playerID": "3446", "_": int(now)},
                headers=BROWSER_HEADERS,
                timeout=(5, 12),
                allow_redirects=True,
            )
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "")
            if "html" not in content_type.lower() and not response.text.lstrip().startswith("<"):
                raise RuntimeError(
                    f"A página oficial devolveu um conteúdo inesperado: {content_type or 'desconhecido'}."
                )

            track = parse_official_player(response.text)
            cover, album = get_itunes_data(track["title"], track["artist"])
            track["cover"] = cover or DEFAULT_COVER
            track["album"] = album
            track["official_url"] = OFFICIAL_PLAYER_URL
            track["stale"] = False

            metadata_cache = {
                "ts": time.time(),
                "track": dict(track),
            }
            return track

        except Exception:
            # Se a página oficial tiver uma falha momentânea, mantém a última
            # música confirmada durante três minutos em vez de apagar o player.
            if cached_track and now - cached_ts < STALE_CACHE_SECONDS:
                stale_track = dict(cached_track)
                stale_track["stale"] = True
                stale_track["source"] = "indie88-official-player-cache"
                return stale_track
            raise


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
            "identifySeconds": 0,
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


@app.route("/api/identify", methods=["GET", "POST"])
@app.route("/api/identify/force", methods=["GET", "POST"])
def identify():
    started = time.perf_counter()

    try:
        track = fetch_official_now_playing(force=True)
        elapsed = round(time.perf_counter() - started, 3)
        return jsonify({
            "ok": True,
            "track": track,
            "stage": "official_now_playing",
            "source": track.get("source"),
            "timings": {"total": elapsed},
            "build": BUILD_ID,
        })
    except Exception as exc:
        elapsed = round(time.perf_counter() - started, 3)
        return jsonify({
            "ok": False,
            "track": None,
            "stage": "official_now_playing",
            "timings": {"total": elapsed},
            "build": BUILD_ID,
            "error": f"{type(exc).__name__}: {exc}",
        }), 503


@app.get("/api/official-now-playing")
def official_now_playing():
    try:
        track = fetch_official_now_playing(force=True)
        return jsonify({
            "ok": True,
            "track": track,
            "build": BUILD_ID,
        })
    except Exception as exc:
        return jsonify({
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "build": BUILD_ID,
        }), 503


@app.get("/api/sample")
def sample_disabled():
    return jsonify({
        "ok": False,
        "disabled": True,
        "reason": (
            "A amostra MP3 foi desativada porque o servidor do stream entrega "
            "uma mensagem de indisponibilidade ao Vercel em vez da música."
        ),
        "identification_mode": "official-now-playing-metadata",
        "build": BUILD_ID,
    }), 410


@app.get("/api/stream-check")
def stream_check():
    """Verifica apenas transporte; não afirma que o conteúdo seja música."""
    response = None
    try:
        response = requests.get(
            STREAM_URL,
            headers={
                "User-Agent": BROWSER_HEADERS["User-Agent"],
                "Accept": "audio/aac,audio/mpeg,audio/*;q=0.9,*/*;q=0.8",
                "Accept-Encoding": "identity",
                "Icy-MetaData": "0",
                "Cache-Control": "no-cache",
            },
            stream=True,
            timeout=(7, 8),
            allow_redirects=True,
        )
        response.raise_for_status()
        chunk = next(response.iter_content(chunk_size=512), b"")
        return jsonify({
            "ok": bool(chunk),
            "transport_only": True,
            "warning": (
                "O servidor pode devolver áudio de indisponibilidade; este teste "
                "não confirma que o conteúdo seja a emissão musical."
            ),
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


@app.get("/api/health")
@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "platform": "vercel" if os.getenv("VERCEL") else "local",
        "radio": RADIO_NAME,
        "stream": STREAM_URL,
        "official_player": OFFICIAL_PLAYER_URL,
        "identification_mode": "official-now-playing-metadata",
        "ffmpeg_required": False,
        "shazam_required": False,
        "metadata_cache_seconds": METADATA_CACHE_SECONDS,
        "build": BUILD_ID,
    })


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
        threaded=True,
    )
