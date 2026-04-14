import os
import sys
import asyncio
import json
import re
import tempfile
from pathlib import Path
from functools import partial
import yt_dlp
import shutil
import uuid
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters
)

# ─────────────────────────────────────────────────────────────────────────────
# Configurazione — tutto dal .env nella stessa cartella dello script
# ─────────────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

TOKEN             = os.getenv("TELEGRAM_TOKEN")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_SECRET    = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_CACHE     = str(BASE_DIR / ".spotify_cache")
DOWNLOAD_FOLDER   = str(BASE_DIR / "downloads")
SPOTIFY_REDIRECT  = "http://127.0.0.1:8888/callback"

if not TOKEN:
    print("❌ TELEGRAM_TOKEN mancante nel .env")
    sys.exit(1)

if not SPOTIFY_CLIENT_ID or not SPOTIFY_SECRET:
    print("⚠  Credenziali Spotify mancanti nel .env — funzione playlist disabilitata.")

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
BATCHES = {}

# ─────────────────────────────────────────────────────────────────────────────
# FFmpeg
# ─────────────────────────────────────────────────────────────────────────────

# Cerca ffmpeg: prima nella PATH, poi in percorsi noti
FFMPEG_PATH = None
FFMPEG_DIR  = None

_candidates = [
    # Percorso noto dal tuo sistema (rilevato all'avvio precedente)
    r"C:\Users\samue\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.0-full_build\bin\ffmpeg.EXE",
    r"C:\Users\samue\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.0-full_build\bin\ffmpeg.exe",
    r"C:\Users\samue\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
]

# Prima prova shutil.which (funziona se ffmpeg è nella PATH)
_which = shutil.which("ffmpeg")
if _which and os.path.isfile(_which):
    FFMPEG_PATH = _which
else:
    for _p in _candidates:
        if os.path.isfile(_p):
            FFMPEG_PATH = _p
            break

if FFMPEG_PATH:
    FFMPEG_DIR = os.path.dirname(os.path.abspath(FFMPEG_PATH))
    # Aggiunge la cartella ffmpeg alla PATH di sistema così yt-dlp la trova sempre
    # anche con path contenenti spazi (es. OneDrive)
    if FFMPEG_DIR not in os.environ.get("PATH", ""):
        os.environ["PATH"] = FFMPEG_DIR + os.pathsep + os.environ.get("PATH", "")
    print(f"✓ FFmpeg: {FFMPEG_PATH}")
    print(f"✓ FFmpeg dir: {FFMPEG_DIR}")
else:
    print("⚠ FFmpeg non trovato — alcune funzioni non funzioneranno")


# ─────────────────────────────────────────────────────────────────────────────
# SPOTIFY
# ─────────────────────────────────────────────────────────────────────────────

def _get_spotify_client():
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth
    except ImportError:
        raise RuntimeError("spotipy non installato. Esegui: pip install spotipy")

    if not SPOTIFY_CLIENT_ID or not SPOTIFY_SECRET:
        raise RuntimeError(
            "SPOTIFY_CLIENT_ID o SPOTIFY_CLIENT_SECRET mancanti nel .env\n"
            "Aggiungili e riesegui: python setup_spotify.py"
        )

    if not os.path.exists(SPOTIFY_CACHE):
        raise RuntimeError(
            "Token Spotify non trovato.\n"
            "Esegui prima: python setup_spotify.py"
        )

    return spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_SECRET,
        redirect_uri=SPOTIFY_REDIRECT,
        scope="playlist-read-private playlist-read-collaborative",
        cache_path=SPOTIFY_CACHE,
        open_browser=False,
    ))


def _is_spotify_playlist(url: str) -> bool:
    return bool(re.search(r"open\.spotify\.com/playlist/", url))


def _extract_playlist_id(url: str) -> str | None:
    m = re.search(r"playlist/([A-Za-z0-9]+)", url)
    return m.group(1) if m else None


