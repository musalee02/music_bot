import os
import asyncio
import re
import json

from functools import partial
from pathlib import Path
from dotenv import load_dotenv

import yt_dlp
import shutil
import uuid
import requests

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters
)

# Carica variabili d'ambiente dal file .env
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# ════════════════════════════════════════════════════════════════════════════
#  CONFIGURAZIONE
# ════════════════════════════════════════════════════════════════════════════
TOKEN = os.getenv("TELEGRAM_TOKEN")
# ════════════════════════════════════════════════════════════════════════════

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

BATCHES         = {}   # uuid -> list[str]

# ── FFmpeg ───────────────────────────────────────────────────────────────────
FFMPEG_PATH = shutil.which("ffmpeg")
if not FFMPEG_PATH:
    possible_paths = [
        r"C:\Users\samue\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.0-full_build\bin\ffmpeg.exe",
        r"C:\Users\samue\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    ]
    for path in possible_paths:
        if os.path.exists(path):
            FFMPEG_PATH = path
            break

print(f"✓ FFmpeg: {FFMPEG_PATH}" if FFMPEG_PATH else "⚠ FFmpeg non trovato")


def resolve_spotify_links(text: str) -> list[str]:
    """
    Estrae gli URL dal testo e usa l'API pubblica oEmbed di Spotify
    per ottenere Titolo e Artista senza chiavi di accesso.
    """
    # Trova tutti gli URL nel testo incollato
    urls = re.findall(r'(https?://[^\s]+)', text)
    
    queries = []
    for url in urls:
        if "spotify" not in url.lower():
            continue
            
        try:
            # Endpoint oEmbed pubblico
            api_url = f"https://open.spotify.com/oembed?url={url}"
            resp = requests.get(api_url, timeout=5)
            
            if resp.status_code == 200:
                data = resp.json()
                title = data.get("title", "")
                artist = data.get("author_name", "")
                
                if title:
                    # Crea la query perfetta per yt-dlp
                    query = f"{title} - {artist}".strip(" -")
                    queries.append(query)
        except Exception as e:
            print(f"Errore oEmbed per {url}: {e}")
            
    # Rimuove duplicati preservando l'ordine
    return list(dict.fromkeys(queries))


# ─────────────────────────────────────────────────────────────────────────────
#  Heuristica di estrazione dal Copia-Incolla (Bypass Spotify)
# ─────────────────────────────────────────────────────────────────────────────
def extract_tracks_from_clipboard(text: str) -> list[str]:
    """
    Pulisce un muro di testo copiato da Spotify e ricostruisce query di ricerca valide.
    """
    lines = text.split("\n")
    cleaned = []
    
    # Parole e stringhe UI da ignorare
    blacklist = {
        "titolo", "album", "data di aggiunta", "durata", "riproduci",
        "salva", "brano", "title", "date added", "duration", "play",
        "save", "options", "opzioni", "artisti", "artist", "#"
    }
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        line_lower = line.lower()
        if line_lower in blacklist:
            continue
        if line.isdigit():  # Esclude numeri di traccia
            continue
        if re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", line):  # Esclude pattern durata (es. 3:45)
            continue
        if re.match(r"^\d{1,2}\s+[a-zA-Z]+\s+\d{4}$", line): # Esclude date (es. 12 gen 2024)
            continue
        if line.startswith("http"): # Ignora link crudi
            continue
            
        cleaned.append(line)
        
    # Accorpiamo a due a due (Titolo + Artista) per query più precise
    queries = []
    for i in range(0, len(cleaned), 2):
        if i + 1 < len(cleaned):
            queries.append(f"{cleaned[i]} - {cleaned[i+1]}")
        else:
            queries.append(cleaned[i])
            
    # Rimuoviamo eventuali duplicati preservando l'ordine
    return list(dict.fromkeys(queries))


# ─────────────────────────────────────────────────────────────────────────────
#  /start
# ─────────────────────────────────────────────────────────────────────────────
async def start(update, context):
    await update.message.reply_text(
        "👋 Ciao! Puoi mandarmi:\n\n"
        "• Un *URL YouTube* → scelgo il formato e scarico\n"
        "• Un *titolo* → cerco su YouTube\n"
        "• *Più titoli separati da virgola* → download batch\n"
        "• *Incolla il testo di una playlist Spotify* → copia i brani dall'app e incollali qui per scaricarli tutti 🎧\n",
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Gestione messaggi in entrata
# ─────────────────────────────────────────────────────────────────────────────
async def handle_text(update, context):
    text = update.message.text.strip()

    # 1. Link YouTube diretto
    if text.startswith("http") and "spotify" not in text:
        await send_format_menu(update, text)
        return

    # 2. Copia-Incolla (Lista di URL o testo multiriga)
    if "\n" in text and "http" in text:
        msg = await update.message.reply_text("⏳ Analizzo i link tramite oEmbed...")
        
        loop = asyncio.get_running_loop()
        titles = await loop.run_in_executor(None, resolve_spotify_links, text)
        
        if not titles:
            await msg.edit_text("❌ Non sono riuscito a estrarre i brani dai link forniti.")
            return

        MAX_BATCH = 180
        troncato = len(titles) > MAX_BATCH
        if troncato:
            titles = titles[:MAX_BATCH]

        preview = "\n".join(f"• {t}" for t in titles[:5])
        extra   = f"\n_... e altri {len(titles)-5}_" if len(titles) > 5 else ""
        avviso  = f"\n⚠ Ho troncato ai primi {MAX_BATCH} brani per evitare blocchi." if troncato else ""
        
        await msg.edit_text(
            f"✅ *{len(titles)} brani* riconosciuti:{avviso}\n\n"
            f"{preview}{extra}\n\n🎧 *Avvio il download audio a 320kbps in automatico...*",
            parse_mode="Markdown",
        )
        
        # Parte il download senza dover cliccare nulla
        await _process_track_list(update.message, titles)
        await update.message.reply_text("🎉 Playlist HQ scaricata con successo!")
        return

    # 3. Batch virgola classico
    if "," in text:
        titles = [t.strip() for t in text.split(",") if t.strip()]
        if not titles:
            await update.message.reply_text("❌ Nessun titolo valido.")
            return

        MAX_BATCH = 10
        if len(titles) > MAX_BATCH:
            await update.message.reply_text(f"⚠ Massimo {MAX_BATCH} titoli per batch. Hai inviato {len(titles)}.")
            return

        key = uuid.uuid4().hex
        BATCHES[key] = titles

        keyboard = [
            [InlineKeyboardButton("🎵 MP3 - batch", callback_data=f"BATCH|{key}|MP3")],
            [InlineKeyboardButton("🎬 MP4 - batch", callback_data=f"BATCH|{key}|MP4")],
        ]
        await update.message.reply_text(
            f"Ho ricevuto {len(titles)} titoli. Scegli il formato:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    # 4. Ricerca singola
    await search_youtube(update, text)


# ─────────────────────────────────────────────────────────────────────────────
#  YouTube search / download
# ─────────────────────────────────────────────────────────────────────────────
async def search_youtube(update, query):
    msg = await update.message.reply_text(f"🔍 Cerco: {query} ...")
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, partial(ydl_search, query, 5))

    if not results:
        await msg.reply_text("❌ Nessun risultato trovato.")
        return

    keyboard = [
        [InlineKeyboardButton(f"{i+1}. {r.get('title','')[:60]}", callback_data=f"FMT|{r.get('webpage_url')}")]
        for i, r in enumerate(results)
    ]
    await msg.reply_text(
        "🎶 Risultati — tocca per scegliere:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


def ydl_search(query, limit=5):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extractor_args": {"youtube": {"player_client": ["default"]}},
    }
    if FFMPEG_PATH:
        opts["ffmpeg_location"] = os.path.dirname(FFMPEG_PATH)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        return info.get("entries", [])


async def send_format_menu(update, url, callback=False):
    keyboard = [
        [InlineKeyboardButton("🎬 MP4 (video HQ)", callback_data=f"MP4|{url}")],
        [InlineKeyboardButton("🎵 MP3 (audio HQ)", callback_data=f"MP3|{url}")],
    ]
    if callback:
        await update.callback_query.edit_message_text(
            "📥 Scegli il formato:", reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text(
            "📥 Scegli il formato:", reply_markup=InlineKeyboardMarkup(keyboard)
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Helper condiviso: processa lista di brani
# ─────────────────────────────────────────────────────────────────────────────
import zipfile

async def _process_track_list(message, titles: list[str]):
    # Crea una directory isolata per questa playlist
    batch_id = uuid.uuid4().hex
    batch_dir = os.path.join(DOWNLOAD_FOLDER, batch_id)
    os.makedirs(batch_dir, exist_ok=True)
    
    downloaded_files = []
    failed_tracks = []
    
    # Messaggio di progress unico che sarà aggiornato
    progress_msg = await message.reply_text(f"⏳ Inizio download di {len(titles)} brani...\n0/{len(titles)} completati")

    # Aggiorna il messaggio solo ogni UPDATE_EVERY brani (o sull'ultimo)
    # per evitare il rate limit di Telegram (~30 edit/min)
    UPDATE_EVERY = 5

    # 1. Download di tutti i brani
    for idx, t in enumerate(titles, 1):
        try:
            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(None, partial(ydl_search, t, 1))

            if not results:
                failed_tracks.append(t)
                await asyncio.sleep(0.5)  # Rate limiting
                continue

            url = results[0].get("webpage_url")
            found_title = results[0].get("title", t)
            
            # Passiamo la directory isolata
            path, _ = await download_and_get_path(url, batch_dir)
            if not path:
                failed_tracks.append(found_title)
                await asyncio.sleep(0.5)
                continue

            downloaded_files.append(path)
            
            # Aggiorna progress solo ogni N brani o sull'ultimo
            if idx % UPDATE_EVERY == 0 or idx == len(titles):
                try:
                    await progress_msg.edit_text(
                        f"⏳ Download in corso...\n"
                        f"{idx}/{len(titles)} completati "
                        f"({len(downloaded_files)} ✅ · {len(failed_tracks)} ❌)\n"
                        f"🎵 Ultimo: {found_title[:50]}",
                    )
                except Exception:
                    pass  # Ignora errori di edit (es. messaggio identico)
            
            # Rate limiting tra i download
            if idx < len(titles):
                await asyncio.sleep(1)

        except Exception as e:
            failed_tracks.append(f"{t} ({str(e)[:30]})")
            await asyncio.sleep(0.5)

    if not downloaded_files:
        await progress_msg.edit_text("❌ Nessun brano scaricato con successo.")
        shutil.rmtree(batch_dir, ignore_errors=True)
        return

    # 2. Creazione degli archivi ZIP (Gestione limite 50MB di Telegram)
    await progress_msg.edit_text("📦 Compressione file in corso...")
    
    MAX_ZIP_SIZE = 48 * 1024 * 1024  # 48 MB di margine di sicurezza
    zip_parts = []
    current_zip_idx = 1
    current_zip_size = 0
    
    current_zip_path = os.path.join(DOWNLOAD_FOLDER, f"Playlist_{batch_id}_Part{current_zip_idx}.zip")
    current_zip = zipfile.ZipFile(current_zip_path, 'w', zipfile.ZIP_DEFLATED)
    zip_parts.append(current_zip_path)

    for file_path in downloaded_files:
        file_size = os.path.getsize(file_path)
        
        # Se l'aggiunta di questo MP3 supera i 48MB, chiudi lo ZIP corrente e aprine uno nuovo
        if current_zip_size + file_size > MAX_ZIP_SIZE and current_zip_size > 0:
            current_zip.close()
            current_zip_idx += 1
            current_zip_path = os.path.join(DOWNLOAD_FOLDER, f"Playlist_{batch_id}_Part{current_zip_idx}.zip")
            current_zip = zipfile.ZipFile(current_zip_path, 'w', zipfile.ZIP_DEFLATED)
            zip_parts.append(current_zip_path)
            current_zip_size = 0
            
        current_zip.write(file_path, os.path.basename(file_path))
        current_zip_size += file_size
        
    current_zip.close()

    # 3. Invio dei file e Cleanup
    for i, z_path in enumerate(zip_parts):
        await progress_msg.edit_text(f"🚀 Invio archivio {i+1}/{len(zip_parts)}...")
        try:
            with open(z_path, "rb") as doc:
                await message.reply_document(document=InputFile(doc, filename=os.path.basename(z_path)))
        except Exception as e:
            await message.reply_text(f"❌ Errore durante l'invio dell'archivio {i+1}: {e}")
        finally:
            # Elimina l'archivio inviato
            if os.path.exists(z_path):
                os.remove(z_path)

    # Distruzione della directory temporanea con i file MP3
    shutil.rmtree(batch_dir, ignore_errors=True)
    
    # Resoconto finale
    summary = f"🎉 Playlist consegnata!\n✅ {len(downloaded_files)} brani scaricati"
    if failed_tracks:
        summary += f"\n⚠ {len(failed_tracks)} non scaricabili"
    summary += "\nTracce sul server eliminate."
    await progress_msg.edit_text(summary)

    
# ─────────────────────────────────────────────────────────────────────────────
#  Callback handler
# ─────────────────────────────────────────────────────────────────────────────
async def callback_handler(update, context):
    q = update.callback_query
    await q.answer()
    data = q.data

    # Batch generico / Testuale Spotify
    if data.startswith("BATCH|"):
        _, key, mode = data.split("|", 2)
        titles = BATCHES.pop(key, None)
        if not titles:
            await q.message.reply_text("❌ Batch scaduto.")
            return
        await q.message.reply_text(f"⏳ Download di {len(titles)} brani in {mode}...")
        await _process_track_list(q.message, titles, mode)
        await q.message.reply_text("🎉 Batch completato!")
        return

    # Selezione risultato ricerca
    if data.startswith("FMT|"):
        url = data.split("|", 1)[1]
        await send_format_menu(update, url, callback=True)
        return

    # Download singolo
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
        except Exception as e:
            await q.message.reply_text(f"❌ Errore: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  Download con yt-dlp
# ─────────────────────────────────────────────────────────────────────────────
async def download_and_get_path(url, target_dir):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(ydl_download_blocking, url, target_dir))

def ydl_download_blocking(url, target_dir):
    ydl_opts = {
        "outtmpl": os.path.join(target_dir, "%(title).200s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "extractor_args": {"youtube": {"player_client": ["default"]}},
        "format": "bestaudio/best",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320"
            }
        ],
    }
    if FFMPEG_PATH:
        ydl_opts["ffmpeg_location"] = os.path.dirname(FFMPEG_PATH)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    # Cerca il file appena scaricato nella directory target
    files = [os.path.join(target_dir, f) for f in os.listdir(target_dir) if os.path.isfile(os.path.join(target_dir, f))]
    if not files:
        return None, None
    return max(files, key=os.path.getmtime), info.get("title", "audio")

# ─────────────────────────────────────────────────────────────────────────────
#  Avvio
# ─────────────────────────────────────────────────────────────────────────────
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(callback_handler))
    print("Bot avviato...")
    app.run_polling()

if __name__ == "__main__":
    main()