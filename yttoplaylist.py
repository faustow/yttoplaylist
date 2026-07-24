#!/usr/bin/env python3
"""
yttoplaylist - Extract tracklists from YouTube DJ sets using Shazam fingerprinting.

Downloads audio from a YouTube URL, splits it into segments, identifies each
segment via Shazam, deduplicates, and outputs an ordered tracklist.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import ssl
import subprocess
import sys
import tempfile
import warnings
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import certifi
import aiohttp
from aiohttp_retry import RetryClient, ExponentialRetry
from shazamio import Shazam
from shazamio.client import HTTPClient
from shazamio.exceptions import BadMethod
from shazamio.utils import validate_json
from spotify_search import search_tracks as spotify_search_tracks
from spotify_search import generate_spotify_html, create_spotify_playlist

# Suppress pydub's noisy ffmpeg warnings (shazamio uses pydub internally)
warnings.filterwarnings("ignore", message=".*Couldn't find ffmpeg.*")
logging.getLogger("pydub.converter").setLevel(logging.ERROR)


class OutputFilter(logging.Filter):
    """Filter out noisy pydub/ffmpeg/shazamio internal messages from output."""
    NOISE_PATTERNS = (
        "skipping junk", "invalid mpeg audio header", "estimating duration",
        "found the format marker", "format marker",
    )

    def filter(self, record):
        msg = record.getMessage()
        return not any(p in msg for p in self.NOISE_PATTERNS)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
# Apply warning filter to root logger
for handler in logging.root.handlers:
    handler.addFilter(OutputFilter())
logger = logging.getLogger(__name__)

# SSL context using certifi certificates (fixes macOS Python SSL issues)
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


class SSLHTTPClient(HTTPClient):
    """HTTPClient with certifi SSL and proper 429 handling."""

    async def request(self, method, url, *args, **kwargs):
        import json as _json

        kwargs["ssl"] = SSL_CONTEXT
        max_retries = 5
        base_delay = 2.0

        for attempt in range(max_retries):
            async with RetryClient(
                retry_options=self.retry_options,
                raise_for_status=False,
                trace_configs=[self.trace_config],
            ) as client:
                handler = client.get if method.upper() == "GET" else client.post
                if method.upper() not in ("GET", "POST"):
                    raise BadMethod("Accept only GET/POST")

                async with handler(url, **kwargs) as resp:
                    if resp.status == 429:
                        delay = base_delay * (2 ** attempt)
                        logger.debug(f"Shazam 429, waiting {delay:.0f}s (attempt {attempt + 1}/{max_retries})")
                        await asyncio.sleep(delay)
                        continue

                    text = await resp.text()
                    try:
                        return _json.loads(text)
                    except _json.JSONDecodeError:
                        if attempt < max_retries - 1:
                            delay = base_delay * (2 ** attempt)
                            await asyncio.sleep(delay)
                            continue
                        raise

        # If we exhausted retries on 429
        raise Exception("Shazam API rate limited (429) - try again later")


def download_audio(url: str, output_dir: str, cookies_browser: str = None) -> tuple[str, str]:
    """Download audio from YouTube URL using yt-dlp. Returns (audio_path, video_title)."""
    logger.info(f"Downloading audio from: {url}")

    cookie_args = ["--cookies-from-browser", cookies_browser] if cookies_browser else []

    # First get the title
    result = subprocess.run(
        ["yt-dlp", "--print", "title", "--no-download", *cookie_args, url],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        # Check for common errors
        if "429" in result.stderr or "bot" in result.stderr.lower():
            logger.error("YouTube rate limited us (429). Try again in a few minutes, or use --cookies-from-browser chrome")
        else:
            logger.error(f"yt-dlp failed to get title:\n{result.stderr}")
        raise RuntimeError("Failed to get video title")
    title = result.stdout.strip()

    # Download as mp3
    output_path = os.path.join(output_dir, "audio.mp3")
    dl_result = subprocess.run(
        [
            "yt-dlp",
            "-x",                       # extract audio
            "--audio-format", "mp3",     # mp3 — widely compatible
            "--audio-quality", "5",      # medium quality (good enough for fingerprinting)
            "-o", output_path,
            "--no-playlist",             # single video only
            *cookie_args,
            url,
        ],
        capture_output=True,
        text=True,
    )
    if dl_result.returncode != 0:
        if "429" in dl_result.stderr or "bot" in dl_result.stderr.lower():
            logger.error("YouTube rate limited us (429). Try again in a few minutes, or use --cookies-from-browser chrome")
        else:
            logger.error(f"yt-dlp download failed:\n{dl_result.stderr}")
        raise RuntimeError(f"yt-dlp download failed (exit code {dl_result.returncode})")

    if not os.path.exists(output_path):
        # yt-dlp sometimes appends extension
        candidates = list(Path(output_dir).glob("audio.*"))
        if candidates:
            output_path = str(candidates[0])
        else:
            raise FileNotFoundError("Failed to download audio")

    logger.info(f"Downloaded: {title}")
    return output_path, title


def get_audio_duration(audio_path: str) -> float:
    """Get duration of an audio file in seconds using ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            audio_path,
        ],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def split_audio(
    audio_path: str,
    output_dir: str,
    segment_duration: int = 20,
    interval: int = 30,
) -> list[tuple[str, int]]:
    """
    Split audio into segments for recognition using ffmpeg.

    Args:
        audio_path: Path to the audio file
        output_dir: Directory for segment files
        segment_duration: Length of each audio clip sent to Shazam (default: 20s)
        interval: How often to sample (default: every 30s for dense coverage)

    Returns:
        List of (segment_path, start_time_seconds)
    """
    total_duration = int(get_audio_duration(audio_path))
    logger.info(f"Audio duration: {total_duration // 60}m {total_duration % 60}s")

    segments = []
    segments_dir = os.path.join(output_dir, "segments")
    os.makedirs(segments_dir, exist_ok=True)

    position = 0
    num_segments = (total_duration + interval - 1) // interval
    logger.info(f"Splitting into {num_segments} segments (every {interval}s, {segment_duration}s each)...")

    while position < total_duration:
        segment_path = os.path.join(segments_dir, f"segment_{position:05d}.ogg")
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "quiet",
                "-ss", str(position),
                "-i", audio_path,
                "-t", str(segment_duration),
                "-ac", "1",             # mono (Shazam only needs mono)
                "-ar", "16000",         # 16kHz sample rate (enough for fingerprinting)
                segment_path,
            ],
            check=True, capture_output=True,
        )
        segments.append((segment_path, position))
        position += interval

    logger.info(f"Created {len(segments)} segments")
    return segments


