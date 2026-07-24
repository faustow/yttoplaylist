# yttoplaylist

Extract the tracklist from a YouTube DJ set using Shazam audio fingerprinting, and generate Spotify-ready search links.

## How it works

1. Downloads audio from a YouTube URL using `yt-dlp`
2. Splits the audio into segments using `ffmpeg` (default: 30s each, sampled every 60s)
3. Sends each segment to Shazam for recognition via `shazamio`
4. Deduplicates and orders the detected tracks
5. Outputs a timestamped tracklist as `.txt` with optional Spotify search links

## Installation

```bash
pip install -r requirements.txt
```

Requires `ffmpeg` installed on your system (`brew install ffmpeg` on macOS).

Python 3.10+ required.

## Usage

```bash
# Basic: detect tracks and save tracklist
python yttoplaylist.py "https://www.youtube.com/watch?v=XR7771GXI1c"

# Custom segment interval (sample every 45 seconds for better coverage)
python yttoplaylist.py "https://www.youtube.com/watch?v=XR7771GXI1c" --interval 45

# Custom output file
python yttoplaylist.py "https://www.youtube.com/watch?v=XR7771GXI1c" -o my_tracklist.txt

# Include Spotify search links
python yttoplaylist.py "https://www.youtube.com/watch?v=XR7771GXI1c" --spotify

# Full output (JSON + Spotify + verbose)
python yttoplaylist.py "https://www.youtube.com/watch?v=XR7771GXI1c" --spotify --json -v

# Keep the downloaded audio
python yttoplaylist.py "https://www.youtube.com/watch?v=XR7771GXI1c" --keep-audio
```

## Output

The tool generates a `.txt` file with the tracklist:

```
# Tracklist: Magdalena | Live at La Estacion Cordoba | 2025
# Source: https://www.youtube.com/watch?v=XR7771GXI1c
# Detected: 2025-07-23 22:03
# Tracks found: 19

 1. [04:00] Editors - Frankenstein (Joyhauser Mix)
 2. [10:00] 2THIRD - Bet U Know Me
 3. [13:00] Moloko - Sing It Back (Boris Musical Mix) [Edit]
 4. [35:00] Oxia - Domino
 5. [61:00] Alice Deejay - Better Off Alone
 6. [64:00] Rank 1 - Airwave (Album Cut)
 7. [90:00] Soda Stereo - De Música Ligera
...
```

With `--spotify`, it also generates a `_spotify.txt` with search links you can open directly in your browser.

## Tips

- **Longer sets**: Use `--interval 45` for better coverage (more API calls but fewer missed tracks)
- **Short tracks**: If the DJ plays short clips, try `--interval 30 --segment-duration 15`
- **Rate limiting**: The tool defaults to 3 concurrent Shazam requests. If you get errors, try `--concurrent 1`
- **Transitions**: Tracks playing during transitions may not be detected (audio is mixed)

## Use case

Analyze how DJs like Magdalena, Solomun, etc. build their setlists — what tracks they pick, in what order, and how they transition between them.

## License

MIT
