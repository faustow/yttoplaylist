# yttoplaylist

Extract tracklists from YouTube DJ sets using Shazam audio fingerprinting, and get them into Spotify.

## How it works

1. Downloads audio from a YouTube URL using `yt-dlp`
2. Splits the audio into segments using `ffmpeg` (default: 20s clips every 30s)
3. Sends each segment to Shazam for recognition via `shazamio` (sequential, rate-limit safe)
4. Deduplicates and orders the detected tracks
5. Optionally searches Spotify and generates an HTML page to open all tracks, or creates a playlist directly via OAuth

## Installation

```bash
pip install -r requirements.txt
```

Requires:
- Python 3.10+
- `ffmpeg` (`brew install ffmpeg` on macOS)
- `deno` (`brew install deno` on macOS) — required by yt-dlp for YouTube extraction

## Usage

```bash
# Basic: detect tracks and save tracklist
python yttoplaylist.py "https://www.youtube.com/watch?v=XR7771GXI1c"

# With Spotify integration (generates an HTML page to open all tracks)
python yttoplaylist.py "https://www.youtube.com/watch?v=XR7771GXI1c" --spotify

# Create a Spotify playlist directly in your account (requires OAuth setup, see below)
python yttoplaylist.py "https://www.youtube.com/watch?v=XR7771GXI1c" --spotify-playlist "Magdalena Córdoba 2025"

# Also save as JSON
python yttoplaylist.py "https://www.youtube.com/watch?v=XR7771GXI1c" --spotify --json

# If YouTube rate-limits you
python yttoplaylist.py URL --cookies-from-browser chrome

# Keep the downloaded audio file
python yttoplaylist.py URL --keep-audio

# Verbose (show unmatched segments too)
python yttoplaylist.py URL -v
```

## Spotify integration

### Option 1: HTML opener (no setup needed)

Pass `--spotify` and the tool generates an HTML file you open in your browser:

- **Open All in Spotify** — opens every track with 800ms delay between each
- **Open Next** — opens tracks one at a time (click repeatedly)
- **Copy Track List** — copies the full tracklist to your clipboard
- Individual **Open** button per track

No API keys, no accounts, no OAuth. Just open the HTML file.

### Option 2: Auto-create playlist (one-time setup)

Pass `--spotify-playlist "My Playlist Name"` to create a real playlist in your Spotify account.

One-time setup:
1. Go to https://developer.spotify.com/dashboard
2. Create an app (name: "yttoplaylist", any description)
3. In Settings, add `http://localhost:8888/callback` as Redirect URI
4. Copy the Client ID and Client Secret
5. Set environment variables:

```bash
export SPOTIPY_CLIENT_ID="your-client-id"
export SPOTIPY_CLIENT_SECRET="your-client-secret"
```

The first run will open a browser window for you to authorize. After that, the token is cached.

## Example output

Magdalena | Live at La Estación Córdoba | 2025 (90 min set → 33 tracks detected):

```
 1. [04:00] Ion Ludwig - Classilion
 2. [10:00] Lecco - Keep Riddin
 3. [14:00] MIKAA & Joui - Talk My Talk (Original)
 4. [18:30] Cloz & RIKO & GUGGA - Make Money, No Friends (feat. Saintglum)
 5. [22:30] Stephan V. Star - Black Waters
 6. [24:00] Boris Way & Zans - More
 7. [29:30] DJ Stephano & DJ Adrianno - Culori (feat. ADDA) [N-Tone Remix]
 8. [30:00] Villon - Knusperstück
 9. [31:30] MEDUZA - Paradise (feat. Dermot Kennedy) [Cassian Remix]
10. [32:00] R.A.W. - Unbe (Erick 'More' Mix)
11. [32:30] Chris Fortier & Neil Kolo - All I Got (Chris Fortier 20yr Dub)
12. [35:00] Oxia - Domino
13. [35:30] Glenn Morrison - Contact
14. [41:00] Paul Morena - Here Goes
15. [50:30] Mr. G - Transient
16. [54:00] Kings of Tomorrow - Finally (Inspired Edit) [Extended Mix]
17. [55:30] PAX & Rui Da Silva - Touch Me
18. [61:00] ATB - 9 P.M. (Till I Come)
19. [61:30] Oliver Heldens & Kate Ryan - Désenchantée (Oli's EuroRave Mix)
20. [62:30] Rank 1 - Airwave (Radio Vocal Edit)
21. [63:30] DEADWALKMAN - Rhythm 11
22. [65:30] Energy 52 - Café Del Mar (Three 'n One Remix)
23. [66:00] Alice Deejay - Better Off Alone
24. [66:30] Max Styler & Deomid - Get Down
25. [67:00] Faithless - Salva Mea
26. [68:00] Joe Mesmar - Feeling My Mind
27. [69:30] Dario G - Sunchyme
28. [70:30] Paul van Dyk - For An Angel (PvD's E-Werk Club Mix)
29. [71:00] Carl Bee - Somethin' Like This
30. [76:00] DjBleasek - Mystic House Ritual of the Sun
31. [87:30] LynX Producer - Nỗi Nhớ Trong Lòng
32. [89:00] Arjona Moran - Yesterday
33. [90:00] Soda Stereo - De Música Ligera (SÉP7IMO DÍA)
```

## How it handles rate limits

Shazam's API has an undocumented rate limit (~20 requests/minute). The tool processes segments **sequentially** with a 3-second delay between requests (~20 req/min), which stays safely under the threshold. A 90-minute set takes ~10 minutes to analyze.

If a 429 response is received anyway, the tool retries with exponential backoff (2s → 4s → 8s → 16s → 32s).

## Tips

- **Accuracy vs speed**: The default settings (interval=30, segment=20) give good coverage. Increase `--interval` to 45 or 60 for faster but less thorough scans.
- **Transitions**: Tracks playing during DJ transitions may not be detected — Shazam needs ~10s of clean audio. The dense sampling compensates by trying multiple points per track.
- **Underground tracks**: Very obscure or unreleased tracks won't be in Shazam's database. Gaps in the tracklist usually indicate underground/white-label material.
- **Long sets**: A 2-hour set produces ~240 segments and takes ~12 minutes to process.

## Use case

Analyze how DJs like Magdalena, Solomun, Adriatique, etc. build their setlists — what tracks they pick, in what order, and how they transition between genres and energy levels.

## License

MIT
