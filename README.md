# yttoplaylist

Extract tracklists from YouTube DJ sets using Shazam audio fingerprinting, and get them into Spotify.

## Quick start

```bash
git clone https://github.com/faustow/yttoplaylist.git
cd yttoplaylist
pip3 install -r requirements.txt
python3 yttoplaylist.py "https://www.youtube.com/watch?v=XR7771GXI1c" --spotify --json
```

## Requirements

- **Python 3.10+**
- **ffmpeg** — `brew install ffmpeg` (macOS) or `apt install ffmpeg` (Linux)
- **deno** — `brew install deno` (macOS) or [deno.land](https://deno.land/) (Linux). Required by yt-dlp to solve YouTube's JavaScript challenges.

All Python dependencies are in `requirements.txt`.

> **Python 3.13 users**: `audioop-lts` is included in requirements.txt to handle a removed stdlib module.

## Usage

```bash
# Detect tracks and save tracklist
python3 yttoplaylist.py "YOUTUBE_URL"

# With Spotify search + HTML opener page
python3 yttoplaylist.py "YOUTUBE_URL" --spotify

# Also save results as JSON (needed for auto-playlist creation)
python3 yttoplaylist.py "YOUTUBE_URL" --spotify --json

# Custom output filename
python3 yttoplaylist.py "YOUTUBE_URL" -o my_set.txt

# Keep the downloaded audio file
python3 yttoplaylist.py "YOUTUBE_URL" --keep-audio

# Show all segments including unmatched ones
python3 yttoplaylist.py "YOUTUBE_URL" -v

# Faster but less thorough scan (sample every 60s instead of 30s)
python3 yttoplaylist.py "YOUTUBE_URL" --interval 60
```

## Getting tracks into Spotify

### Option 1: HTML opener (no setup needed)

Pass `--spotify` and the tool generates an HTML file you open in your browser:

- **Open All in Spotify** — opens every track (800ms delay between each)
- **Open Next** — opens tracks one at a time
- **Copy Track List** — copies the tracklist to your clipboard
- Individual **Open** button per track

### Option 2: Auto-create playlist (one-time setup)

Use `create_playlist.py` to create a real playlist directly in your Spotify account.

```bash
# 1. Run yttoplaylist with --json to get the tracklist
python3 yttoplaylist.py "YOUTUBE_URL" --json -o my_set.txt

# 2. Create the Spotify playlist from the JSON
python3 create_playlist.py my_set.json "My Playlist Name"
```

**One-time setup:**

1. Go to https://developer.spotify.com/dashboard → Create App
   - **App name**: `yttoplaylist`
   - **Redirect URI**: `https://127.0.0.1:8888/callback`
   - **APIs**: select **Web API**
   - In **Settings → User Management**, add your Spotify email
2. Copy the **Client ID** and **Client Secret** from Settings
3. Add to your shell profile (`~/.zshrc` or `~/.bashrc`):

```bash
export SPOTIPY_CLIENT_ID="your-client-id"
export SPOTIPY_CLIENT_SECRET="your-client-secret"
```

4. Run `create_playlist.py` — it opens your browser to authorize. Spotify redirects to a page that won't load (that's OK). Copy the full URL from the address bar and pass it as the third argument:

```bash
python3 create_playlist.py my_set.json "My Playlist" "https://127.0.0.1:8888/callback?code=..."
```

After the first authorization, the token is cached and subsequent runs won't ask again.

The search validates that Spotify results actually match the detected artist and title — underground tracks not on Spotify show as "NOT FOUND" instead of adding wrong songs.

## Troubleshooting

### YouTube says "Sign in to confirm you're not a bot" / HTTP 429

YouTube rate-limits IPs that make too many requests. Solutions:

1. **Wait a few minutes** and try again
2. **Use browser cookies**: `python3 yttoplaylist.py URL --cookies-from-browser chrome`
3. **Install deno** if you haven't: `brew install deno` — yt-dlp needs it to solve YouTube's anti-bot challenges

### Shazam doesn't recognize some tracks

Normal — Shazam can't identify tracks during DJ transitions (two songs playing simultaneously) or very underground/unreleased tracks. The tool samples every 30 seconds to maximize coverage, but gaps in the tracklist usually mean the DJ was playing something too obscure for Shazam's database.

## Example output

Magdalena | Live at La Estación Córdoba | 2025 (90 min set → 33 tracks detected, 29 found on Spotify):

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

**Shazam**: Processes segments sequentially with a 3-second delay between requests (~20 req/min), safely under the rate limit. If throttled, retries with exponential backoff. A 90-minute set takes ~10 minutes to analyze.

**YouTube**: If rate-limited, the tool suggests using `--cookies-from-browser`. Multiple rapid downloads from the same IP will trigger YouTube's anti-bot protection.

## Use case

Analyze how DJs like Magdalena, Solomun, Adriatique, Juan Hansen, etc. build their setlists — what tracks they pick, in what order, and how they transition between genres and energy levels.

## License

MIT