def _get_playlist_tracks(playlist_url: str) -> list[dict]:
    pid = _extract_playlist_id(playlist_url)
    if not pid:
        raise ValueError(f"ID playlist non valido: {playlist_url}")

    sp   = _get_spotify_client()
    resp = sp.playlist_tracks(
        pid,
        fields="total,next,items(track(name,artists(name)))",
        limit=100
    )
    total = resp.get("total", 0)
    print(f"[spotify] Playlist {pid}: {total} brani")

    tracks = []

    def _parse(items):
        for item in items:
            t = item.get("track")
            if not t or not t.get("name"):
                continue
            name   = t["name"]
            artist = ", ".join(a["name"] for a in t.get("artists", []) if a.get("name"))
            tracks.append({"title": name, "artist": artist})

    _parse(resp.get("items", []))

    while resp.get("next"):
        print(f"[spotify] Paginazione {len(tracks)}/{total}...")
        resp = sp.next(resp)
        _parse(resp.get("items", []))

    print(f"[spotify] ✓ {len(tracks)} brani estratti")
    return tracks


# ─────────────────────────────────────────────────────────────────────────────
# YT-DLP
# ─────────────────────────────────────────────────────────────────────────────

def _yt_download_track(title: str, artist: str, output_dir: str,
                       audio_format: str, quality: str) -> str | None:
    query   = f"ytsearch1:{artist} - {title}" if artist else f"ytsearch1:{title}"
    out_tpl = os.path.join(output_dir, "%(title).150s.%(ext)s")

    opts = {
        "format":       "bestaudio/best",
        "outtmpl":      out_tpl,
        "quiet":        True,
        "no_warnings":  True,
        "noplaylist":   True,
        "extractor_args": {"youtube": {"player_client": ["default"]}},
        "postprocessors": [{
            "key":              "FFmpegExtractAudio",
            "preferredcodec":   audio_format,
            "preferredquality": quality,
        }],
    }

    before = set(os.listdir(output_dir))
    print(f"[yt-dlp] {query}")

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([query])
    except Exception as e:
        print(f"[yt-dlp] Errore: {e}")
        return None

    new_files = set(os.listdir(output_dir)) - before
    if not new_files:
        return None

    return max(
        (os.path.join(output_dir, f) for f in new_files),
        key=os.path.getmtime
    )


def _ydl_search(query, limit=5):
    opts = {"quiet": True, "no_warnings": True,
            "extractor_args": {"youtube": {"player_client": ["default"]}}}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        return info.get("entries", [])


def _ydl_download_blocking(url, mode):
    base = {
        "outtmpl":     os.path.join(DOWNLOAD_FOLDER, "%(title).200s.%(ext)s"),
        "quiet":       True,
        "no_warnings": True,
        "noplaylist":  True,
        "extractor_args": {"youtube": {"player_client": ["default"]}},
    }

    if mode == "mp4":
        opts = {**base, "format": "bestvideo+bestaudio/best", "merge_output_format": "mp4"}
    else:
        opts = {**base, "format": "bestaudio/best",
                "postprocessors": [{"key": "FFmpegExtractAudio",
                                    "preferredcodec": "mp3", "preferredquality": "192"}]}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    files = [os.path.join(DOWNLOAD_FOLDER, f)
             for f in os.listdir(DOWNLOAD_FOLDER)
             if os.path.isfile(os.path.join(DOWNLOAD_FOLDER, f))]
    if not files:
        return None, None
    return max(files, key=os.path.getmtime), info.get("title", "video")


# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM handlers
# ─────────────────────────────────────────────────────────────────────────────

async def start(update, context):
    await update.message.reply_text(
        "👋 Ciao! Ecco cosa puoi fare:\n\n"
        "🎵 *Titolo singolo* → cerco su YouTube\n"
        "🎬 *URL YouTube* → scarico nel formato scelto\n"
        "📋 *Più titoli separati da virgola* → batch download\n"
        "🎧 *Link playlist Spotify* → scarico tutta la playlist\n\n"
        "_Esempio: https://open.spotify.com/playlist/..._",
        parse_mode="Markdown"
    )


