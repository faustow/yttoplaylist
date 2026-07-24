# yttoplaylist

Extract the tracklist from a YouTube DJ set (or any music video) using Shazam audio fingerprinting, and generate a Spotify-ready playlist.

## How it works

1. Downloads audio from a YouTube URL using `yt-dlp`
2. Splits the audio into segments (default: 30s each, sampled every 60s)
3. Sends each segment to Shazam for recognition via `shazamio`
4. Deduplicates and orders the detected tracks
5. Outputs a tracklist as `.txt` and optionally searches Spotify for each track

## Installation

```bash
pip install -r requirements.txt
```

Requires `ffmpeg` installed on your system.

## Usage

```bash
# Basic: detect tracks and save tracklist
python yttoplaylist.py "https://www.youtube.com/watch?v=XR7771GXI1c"

# Custom segment interval (sample every 45 seconds)
python yttoplaylist.py "https://www.youtube.com/watch?v=XR7771GXI1c" --interval 45

# Custom output file
python yttoplaylist.py "https://www.youtube.com/watch?v=XR7771GXI1c" -o my_tracklist.txt

# Include Spotify search URIs in the output
python yttoplaylist.py "https://www.youtube.com/watch?v=XR7771GXI1c" --spotify
```

## Output

The tool generates a `.txt` file with the tracklist:

```
# Tracklist: Magdalena | Live at La Estacion Cordoba | 2025
# Source: https://www.youtube.com/watch?v=XR7771GXI1c
# Detected: 2025-07-23

1. [00:00] Artist - Track Name
2. [03:30] Artist - Track Name
3. [07:15] Artist - Track Name
...
```

## Use case

Analyze how DJs like Magdalena, Solomun, etc. build their setlists — what tracks they pick, in what order, and how they transition between them.

## License

MIT
