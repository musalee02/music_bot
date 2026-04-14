# Music Bot 🎵

Un bot Telegram per scaricare musica da YouTube e Spotify direttamente sulla piattaforma.

## Descrizione

Music Bot è un bot Telegram che consente di:
- ✅ Scaricare musica da YouTube
- ✅ Cercare e scaricare da Spotify
- ✅ Gestire playlist
- ✅ Convertire audio in MP3 con FFmpeg

## Prerequisiti

### Locali
- Python 3.8+
- FFmpeg (per la conversione audio)
- Un bot Telegram creato tramite [@BotFather](https://t.me/botfather)
- (Opzionale) Credenziali Spotify per la ricerca playlist

### Da GitHub Actions
- Il progetto viene testato automaticamente ad ogni push
- Le variabili di ambiente sono iniettate tramite GitHub Secrets

## Setup Locale

### 1. Clona il repository
```bash
git clone <repo-url>
cd music_bot
```

### 2. Crea un ambiente virtuale
```bash
python -m venv venv
source venv/bin/activate  # su Linux/Mac
# oppure
venv\Scripts\activate  # su Windows
```

### 3. Installa le dipendenze
```bash
pip install -r requirements.txt
```

### 4. Configura le variabili di ambiente
Crea un file `.env` nella root del progetto:
```env
TELEGRAM_TOKEN=123456789:ABCDefGhiJklMnoPqrsTuvWxyz...
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
```

### 5. (Opzionale) Setup Spotify
Se vuoi usare la ricerca Spotify:
```bash
python setup_spotify.py
```

### 6. Avvia il bot
```bash
python yt_music_bot.py
```

## Struttura del Progetto

```
music_bot/
├── yt_music_bot.py           # Bot principale
├── songs_download_bot.py      # Bot alternativo (legacy)
├── setup_spotify.py           # Script setup Spotify
├── requirements.txt           # Dipendenze Python
├── playlist.txt              # File playlist
├── .env                      # Variabili di ambiente (non committare)
├── .gitignore               # File ignorati da git
└── README.md                # Questo file
```

## File Principali

### `yt_music_bot.py`
Il bot principale che gestisce le interazioni Telegram:
- Comando `/start` - Avvia il bot
- Comando `/help` - Mostra i comandi disponibili
- Invio URL YouTube/Spotify - Scarica la musica

### `setup_spotify.py`
Script di configurazione per OAuth Spotify. Eseguire prima se si vuole usare Spotify.

### `songs_download_bot.py`
Versione legacy del bot con funzionalità alternative.

## Deploy su GitHub Actions

Il progetto è configurato per eseguire test automatici tramite GitHub Actions.

### Configurare i Secrets
1. Vai su **Settings** → **Secrets and variables** → **Actions**
2. Aggiungi i seguenti secrets:
   - `TELEGRAM_TOKEN` - Il token del tuo bot Telegram
   - `SPOTIFY_CLIENT_ID` - (Opzionale) ID Spotify
   - `SPOTIFY_CLIENT_SECRET` - (Opzionale) Secret Spotify

### Workflow Automati
Il file `.github/workflows/test.yml` esegue automaticamente:
- Linting del codice
- Test delle dipendenze
- Verifica della sintassi

## Utilizzo

### Su Telegram
1. Trova il bot: cerca il nome nel tuo username Telegram
2. Invia un URL YouTube o Spotify
3. Il bot scaricherà e ti invierà il file audio

### Comandi disponibili
- `/start` - Inizia
- `/help` - Aiuto
- `/list` - Mostra playlist salvate

## Troubleshooting

### FFmpeg non trovato
Assicurati che FFmpeg sia installato e nel PATH:
```bash
ffmpeg -version
```

### Errori di autenticazione Spotify
Verifica che le credenziali nel `.env` siano corrette e che il Redirect URI sia configurato correttamente nel Spotify Dashboard.

### Bot non risponde
Controlla che il `TELEGRAM_TOKEN` sia corretto e che il bot sia in esecuzione.

## Sviluppo

### Branch principale
Il codice viene testato automaticamente ad ogni push nel branch `main`.

### Contribuire
1. Crea un branch feature: `git checkout -b feature/nuova-funzionalita`
2. Fai i commit: `git commit -m "Descrizione"`
3. Push: `git push origin feature/nuova-funzionalita`
4. Apri una Pull Request

## License

MIT License - vedi LICENSE per dettagli.

## Supporto

Per problemi o feature request, apri un issue nel repository.
