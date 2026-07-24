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

# Suppress pydub's noisy ffmpeg warnings (shazamio uses pydub internally)
warnings.filterwarnings("ignore", message=".*Couldn't find ffmpeg.*")
logging.getLogger("pydub.converter").setLevel(logging.ERROR)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# SSL context using certifi certificates (fixes macOS Python SSL issues)
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


class SSLHTTPClient(HTTPClient):
    """HTTPClient that uses certifi SSL certificates for macOS compatibility."""

    async def request(self, method, url, *args, **kwargs):
        async with RetryClient(
            retry_options=self.retry_options,
            raise_for_status=False,
            trace_configs=[self.trace_config],
        ) as client:
            kwargs["ssl"] = SSL_CONTEXT
            if method.upper() == "GET":
                async with client.get(url, **kwargs) as resp:
                    return await validate_json(resp, *args)
            elif method.upper() == "POST":
                async with client.post(url, **kwargs) as resp:
                    return await validate_json(resp, *args)
            else:
                raise BadMethod("Accept only GET/POST")


def download_audio(url: str, output_dir: str) -> tuple[str, str]:
    """Download audio from YouTube URL using yt-dlp. Returns (audio_path, video_title)."""
    logger.info(f"Downloading audio from: {url}")

    # First get the title
    result = subprocess.run(
        ["yt-dlp", "--print", "title", "--no-download", url],
        capture_output=True, text=True, check=True,
    )
    title = result.stdout.strip()

    # Download as ogg (avoids mp3 junk warnings, and shazamio handles ogg natively)
    output_path = os.path.join(output_dir, "audio.ogg")
    subprocess.run(
        [
            "yt-dlp",
            "-x",                       # extract audio
            "--audio-format", "vorbis",  # ogg vorbis — clean format, no junk warnings
            "--audio-quality", "5",      # medium quality (good enough for fingerprinting)
            "-o", output_path,
            "--no-playlist",             # single video only
            url,
        ],
        check=True,
        capture_output=True,
        text=True,
    )

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
    segment_duration: int = 30,
    interval: int = 60,
) -> list[tuple[str, int]]:
    """
    Split audio into segments for recognition using ffmpeg.

    Args:
        audio_path: Path to the audio file
        output_dir: Directory for segment files
        segment_duration: Length of each segment in seconds
        interval: How often to sample in seconds

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
    max_concurrent: int = 3,
) -> list[dict]:
    """
    Recognize songs in audio segments using Shazam.

    Args:
        segments: List of (segment_path, start_time_seconds)
        max_concurrent: Max concurrent Shazam requests

    Returns:
        List of recognition results with timing info
    """
    shazam = Shazam(
        http_client=SSLHTTPClient(
            retry_options=ExponentialRetry(
                attempts=12,
                max_timeout=204.8,
                statuses={500, 502, 503, 504, 429},
            ),
        ),
    )
    results = []
    semaphore = asyncio.Semaphore(max_concurrent)
    total = len(segments)
    completed = 0
    matched = 0

    async def recognize_one(segment_path: str, start_time: int, index: int):
        nonlocal completed, matched
        async with semaphore:
            timestamp = f"{start_time // 60:02d}:{start_time % 60:02d}"
            try:
                out = await shazam.recognize(segment_path)
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
                    logger.info(f"  [{index + 1}/{total}] {timestamp} -> {artist} - {title}")
                else:
                    logger.debug(f"  [{index + 1}/{total}] {timestamp} -> No match")
            except Exception as e:
                logger.warning(f"  [{index + 1}/{total}] {timestamp} -> Error: {e}")
            finally:
                completed += 1
                # Progress indicator on stderr (no newline)
                pct = completed * 100 // total
                bar = f"[{'#' * (pct // 5)}{'.' * (20 - pct // 5)}]"
                print(f"\r  Progress: {bar} {completed}/{total} ({matched} matched)", end="", file=sys.stderr)

    logger.info(f"Recognizing {total} segments via Shazam...")
    tasks = [
        recognize_one(seg_path, start_time, i)
        for i, (seg_path, start_time) in enumerate(segments)
    ]
    await asyncio.gather(*tasks)
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
        if include_spotify and track.get("spotify_uri"):
            line += f"  |  {track['spotify_uri']}"
        lines.append(line)

    lines.append("")
    return "\n".join(lines)


def format_spotify_search_urls(tracks: list[dict]) -> str:
    """Generate Spotify search URLs for all tracks."""
    lines = [
        "# Spotify Search Links",
        "# Open these URLs in your browser to find and add each track to a playlist",
        "",
    ]
    for i, track in enumerate(tracks, 1):
        query = quote(f"{track['artist']} {track['title']}")
        search_url = f"https://open.spotify.com/search/{query}"
        lines.append(f"{i}. {track['artist']} - {track['title']}")
        if track.get("spotify_uri"):
            lines.append(f"   URI: {track['spotify_uri']}")
        lines.append(f"   Search: {search_url}")
        lines.append("")

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
        "--interval", type=int, default=60,
        help="How often to sample the audio in seconds (default: 60)",
    )
    parser.add_argument(
        "--segment-duration", type=int, default=30,
        help="Length of each audio segment sent to Shazam in seconds (default: 30)",
    )
    parser.add_argument(
        "--concurrent", type=int, default=3,
        help="Max concurrent Shazam requests (default: 3)",
    )
    parser.add_argument(
        "--spotify", action="store_true",
        help="Include Spotify URIs/search links in output",
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

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Clean up URL (remove tracking params but keep video ID)
    url = args.url
    # Extract just the video URL without noise
    url_clean = re.sub(r'&(pp|t|si)=[^&]*', '', url)

    with tempfile.TemporaryDirectory(prefix="yttoplaylist_") as tmp_dir:
        # Step 1: Download audio
        audio_path, video_title = download_audio(url, tmp_dir)

        # Step 2: Split into segments
        segments = split_audio(
            audio_path, tmp_dir,
            segment_duration=args.segment_duration,
            interval=args.interval,
        )

        # Step 3: Recognize each segment
        results = await recognize_segments(segments, max_concurrent=args.concurrent)

        if not results:
            logger.error("No tracks were recognized. Try with --interval 30 for denser sampling.")
            sys.exit(1)

        # Step 4: Deduplicate
        tracks = deduplicate_tracks(results)

        # Step 5: Format and save output
        safe_title = re.sub(r'[^\w\s-]', '', video_title).strip().replace(' ', '_')[:80]
        output_path = args.output or f"{safe_title}_tracklist.txt"

        tracklist = format_tracklist(tracks, video_title, url_clean, include_spotify=args.spotify)

        with open(output_path, "w") as f:
            f.write(tracklist)

        if args.spotify:
            spotify_path = output_path.rsplit(".", 1)[0] + "_spotify.txt"
            with open(spotify_path, "w") as f:
                f.write(format_spotify_search_urls(tracks))
            logger.info(f"Spotify links saved to: {spotify_path}")

        if args.json:
            save_json_results(tracks, output_path)

        # Keep audio if requested
        if args.keep_audio:
            import shutil
            kept_path = f"{safe_title}.ogg"
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
        print(f"Total tracks found: {len(tracks)}")


if __name__ == "__main__":
    asyncio.run(main())