async def recognize_segments(
    segments: list[tuple[str, int]],
    request_delay: float = 3.0,
) -> list[dict]:
    """
    Recognize songs in audio segments using Shazam.

    Processes segments SEQUENTIALLY with a fixed delay between requests
    to stay well under Shazam's rate limit (~20 req/min).

    Args:
        segments: List of (segment_path, start_time_seconds)
        request_delay: Seconds to wait between each Shazam request (default: 3.0)

    Returns:
        List of recognition results with timing info
    """
    shazam = Shazam(
        http_client=SSLHTTPClient(
            retry_options=ExponentialRetry(
                attempts=3,
                max_timeout=15.0,
                statuses={500, 502, 503, 504},
            ),
        ),
    )
    results = []
    total = len(segments)
    matched = 0
    estimated_minutes = (total * request_delay) / 60

    logger.info(f"Recognizing {total} segments via Shazam (~{estimated_minutes:.0f} min at {request_delay}s/request)...")

    for i, (segment_path, start_time) in enumerate(segments):
        timestamp = f"{start_time // 60:02d}:{start_time % 60:02d}"
        try:
            out = await asyncio.wait_for(
                shazam.recognize(segment_path),
                timeout=30,
            )
            if out and "track" in out:
                track = out["track"]
                title = track.get("title", "Unknown")
                artist = track.get("subtitle", "Unknown")
                shazam_key = track.get("key", "")

                # Extract Spotify URI if available
                spotify_uri = None
                providers = track.get("hub", {}).get("providers", [])
                for provider in providers:
                    if provider.get("type") == "SPOTIFY":
                        actions = provider.get("actions", [])
                        for action in actions:
                            if action.get("type") == "uri":
                                spotify_uri = action.get("uri")

                # Extract Apple Music URL if available
                apple_url = None
                for provider in providers:
                    if provider.get("type") == "APPLE":
                        actions = provider.get("actions", [])
                        for action in actions:
                            if action.get("type") == "uri":
                                apple_url = action.get("uri")

                result = {
                    "start_time": start_time,
                    "timestamp": timestamp,
                    "title": title,
                    "artist": artist,
                    "shazam_key": shazam_key,
                    "spotify_uri": spotify_uri,
                    "apple_url": apple_url,
                }
                results.append(result)
                matched += 1
                logger.info(f"  [{i + 1}/{total}] {timestamp} -> {artist} - {title}")
            else:
                logger.debug(f"  [{i + 1}/{total}] {timestamp} -> No match")
        except Exception as e:
            logger.warning(f"  [{i + 1}/{total}] {timestamp} -> Error: {e}")

        # Progress indicator
        completed = i + 1
        pct = completed * 100 // total
        bar = f"[{'#' * (pct // 5)}{'.' * (20 - pct // 5)}]"
        remaining = (total - completed) * request_delay
        eta = f"{remaining / 60:.0f}m" if remaining > 60 else f"{remaining:.0f}s"
        print(f"\r  Progress: {bar} {completed}/{total} ({matched} matched) ETA: {eta}  ", end="", file=sys.stderr)

        # Fixed delay between requests — this is what keeps us under the rate limit
        if i < total - 1:
            await asyncio.sleep(request_delay)

    print(file=sys.stderr)  # newline after progress bar

    # Sort by start time
    results.sort(key=lambda x: x["start_time"])
    return results


