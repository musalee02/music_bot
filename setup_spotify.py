"""
SETUP SPOTIFY — esegui UNA VOLTA SOLA prima di avviare il bot.

Legge le credenziali dal .env nella stessa cartella di questo script,
apre il browser per il login e salva il token in .spotify_cache.

Prerequisiti:
    pip install spotipy python-dotenv

Nel Spotify Dashboard della tua app imposta il Redirect URI:
    http://127.0.0.1:8888/callback
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Carica .env dalla cartella dello script (funziona da qualsiasi working dir)
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

CLIENT_ID     = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    print("❌ SPOTIFY_CLIENT_ID o SPOTIFY_CLIENT_SECRET mancanti nel .env")
    print(f"   Cercato in: {BASE_DIR / '.env'}")
    sys.exit(1)

CACHE_PATH = str(BASE_DIR / ".spotify_cache")
REDIRECT   = "https://127.0.0.1:8888/callback"

print("=" * 55)
print("  SETUP SPOTIFY per Music Bot")
print("=" * 55)
print(f"  Client ID:    {CLIENT_ID[:8]}...")
print(f"  Cache path:   {CACHE_PATH}")
print(f"  Redirect URI: {REDIRECT}")
print()
print("⚠️  Assicurati che nel Spotify Dashboard la tua app abbia:")
print(f"   Redirect URI → {REDIRECT}")
print()
print("🌐 Apro il browser per il login Spotify...")
print("   Accedi, clicca 'Agree', poi copia l'URL della pagina")
print("   a cui vieni reindirizzato (inizia con http://127.0.0.1...).\n")

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
except ImportError:
    print("❌ spotipy non installato. Esegui: pip install spotipy")
    sys.exit(1)

sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT,
    scope="playlist-read-private playlist-read-collaborative",
    cache_path=CACHE_PATH,
    open_browser=True,
))

try:
    user = sp.current_user()
    print(f"\n✅ Login riuscito! Benvenuto, {user['display_name']} ({user['id']})")
    print(f"\nToken salvato in: {CACHE_PATH}")
    print("Ora puoi avviare il bot con: python yt_music_bot.py")
except Exception as e:
    print(f"\n❌ Errore durante il login: {e}")
    sys.exit(1)