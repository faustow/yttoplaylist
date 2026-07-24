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
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from pydub import AudioSegment
from shazamio import Shazam, Serialize

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def download_audio(url: str, output_dir: str) -> tuple[str, str]:
    """Download audio from YouTube URL using yt-dlp. Returns (audio_path, video_title)."""
    logger.info(f"Downloading audio from: {url}")

    # First get the title
    result = subprocess.run(
        ["yt-dlp", "--print", "title", "--no-download", url],
        capture_output=True, text=True, check=True,
    )
    title = result.stdout.strip()

    # Download as mp3
    output_path = os.path.join(output_dir, "audio.mp3")
    subprocess.run(
        [
            "yt-dlp",
            "-x",                       # extract audio
            "--audio-format", "mp3",     # convert to mp3
            "--audio-quality", "5",      # medium quality (smaller file, good enough for fingerprinting)
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


def split_audio(
    audio_path: str,
    output_dir: str,
    segment_duration: int = 30,
    interval: int = 60,
) -> list[tuple[str, int]]:
    """
    Split audio into segments for recognition.

    Args:
        audio_path: Path to the audio file
        output_dir: Directory for segment files
        segment_duration: Length of each segment in seconds (how much audio to send to Shazam)
        interval: How often to sample in seconds (e.g., every 60s = one segment per minute)

    Returns:
        List of (segment_path, start_time_seconds)
    """
    logger.info("Loading audio file...")
    audio = AudioSegment.from_file(audio_path)
    total_duration = len(audio) // 1000  # milliseconds to seconds
    logger.info(f"Audio duration: {total_duration // 60}m {total_duration % 60}s")

    segments = []
    segments_dir = os.path.join(output_dir, "segments")
    os.makedirs(segments_dir, exist_ok=True)

    position = 0
    while position < total_duration:
        start_ms = position * 1000
        end_ms = min((position + segment_duration) * 1000, len(audio))
        segment = audio[start_ms:end_ms]

        segment_path = os.path.join(segments_dir, f"segment_{position:05d}.ogg")
        segment.export(segment_path, format="ogg")
        segments.append((segment_path, position))

        position += interval

    logger.info(f"Created {len(segments)} segments (every {interval}s, {segment_duration}s each)")
    return segments


async def recognize_segments(
    segments: list[tuple[str, int]],
    max_concurrent: int = 3,
) -> list[dict]:
    """
    Recognize songs in audio segments using Shazam.

    Args:
        segments: List of (segment_path, start_time_seconds)
        max_concurrent: Max concurrent Shazam requests (be gentle with the API)

    Returns:
        List of recognition results with timing info
    """
    shazam = Shazam()
    results = []
    semaphore = asyncio.Semaphore(max_concurrent)
    total = len(segments)

    async def recognize_one(segment_path: str, start_time: int, index: int):
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

                    result = {
                        "start_time": start_time,
                        "timestamp": timestamp,
                        "title": title,
                        "artist": artist,
                        "shazam_key": shazam_key,
                        "spotify_uri": spotify_uri,
                    }
                    results.append(result)
                    logger.info(f"  [{index + 1}/{total}] {timestamp} -> {artist} - {title}")
                else:
                    logger.debug(f"  [{index + 1}/{total}] {timestamp} -> No match")
            except Exception as e:
                logger.warning(f"  [{index + 1}/{total}] {timestamp} -> Error: {e}")

    logger.info(f"Recognizing {total} segments...")
    tasks = [
        recognize_one(seg_path, start_time, i)
        for i, (seg_path, start_time) in enumerate(segments)
    ]
    await asyncio.gather(*tasks)

    # Sort by start time
    results.sort(key=lambda x: x["start_time"])
    return results


def deduplicate_tracks(results: list[dict]) -> list[dict]:
    """
    Deduplicate consecutive identical tracks, keeping the first occurrence.
    A DJ set will have the same song detected across multiple consecutive segments.
    """
    if not results:
        return []

    deduped = [results[0]]
    for result in results[1:]:
        # Same track as previous? Skip it.
        if result["shazam_key"] == deduped[-1]["shazam_key"]:
            continue
        deduped.append(result)

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
    """Generate Spotify search URLs for tracks that don't have a direct URI."""
    lines = [
        "# Spotify Search Links",
        "# Open these in your browser to find and add each track",
        "",
    ]
    for i, track in enumerate(tracks, 1):
        if track.get("spotify_uri"):
            lines.append(f"{i}. {track['artist']} - {track['title']}")
            lines.append(f"   Spotify URI: {track['spotify_uri']}")
        else:
            query = f"{track['artist']} {track['title']}".replace(" ", "%20")
            search_url = f"https://open.spotify.com/search/{query}"
            lines.append(f"{i}. {track['artist']} - {track['title']}")
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

    # Clean up URL (remove tracking params)
    url = args.url.split("&pp=")[0].split("&t=")[0]
    if "&" not in url and "?" in url:
        url = url  # just the video ID param

    with tempfile.TemporaryDirectory(prefix="yttoplaylist_") as tmp_dir:
        # Step 1: Download audio
        audio_path, video_title = download_audio(args.url, tmp_dir)

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

        tracklist = format_tracklist(tracks, video_title, args.url, include_spotify=args.spotify)

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
            kept_path = f"{safe_title}.mp3"
            import shutil
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
