"""
spotify_search.py - Spotify integration for yttoplaylist.

Three modes:
1. Anonymous search (no auth): finds Spotify URLs for each track
2. HTML opener (no auth): generates an HTML file that opens all tracks in Spotify
3. OAuth playlist (requires setup): creates a real playlist in your Spotify account
"""

import json
import logging
import os
import re
import ssl
from urllib.parse import quote, urlencode

import certifi

logger = logging.getLogger(__name__)

SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

# Known track URI to bootstrap the anonymous token
_BOOTSTRAP_TRACK = "4it4NYn9wNqGV54joA6oN0"  # Soda Stereo - De Música Ligera


# ---------------------------------------------------------------------------
# Anonymous search (no auth needed)
# ---------------------------------------------------------------------------

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
    Search for a track on Spotify using the anonymous token.
    Returns dict with keys: uri, name, artist, url, or None if not found.
    """
    import aiohttp

    query = f"{artist} {title}"
    clean_title = re.sub(r'\s*[\(\[].*?[\)\]]', '', title).strip()
    clean_query = f"{artist} {clean_title}" if clean_title != title else query

    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    }

    async with aiohttp.ClientSession() as session:
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


# ---------------------------------------------------------------------------
# HTML opener — generates an HTML page that opens all tracks in Spotify
# ---------------------------------------------------------------------------

def generate_spotify_html(tracks: list[dict], title: str, output_path: str) -> str:
    """
    Generate an HTML file that lets you open all tracks in Spotify with one click.
    Works with both Spotify URIs (opens the app) and search URLs (opens the browser).
    """
    html_path = output_path.rsplit(".", 1)[0] + "_spotify.html"

    rows = []
    for i, track in enumerate(tracks, 1):
        artist = track["artist"]
        track_title = track["title"]
        timestamp = track["timestamp"]

        if track.get("spotify_url"):
            url = track["spotify_url"]
            badge = '<span class="found">✓ found</span>'
        else:
            q = quote(f"{artist} {track_title}")
            url = f"https://open.spotify.com/search/{q}"
            badge = '<span class="search">search</span>'

        rows.append(
            f'      <tr>'
            f'<td>{i}</td>'
            f'<td>[{timestamp}]</td>'
            f'<td>{artist} - {track_title}</td>'
            f'<td>{badge}</td>'
            f'<td><a href="{url}" target="_blank" class="open-btn">Open</a></td>'
            f'</tr>'
        )

    table_rows = "\n".join(rows)

    track_urls = []
    for track in tracks:
        if track.get("spotify_url"):
            track_urls.append(track["spotify_url"])
        else:
            q = quote(f"{track['artist']} {track['title']}")
            track_urls.append(f"https://open.spotify.com/search/{q}")
    track_urls_json = json.dumps(track_urls)

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Spotify Playlist: {title}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #121212; color: #fff; margin: 2rem; }}
    h1 {{ color: #1DB954; font-size: 1.5rem; }}
    .subtitle {{ color: #b3b3b3; margin-bottom: 1.5rem; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #282828; }}
    th {{ color: #b3b3b3; font-weight: normal; text-transform: uppercase; font-size: 0.75rem; }}
    tr:hover {{ background: #1a1a1a; }}
    a {{ color: #1DB954; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .open-btn {{ background: #1DB954; color: #000; padding: 4px 12px; border-radius: 20px; font-weight: bold; font-size: 0.8rem; }}
    .open-btn:hover {{ background: #1ed760; text-decoration: none; }}
    .found {{ color: #1DB954; font-size: 0.8rem; }}
    .search {{ color: #b3b3b3; font-size: 0.8rem; }}
    .actions {{ margin: 1.5rem 0; display: flex; gap: 1rem; }}
    .action-btn {{ background: #1DB954; color: #000; padding: 10px 24px; border-radius: 24px; border: none;
                   cursor: pointer; font-weight: bold; font-size: 0.9rem; }}
    .action-btn:hover {{ background: #1ed760; }}
    .action-btn.secondary {{ background: #282828; color: #fff; }}
    .action-btn.secondary:hover {{ background: #333; }}
    #status {{ color: #b3b3b3; margin-top: 0.5rem; font-size: 0.85rem; }}
  </style>
</head>
<body>
  <h1>🎧 {title}</h1>
  <p class="subtitle">{len(tracks)} tracks detected</p>

  <div class="actions">
    <button class="action-btn" onclick="openAll()">▶ Open All in Spotify</button>
    <button class="action-btn secondary" onclick="openNext()">⏭ Open Next</button>
    <button class="action-btn secondary" onclick="copyList()">📋 Copy Track List</button>
  </div>
  <p id="status"></p>

  <table>
    <thead>
      <tr><th>#</th><th>Time</th><th>Track</th><th>Status</th><th></th></tr>
    </thead>
    <tbody>
{table_rows}
    </tbody>
  </table>

  <script>
    const urls = {track_urls_json};
    let currentIndex = 0;

    function openAll() {{
      if (!confirm('This will open ' + urls.length + ' tabs. Continue?')) return;
      const status = document.getElementById('status');
      let opened = 0;
      urls.forEach((url, i) => {{
        setTimeout(() => {{
          window.open(url, '_blank');
          opened++;
          status.textContent = 'Opened ' + opened + '/' + urls.length + '...';
        }}, i * 800);  // 800ms between each to avoid popup blocker
      }});
    }}

    function openNext() {{
      if (currentIndex >= urls.length) {{
        document.getElementById('status').textContent = 'All tracks opened!';
        currentIndex = 0;
        return;
      }}
      window.open(urls[currentIndex], '_blank');
      document.getElementById('status').textContent = 'Opened track ' + (currentIndex + 1) + '/' + urls.length;
      currentIndex++;
    }}

    function copyList() {{
      const lines = [];
      document.querySelectorAll('tbody tr').forEach(row => {{
        const cells = row.querySelectorAll('td');
        lines.push(cells[0].textContent + '. ' + cells[2].textContent);
      }});
      navigator.clipboard.writeText(lines.join('\\n')).then(() => {{
        document.getElementById('status').textContent = 'Track list copied to clipboard!';
      }});
    }}
  </script>
</body>
</html>"""

    with open(html_path, "w") as f:
        f.write(html)

    return html_path