def normalize_title(title: str) -> str:
    """Normalize a track title for deduplication comparison."""
    # Remove common suffixes like (Original Mix), (Radio Edit), (Album Cut), etc.
    t = re.sub(r'\s*\(.*?\)\s*', ' ', title)
    t = re.sub(r'\s*\[.*?\]\s*', ' ', t)
    t = t.lower().strip()
    t = re.sub(r'\s+', ' ', t)
    return t


def deduplicate_tracks(results: list[dict]) -> list[dict]:
    """
    Deduplicate tracks, keeping the first occurrence.
    Handles both consecutive duplicates and non-consecutive ones
    (e.g., same song detected at different timestamps with different remix labels).
    """
    if not results:
        return []

    deduped = []
    seen_keys = set()       # exact Shazam key matches
    seen_titles = set()     # normalized artist+title for fuzzy matching

    for result in results:
        key = result["shazam_key"]
        normalized = normalize_title(f"{result['artist']} - {result['title']}")

        # Skip if we've seen this exact track or a very similar one
        if key in seen_keys:
            continue
        if normalized in seen_titles:
            continue

        deduped.append(result)
        seen_keys.add(key)
        seen_titles.add(normalized)

    logger.info(f"Deduplicated: {len(results)} detections -> {len(deduped)} unique tracks")
    return deduped


def format_tracklist(
    tracks: list[dict],
    title: str,
    url: str,
    include_spotify: bool = False,
) -> str:
    """Format the tracklist as a human-readable text file."""
    lines = [
        f"# Tracklist: {title}",
        f"# Source: {url}",
        f"# Detected: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"# Tracks found: {len(tracks)}",
        "",
    ]

    for i, track in enumerate(tracks, 1):
        line = f"{i:2d}. [{track['timestamp']}] {track['artist']} - {track['title']}"
        if include_spotify and track.get("spotify_url"):
            line += f"  |  {track['spotify_url']}"
        elif include_spotify and track.get("spotify_uri"):
            line += f"  |  {track['spotify_uri']}"
        lines.append(line)

    lines.append("")
    return "\n".join(lines)


def format_spotify_search_urls(tracks: list[dict]) -> str:
    """Generate Spotify links for all tracks."""
    lines = [
        "# Spotify Playlist",
        "# Tracks with Spotify URIs can be pasted directly into Spotify search",
        "# Tracks with URLs can be opened in browser",
        "",
    ]
    found = 0
    for i, track in enumerate(tracks, 1):
        lines.append(f"{i}. {track['artist']} - {track['title']}")
        if track.get("spotify_url"):
            lines.append(f"   URL: {track['spotify_url']}")
            lines.append(f"   URI: {track['spotify_uri']}")
            found += 1
        elif track.get("spotify_uri"):
            lines.append(f"   URI: {track['spotify_uri']}")
            found += 1
        else:
            query = quote(f"{track['artist']} {track['title']}")
            lines.append(f"   Search: https://open.spotify.com/search/{query}")
            lines.append(f"   (not found on Spotify)")
        lines.append("")

    lines.insert(3, f"# Found on Spotify: {found}/{len(tracks)}")
    return "\n".join(lines)


def save_json_results(tracks: list[dict], output_path: str):
    """Save raw results as JSON for programmatic use."""
    json_path = output_path.rsplit(".", 1)[0] + ".json"
    with open(json_path, "w") as f:
        json.dump(tracks, f, indent=2, ensure_ascii=False)
    logger.info(f"JSON results saved to: {json_path}")


