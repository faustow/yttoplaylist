"""
spotify_search.py - Search Spotify without authentication using the anonymous embed token.

Uses Spotify's embed endpoint to get an anonymous access token, then searches
the Spotify catalog via the Web API. No OAuth, no client credentials needed.
"""

import json
import logging
import re
import ssl
from urllib.parse import quote, urlencode

import certifi

logger = logging.getLogger(__name__)

SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

# Known track URI to bootstrap the anonymous token
_BOOTSTRAP_TRACK = "4it4NYn9wNqGV54joA6oN0"  # Soda Stereo - De Música Ligera


async def get_anonymous_token() -> str:
    """Get an anonymous Spotify access token from the embed endpoint."""
    import aiohttp

    url = f"https://open.spotify.com/embed/track/{_BOOTSTRAP_TRACK}"
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, ssl=SSL_CONTEXT) as resp:
            html = await resp.text()
            match = re.search(r'"accessToken":"([^"]+)"', html)
            if match:
                return match.group(1)
            raise RuntimeError("Could not extract anonymous Spotify token from embed page")


async def search_track(token: str, artist: str, title: str) -> dict | None:
    """
    Search for a track on Spotify.

    Returns dict with keys: uri, name, artist, url, or None if not found.
    """
    import aiohttp

    # Clean up the query — remove remix/edit suffixes for broader matching
    query = f"{artist} {title}"
    # Remove parenthetical suffixes for the search query
    clean_title = re.sub(r'\s*[\(\[].*?[\)\]]', '', title).strip()
    clean_query = f"{artist} {clean_title}" if clean_title != title else query

    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    }

    async with aiohttp.ClientSession() as session:
        # Try exact query first, then cleaned query
        for q in [query, clean_query]:
            params = urlencode({"q": q, "type": "track", "limit": "3"})
            url = f"https://api.spotify.com/v1/search?{params}"

            async with session.get(url, headers=headers, ssl=SSL_CONTEXT) as resp:
                if resp.status != 200:
                    logger.debug(f"Spotify search returned {resp.status} for: {q}")
                    continue

                data = await resp.json()
                items = data.get("tracks", {}).get("items", [])

                if not items:
                    continue

                # Find best match — prefer exact artist match
                best = items[0]
                for item in items:
                    item_artists = ", ".join(a["name"].lower() for a in item["artists"])
                    if artist.lower() in item_artists:
                        best = item
                        break

                return {
                    "uri": best["uri"],
                    "name": best["name"],
                    "artist": ", ".join(a["name"] for a in best["artists"]),
                    "url": best["external_urls"].get("spotify", ""),
                    "album": best.get("album", {}).get("name", ""),
                }

    return None


async def search_tracks(tracks: list[dict]) -> list[dict]:
    """
    Search for all tracks on Spotify. Adds 'spotify_uri' and 'spotify_url'
    to each track dict.

    Args:
        tracks: List of track dicts with 'artist' and 'title' keys

    Returns:
        The same list with Spotify info added
    """
    try:
        token = await get_anonymous_token()
    except Exception as e:
        logger.warning(f"Could not get Spotify token: {e}")
        return tracks

    found = 0
    for track in tracks:
        result = await search_track(token, track["artist"], track["title"])
        if result:
            track["spotify_uri"] = result["uri"]
            track["spotify_url"] = result["url"]
            track["spotify_name"] = result["name"]
            track["spotify_artist"] = result["artist"]
            found += 1
            logger.info(f"  Spotify: {result['artist']} - {result['name']}")
        else:
            logger.debug(f"  Spotify: not found - {track['artist']} - {track['title']}")

    logger.info(f"Spotify: found {found}/{len(tracks)} tracks")
    return tracks