# ---------------------------------------------------------------------------
# OAuth playlist creation (requires SPOTIPY_CLIENT_ID + SPOTIPY_CLIENT_SECRET)
# ---------------------------------------------------------------------------

def create_spotify_playlist(tracks: list[dict], playlist_name: str) -> str | None:
    """
    Create a Spotify playlist and add all found tracks to it.
    Requires SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET env vars.
    Returns the playlist URL, or None on failure.
    """
    client_id = os.environ.get("SPOTIPY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET")

    if not client_id or not client_secret:
        logger.warning(
            "Spotify OAuth not configured. Set SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET "
            "to create playlists automatically. See README for setup instructions."
        )
        return None

    try:
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth
    except ImportError:
        logger.warning("spotipy not installed. Run: pip install spotipy")
        return None

    redirect_uri = os.environ.get("SPOTIPY_REDIRECT_URI", "http://localhost:8888/callback")

    try:
        sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope="playlist-modify-public playlist-modify-private",
            cache_path=os.path.expanduser("~/.cache/yttoplaylist_spotify_token"),
        ))

        user = sp.current_user()
        user_id = user["id"]
        logger.info(f"Spotify: logged in as {user.get('display_name', user_id)}")

        # Collect URIs — search for tracks that don't have a URI yet
        uris = []
        for track in tracks:
            if track.get("spotify_uri"):
                uris.append(track["spotify_uri"])
                continue

            # Search via authenticated API
            q = f"{track['artist']} {track['title']}"
            results = sp.search(q=q, type="track", limit=1)
            items = results.get("tracks", {}).get("items", [])
            if items:
                uris.append(items[0]["uri"])
                track["spotify_uri"] = items[0]["uri"]
                track["spotify_url"] = items[0]["external_urls"].get("spotify", "")
            else:
                logger.debug(f"  Spotify: not found - {q}")

        if not uris:
            logger.warning("No tracks found on Spotify to add to playlist")
            return None

        # Create playlist
        playlist = sp.user_playlist_create(
            user_id,
            playlist_name,
            public=True,
            description=f"Auto-generated by yttoplaylist — {len(uris)} tracks detected via Shazam",
        )
        playlist_id = playlist["id"]
        playlist_url = playlist["external_urls"]["spotify"]

        # Add tracks (Spotify API accepts max 100 per call)
        for i in range(0, len(uris), 100):
            sp.playlist_add_items(playlist_id, uris[i:i + 100])

        logger.info(f"Spotify: created playlist '{playlist_name}' with {len(uris)} tracks")
        logger.info(f"Spotify: {playlist_url}")
        return playlist_url

    except Exception as e:
        logger.error(f"Spotify playlist creation failed: {e}")
        return None