async def handle_text(update, context):
    text = update.message.text.strip()

    # Playlist Spotify
    if _is_spotify_playlist(text):
        context.user_data["spotify_url"] = text
        keyboard = [
            [InlineKeyboardButton("🎵 MP3 192kbps",     callback_data="SPOT_FMT|mp3|192")],
            [InlineKeyboardButton("🎵 MP3 320kbps",     callback_data="SPOT_FMT|mp3|320")],
            [InlineKeyboardButton("🎶 FLAC (lossless)", callback_data="SPOT_FMT|flac|320")],
        ]
        await update.message.reply_text(
            "🎧 *Playlist Spotify rilevata!*\nScegli il formato:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # URL Spotify track (uno o più incollati come testo)
    if "open.spotify.com/track/" in text:
        urls = [l.strip() for l in text.splitlines()
                if re.search(r"open\.spotify\.com/track/", l.strip())]
        if urls:
            key = uuid.uuid4().hex
            BATCHES[key] = urls
            keyboard = [
                [InlineKeyboardButton(f"🎵 MP3 192kbps — {len(urls)} brani", callback_data=f"SPOTURL|{key}|mp3|192")],
                [InlineKeyboardButton(f"🎵 MP3 320kbps — {len(urls)} brani", callback_data=f"SPOTURL|{key}|mp3|320")],
                [InlineKeyboardButton(f"🎶 FLAC — {len(urls)} brani",        callback_data=f"SPOTURL|{key}|flac|320")],
            ]
            await update.message.reply_text(
                f"🎧 *Trovati {len(urls)} link Spotify!*\nScegli il formato:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

    # URL YouTube singolo
    if text.startswith("http") and "\n" not in text:
        await send_format_menu(update, text)
        return

    # Batch titoli separati da virgola
    if "," in text:
        titles = [t.strip() for t in text.split(",") if t.strip()]
        if not titles:
            await update.message.reply_text("❌ Nessun titolo valido.")
            return
        if len(titles) > 10:
            await update.message.reply_text("⚠ Massimo 10 titoli per batch.")
            return
        key = uuid.uuid4().hex
        BATCHES[key] = titles
        keyboard = [
            [InlineKeyboardButton("🎵 MP3 - batch", callback_data=f"BATCH|{key}|MP3")],
            [InlineKeyboardButton("🎬 MP4 - batch", callback_data=f"BATCH|{key}|MP4")],
        ]
        await update.message.reply_text(
            f"📋 {len(titles)} titoli ricevuti. Scegli il formato:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    await search_youtube(update, text)


async def handle_spotify_playlist(query, playlist_url: str, audio_format: str, bitrate: str):
    tmp_dir = tempfile.mkdtemp(prefix="spotify_", dir=DOWNLOAD_FOLDER)
    loop    = asyncio.get_running_loop()

    status_msg = await query.message.reply_text(
        "📋 *Recupero lista brani da Spotify...*",
        parse_mode="Markdown"
    )

    try:
        tracks = await loop.run_in_executor(None, partial(_get_playlist_tracks, playlist_url))
    except Exception as e:
        await status_msg.edit_text(
            f"❌ *Errore Spotify:*\n`{str(e)[:500]}`\n\n"
            "_Hai eseguito `python setup_spotify.py`?_",
            parse_mode="Markdown"
        )
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return

    total = len(tracks)
    if total == 0:
        await status_msg.edit_text("❌ Playlist vuota o privata.")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return

    await status_msg.edit_text(
        f"✅ *{total} brani trovati!*\n"
        f"▶️ {audio_format.upper()} {bitrate} — invio brano per brano\n"
        f"`{'░' * 10}` 0% — [0/{total}]",
        parse_mode="Markdown"
    )

    downloaded = 0
    failed     = []
    quality    = bitrate.replace("k", "")

    for i, track in enumerate(tracks, start=1):
        title  = track["title"]
        artist = track["artist"]
        label  = f"{artist} - {title}" if artist else title
        pct    = round(i / total * 100)
        bar    = "█" * (pct // 10) + "░" * (10 - pct // 10)

        print(f"[spotify] [{i}/{total}] {label}")

        try:
            await status_msg.edit_text(
                f"🎵 *Download playlist*\n"
                f"`{bar}` {pct}% — [{i}/{total}]\n"
                f"⬇️ _{label[:65]}_",
                parse_mode="Markdown"
            )
        except Exception:
            pass

        file_path = None
        try:
            file_path = await loop.run_in_executor(
                None, partial(_yt_download_track, title, artist, tmp_dir, audio_format, quality)
            )

            if not file_path or not os.path.exists(file_path):
                raise FileNotFoundError("File non creato.")

            size_mb = os.path.getsize(file_path) / (1024 * 1024)
            if size_mb > 49:
                os.remove(file_path)
                raise ValueError(f"Troppo grande ({size_mb:.1f} MB)")

            with open(file_path, "rb") as f:
                await query.message.reply_audio(
                    InputFile(f, filename=os.path.basename(file_path)),
                    title=title, performer=artist,
                )
            os.remove(file_path)
            downloaded += 1

        except Exception as e:
            print(f"[spotify] FAIL {label}: {e}")
            failed.append(f"{label} — {e}")
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception:
                    pass

    shutil.rmtree(tmp_dir, ignore_errors=True)

    lines = [
        "🎉 *Download completato!*",
        f"✅ Inviati: {downloaded}/{total}",
        f"❌ Falliti:  {len(failed)}/{total}",
    ]
    if failed:
        lines.append("\n*Non scaricati:*")
        lines += [f"• {f}" for f in failed[:15]]
        if len(failed) > 15:
            lines.append(f"... e altri {len(failed) - 15}")

    await status_msg.edit_text("\n".join(lines), parse_mode="Markdown")


def _resolve_spotify_track_url(track_url: str) -> dict | None:
    """
    Risolve un URL di brano Spotify usando l'endpoint oEmbed pubblico.
    Non richiede API key né login.
    Ritorna {"title": ..., "artist": ...} oppure None se fallisce.
    """
    import urllib.request
    import urllib.parse
    import gzip

    oembed_url = "https://open.spotify.com/oembed?url=" + urllib.parse.quote(track_url)
    try:
        req = urllib.request.Request(oembed_url, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept-Encoding": "gzip, deflate",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
        data = json.loads(raw.decode())

        # oEmbed restituisce: {"title": "Song Name by Artist Name", ...}
        full_title = data.get("title", "")
        if " by " in full_title:
            parts  = full_title.rsplit(" by ", 1)
            title  = parts[0].strip()
            artist = parts[1].strip()
        else:
            title  = full_title.strip()
            artist = ""
        return {"title": title, "artist": artist}
    except Exception as e:
        print(f"[oembed] Errore per {track_url}: {e}")
        return None


async def handle_document(update, context):
    """
    Accetta un .txt con:
      - URL Spotify per brano  (https://open.spotify.com/track/...)
      - Oppure righe "Artista - Titolo" / "Titolo"
    """
    doc = update.message.document
    fname = doc.file_name or ""
    # Accetta .txt oppure file senza estensione mandati come documento
    if fname and not fname.lower().endswith(".txt") and "." in fname:
        await update.message.reply_text(
            "❌ Invia un file `.txt` con un URL Spotify per riga.\n"
            "Esempio riga: `https://open.spotify.com/track/XXXXX`",
            parse_mode="Markdown"
        )
        return
    if doc.file_size > 500_000:
        await update.message.reply_text("❌ File troppo grande (max 500 KB).")
        return

    tg_file = await doc.get_file()
    raw     = await tg_file.download_as_bytearray()
    lines   = raw.decode("utf-8", errors="replace").splitlines()

    # Filtra righe vuote e commenti
    lines = [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]

    if not lines:
        await update.message.reply_text("❌ Nessun brano trovato nel file.")
        return
    if len(lines) > 500:
        await update.message.reply_text(f"⚠️ Trovate {len(lines)} righe — massimo 500.")
        return

    # Controlla se sono URL Spotify
    spotify_urls = [l for l in lines if re.search(r"open\.spotify\.com/track/", l)]
    is_spotify   = len(spotify_urls) == len(lines)

    if is_spotify:
        # Risolvi gli URL via oEmbed
        resolving_msg = await update.message.reply_text(
            f"🔍 *Risolvo {len(lines)} brani da Spotify...*",
            parse_mode="Markdown"
        )
        loop    = asyncio.get_running_loop()
        entries = []
        failed  = 0

        for i, url in enumerate(lines, 1):
            # Pulizia URL (rimuovi parametri tracking)
            clean = re.sub(r"\?.*", "", url.strip())
            result = await loop.run_in_executor(
                None, partial(_resolve_spotify_track_url, clean)
            )
            if result and result["title"]:
                label = f"{result['artist']} - {result['title']}" if result["artist"] else result["title"]
                entries.append(label)
                print(f"[oembed] [{i}/{len(lines)}] ✓ {label}")
            else:
                failed += 1
                print(f"[oembed] [{i}/{len(lines)}] ✗ {clean}")

            # Aggiorna ogni 10 brani
            if i % 10 == 0:
                try:
                    await resolving_msg.edit_text(
                        f"🔍 *Risolvo brani da Spotify...*\n"
                        f"[{i}/{len(lines)}] ✓ trovati: {len(entries)}",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

        await resolving_msg.edit_text(
            f"✅ *Risolti {len(entries)}/{len(lines)} brani*"
            + (f"\n⚠️ {failed} non trovati" if failed else ""),
            parse_mode="Markdown"
        )

        if not entries:
            await update.message.reply_text("❌ Nessun brano risolto correttamente.")
            return
    else:
        # Righe testuali "Artista - Titolo"
        entries = lines

    key = uuid.uuid4().hex
    BATCHES[key] = entries

    keyboard = [
        [InlineKeyboardButton(f"🎵 MP3 192kbps — {len(entries)} brani", callback_data=f"TXTBATCH|{key}|mp3|192")],
        [InlineKeyboardButton(f"🎵 MP3 320kbps — {len(entries)} brani", callback_data=f"TXTBATCH|{key}|mp3|320")],
        [InlineKeyboardButton(f"🎶 FLAC — {len(entries)} brani",         callback_data=f"TXTBATCH|{key}|flac|320")],
    ]
    await update.message.reply_text(
        f"📄 *Pronti {len(entries)} brani!*\n"
        f"Esempio: `{entries[0][:60]}`\n\nScegli il formato:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


    msg  = await update.message.reply_text(f"🔍 Cerco: {query_text}...")
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, partial(_ydl_search, query_text, 5))
    if not results:
        await msg.reply_text("❌ Nessun risultato.")
        return
    keyboard = [
        [InlineKeyboardButton(f"{i+1}. {r.get('title','')[:60]}",
                              callback_data=f"FMT|{r.get('webpage_url')}")]
        for i, r in enumerate(results)
    ]
    await msg.reply_text("🎶 Scegli la traccia:", reply_markup=InlineKeyboardMarkup(keyboard))


async def send_format_menu(update, url, callback=False):
    keyboard = [
        [InlineKeyboardButton("🎬 MP4 (video HQ)", callback_data=f"MP4|{url}")],
        [InlineKeyboardButton("🎵 MP3 (audio HQ)", callback_data=f"MP3|{url}")],
    ]
    if callback:
        await update.callback_query.edit_message_text("📥 Scegli il formato:", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text("📥 Scegli il formato:", reply_markup=InlineKeyboardMarkup(keyboard))


async def download_and_get_path(url, mode):
    return await asyncio.get_running_loop().run_in_executor(
        None, partial(_ydl_download_blocking, url, mode)
    )


async def callback_handler(update, context):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data.startswith("SPOTURL|"):
        # URL Spotify track incollati come testo → risolvi via oEmbed poi scarica
        _, key, audio_format, bitrate = data.split("|")
        urls = BATCHES.pop(key, None)
        if not urls:
            await q.message.reply_text("❌ Sessione scaduta. Incolla di nuovo i link.")
            return

        total = len(urls)
        resolving_msg = await q.message.reply_text(
            f"🔍 *Risolvo {total} brani da Spotify...*",
            parse_mode="Markdown"
        )
        loop    = asyncio.get_running_loop()
        entries = []
        failed_resolve = 0

        for i, url in enumerate(urls, 1):
            clean  = re.sub(r"\?.*", "", url.strip())
            result = await loop.run_in_executor(
                None, partial(_resolve_spotify_track_url, clean)
            )
            if result and result["title"]:
                label = f"{result['artist']} - {result['title']}" if result["artist"] else result["title"]
                entries.append(label)
            else:
                failed_resolve += 1
            if i % 10 == 0 or i == total:
                try:
                    await resolving_msg.edit_text(
                        f"🔍 *Risolvo brani...* [{i}/{total}]\n✓ trovati: {len(entries)}",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

        if not entries:
            await resolving_msg.edit_text("❌ Nessun brano risolto. Riprova.")
            return

        await resolving_msg.edit_text(
            f"✅ *Risolti {len(entries)}/{total}*" +
            (f"\n⚠️ {failed_resolve} non trovati" if failed_resolve else ""),
            parse_mode="Markdown"
        )

        # Ora scarica esattamente come TXTBATCH
        tmp_dir    = tempfile.mkdtemp(prefix="spoturl_", dir=DOWNLOAD_FOLDER)
        downloaded = 0
        failed_dl  = []
        quality    = bitrate.replace("k", "")

        status_msg = await q.message.reply_text(
            f"`{'░' * 10}` 0% — [0/{len(entries)}]", parse_mode="Markdown"
        )

        for i, entry in enumerate(entries, start=1):
            pct = round(i / len(entries) * 100)
            bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
            parts = re.split(r"\s[-–]\s", entry, maxsplit=1)
            artist, title = (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else ("", entry.strip())
            label = f"{artist} - {title}" if artist else title

            try:
                await status_msg.edit_text(
                    f"`{bar}` {pct}% — [{i}/{len(entries)}]\n⬇️ _{label[:65]}_",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

            file_path = None
            try:
                file_path = await loop.run_in_executor(
                    None, partial(_yt_download_track, title, artist, tmp_dir, audio_format, quality)
                )
                if not file_path or not os.path.exists(file_path):
                    raise FileNotFoundError("File non creato.")
                size_mb = os.path.getsize(file_path) / (1024 * 1024)
                if size_mb > 49:
                    os.remove(file_path)
                    raise ValueError(f"Troppo grande ({size_mb:.1f} MB)")
                with open(file_path, "rb") as f:
                    await q.message.reply_audio(
                        InputFile(f, filename=os.path.basename(file_path)),
                        title=title, performer=artist,
                    )
                os.remove(file_path)
                downloaded += 1
            except Exception as e:
                failed_dl.append(f"{label} — {e}")
                if file_path and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except Exception:
                        pass

        shutil.rmtree(tmp_dir, ignore_errors=True)
        lines = ["🎉 *Download completato!*", f"✅ Inviati: {downloaded}/{len(entries)}", f"❌ Falliti: {len(failed_dl)}/{len(entries)}"]
        if failed_dl:
            lines += ["\n*Non scaricati:*"] + [f"• {f}" for f in failed_dl[:15]]
        await status_msg.edit_text("\n".join(lines), parse_mode="Markdown")
        return

    if data.startswith("TXTBATCH|"):
        _, key, audio_format, bitrate = data.split("|")
        entries = BATCHES.pop(key, None)
        if not entries:
            await q.message.reply_text("❌ Sessione scaduta. Invia di nuovo il file.")
            return

        total = len(entries)
        await q.edit_message_text(
            f"▶️ *Avvio download {total} brani in {audio_format.upper()} {bitrate}kbps...*\n"
            f"_(ogni brano viene inviato e cancellato subito)_",
            parse_mode="Markdown"
        )

        tmp_dir    = tempfile.mkdtemp(prefix="txtbatch_", dir=DOWNLOAD_FOLDER)
        loop       = asyncio.get_running_loop()
        downloaded = 0
        failed     = []
        quality    = bitrate.replace("k", "")

        status_msg = await q.message.reply_text(
            f"`{'░' * 10}` 0% — [0/{total}]", parse_mode="Markdown"
        )

        for i, entry in enumerate(entries, start=1):
            pct = round(i / total * 100)
            bar = "█" * (pct // 10) + "░" * (10 - pct // 10)

            # Prova a dividere "Artista - Titolo" o "Artista – Titolo"
            parts  = re.split(r"\s[-–]\s", entry, maxsplit=1)
            if len(parts) == 2:
                artist, title = parts[0].strip(), parts[1].strip()
            else:
                artist, title = "", entry.strip()

            label = f"{artist} - {title}" if artist else title
            print(f"[txtbatch] [{i}/{total}] {label}")

            try:
                await status_msg.edit_text(
                    f"`{bar}` {pct}% — [{i}/{total}]\n⬇️ _{label[:65]}_",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

            file_path = None
            try:
                file_path = await loop.run_in_executor(
                    None, partial(_yt_download_track, title, artist, tmp_dir, audio_format, quality)
                )
                if not file_path or not os.path.exists(file_path):
                    raise FileNotFoundError("File non creato.")

                size_mb = os.path.getsize(file_path) / (1024 * 1024)
                if size_mb > 49:
                    os.remove(file_path)
                    raise ValueError(f"Troppo grande ({size_mb:.1f} MB)")

                with open(file_path, "rb") as f:
                    await q.message.reply_audio(
                        InputFile(f, filename=os.path.basename(file_path)),
                        title=title, performer=artist,
                    )
                os.remove(file_path)
                downloaded += 1

            except Exception as e:
                print(f"[txtbatch] FAIL {label}: {e}")
                failed.append(f"{label} — {e}")
                if file_path and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except Exception:
                        pass

        shutil.rmtree(tmp_dir, ignore_errors=True)

        lines = [
            "🎉 *Download completato!*",
            f"✅ Inviati: {downloaded}/{total}",
            f"❌ Falliti:  {len(failed)}/{total}",
        ]
        if failed:
            lines.append("\n*Non scaricati:*")
            lines += [f"• {f}" for f in failed[:15]]
            if len(failed) > 15:
                lines.append(f"... e altri {len(failed) - 15}")
        await status_msg.edit_text("\n".join(lines), parse_mode="Markdown")
        return


        _, audio_format, bitrate = data.split("|")
        playlist_url = context.user_data.pop("spotify_url", None)
        if not playlist_url:
            await q.message.reply_text("❌ URL non trovato. Invia di nuovo il link.")
            return
        label = f"{audio_format.upper()} {bitrate}kbps" if audio_format == "mp3" else audio_format.upper()
        await q.edit_message_text(f"▶️ *Avvio download in {label}...*", parse_mode="Markdown")
        await handle_spotify_playlist(q, playlist_url, audio_format, f"{bitrate}k")
        return

    if data.startswith("BATCH|"):
        _, key, mode = data.split("|", 2)
        titles = BATCHES.pop(key, None)
        if not titles:
            await q.message.reply_text("❌ Batch scaduto.")
            return
        await q.message.reply_text(f"⏳ Download {len(titles)} brani in {mode}...")
        loop = asyncio.get_running_loop()
        for t in titles:
            try:
                results = await loop.run_in_executor(None, partial(_ydl_search, t, 1))
                if not results:
                    await q.message.reply_text(f"❌ Non trovato: {t}")
                    continue
                path, _ = await download_and_get_path(results[0]["webpage_url"], mode.lower())
                if not path:
                    await q.message.reply_text(f"❌ Errore download: {t}")
                    continue
                if mode == "MP4":
                    await q.message.reply_video(InputFile(open(path, "rb"), filename=os.path.basename(path)))
                else:
                    await q.message.reply_audio(InputFile(open(path, "rb"), filename=os.path.basename(path)))
                os.remove(path)
            except Exception as e:
                await q.message.reply_text(f"❌ Errore '{t}': {e}")
        await q.message.reply_text("🎉 Batch completato!")
        return

    if data.startswith("FMT|"):
        await send_format_menu(update, data.split("|", 1)[1], callback=True)
        return

    if data.startswith("MP4|") or data.startswith("MP3|"):
        mode, url = data.split("|", 1)
        await q.message.reply_text("⏳ Download in corso...")
        try:
            path, _ = await download_and_get_path(url, mode.lower())
            if not path:
                await q.message.reply_text("❌ Errore nel download.")
                return
            if mode == "MP4":
                await q.message.reply_video(InputFile(open(path, "rb"), filename=os.path.basename(path)))
            else:
                await q.message.reply_audio(InputFile(open(path, "rb"), filename=os.path.basename(path)))
            await q.message.reply_text("✅ Completato!")
            os.remove(path)
        except Exception as e:
            await q.message.reply_text(f"❌ Errore: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(callback_handler))
    print("Bot avviato...")
    app.run_polling()


if __name__ == "__main__":
    main()