async def main():
    parser = argparse.ArgumentParser(
        description="Extract tracklists from YouTube DJ sets using Shazam",
        epilog="Example: python yttoplaylist.py 'https://www.youtube.com/watch?v=XR7771GXI1c'",
    )
    parser.add_argument("url", help="YouTube video URL")
    parser.add_argument(
        "-o", "--output",
        help="Output file path (default: <video_title>_tracklist.txt)",
    )
    parser.add_argument(
        "--interval", type=int, default=30,
        help="How often to sample the audio in seconds (default: 30)",
    )
    parser.add_argument(
        "--segment-duration", type=int, default=20,
        help="Length of each audio segment sent to Shazam in seconds (default: 20)",
    )
    parser.add_argument(
        "--spotify", action="store_true",
        help="Search Spotify for each track and generate an HTML file to open them",
    )
    parser.add_argument(
        "--spotify-playlist", metavar="NAME",
        help="Create a Spotify playlist with the given name (requires SPOTIPY_CLIENT_ID "
             "and SPOTIPY_CLIENT_SECRET env vars)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Also save results as JSON",
    )
    parser.add_argument(
        "--keep-audio", action="store_true",
        help="Keep downloaded audio file (default: delete after processing)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Verbose output (show unmatched segments too)",
    )
    parser.add_argument(
        "--cookies-from-browser",
        help="Browser to extract cookies from (e.g., 'chrome', 'firefox'). "
             "Useful when YouTube rate-limits or asks to sign in.",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Clean up URL (remove tracking params but keep video ID)
    url = args.url
    # Extract just the video URL without noise
    url_clean = re.sub(r'&(pp|t|si)=[^&]*', '', url)

    with tempfile.TemporaryDirectory(prefix="yttoplaylist_") as tmp_dir:
        # Step 1: Download audio
        audio_path, video_title = download_audio(url, tmp_dir, cookies_browser=args.cookies_from_browser)

        # Step 2: Split into segments
        segments = split_audio(
            audio_path, tmp_dir,
            segment_duration=args.segment_duration,
            interval=args.interval,
        )

        # Step 3: Recognize each segment
        results = await recognize_segments(segments)

        if not results:
            logger.error("No tracks were recognized. Try with --interval 30 for denser sampling.")
            sys.exit(1)

        # Step 4: Deduplicate
        tracks = deduplicate_tracks(results)

        # Step 5: Search Spotify for each track
        use_spotify = args.spotify or args.spotify_playlist
        if use_spotify:
            logger.info("Searching Spotify for detected tracks...")
            tracks = await spotify_search_tracks(tracks)

        # Step 6: Format and save output
        safe_title = re.sub(r'[^\w\s-]', '', video_title).strip().replace(' ', '_')[:80]
        output_path = args.output or f"{safe_title}_tracklist.txt"

        tracklist = format_tracklist(tracks, video_title, url_clean, include_spotify=use_spotify)

        with open(output_path, "w") as f:
            f.write(tracklist)

        if use_spotify:
            # Generate HTML file to open tracks in Spotify
            html_path = generate_spotify_html(tracks, video_title, output_path)
            logger.info(f"Spotify HTML saved to: {html_path}")

            # Also save the text version
            spotify_path = output_path.rsplit(".", 1)[0] + "_spotify.txt"
            with open(spotify_path, "w") as f:
                f.write(format_spotify_search_urls(tracks))
            logger.info(f"Spotify links saved to: {spotify_path}")

        # Create Spotify playlist via OAuth if requested
        if args.spotify_playlist:
            playlist_url = create_spotify_playlist(tracks, args.spotify_playlist)
            if playlist_url:
                print(f"\n  Spotify playlist created: {playlist_url}")

        if args.json:
            save_json_results(tracks, output_path)

        # Keep audio if requested
        if args.keep_audio:
            import shutil
            kept_path = f"{safe_title}.mp3"
            shutil.copy2(audio_path, kept_path)
            logger.info(f"Audio saved to: {kept_path}")

        # Print summary
        print()
        print(f"=== TRACKLIST: {video_title} ===")
        print()
        for i, track in enumerate(tracks, 1):
            print(f"  {i:2d}. [{track['timestamp']}] {track['artist']} - {track['title']}")
        print()
        print(f"Saved to: {output_path}")
        if use_spotify:
            print(f"Spotify: open {output_path.rsplit('.', 1)[0] + '_spotify.html'} in your browser")
        print(f"Total tracks found: {len(tracks)}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as e:
        logger.error(str(e))
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
