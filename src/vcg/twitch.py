"""Twitch Helix API + VOD/clip acquisition.

Scope note: point this at a channel whose content you own or have permission to
use. Twitch's Terms of Service prohibit downloading broadcasts you don't have
rights to, so `--channel` is always explicit — there is no bulk crawl here.

Auth uses an *app access token* (client-credentials grant), which is enough for
every public read endpoint we need. Register an app at
https://dev.twitch.tv/console/apps to get a client id + secret.
"""
import json
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import config


def _ytdlp() -> str:
    """yt-dlp binary: prefer the running interpreter's venv, then PATH."""
    candidate = Path(sys.executable).parent / "yt-dlp"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("yt-dlp")
    if found:
        return found
    raise RuntimeError("yt-dlp not found — pip install yt-dlp")

HELIX = "https://api.twitch.tv/helix"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"

_token: str | None = None
_token_expires: float = 0.0


# --------------------------------------------------------------------------
# Helix plumbing
# --------------------------------------------------------------------------

def _app_token() -> str:
    """Client-credentials token, cached until shortly before it expires."""
    global _token, _token_expires
    if _token and time.time() < _token_expires:
        return _token

    body = urllib.parse.urlencode(
        {
            "client_id": config.require("TWITCH_CLIENT_ID"),
            "client_secret": config.require("TWITCH_CLIENT_SECRET"),
            "grant_type": "client_credentials",
        }
    ).encode()

    with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=body)) as resp:
        payload = json.load(resp)

    _token = payload["access_token"]
    _token_expires = time.time() + payload.get("expires_in", 3600) - 60
    return _token


def _get(path: str, **params) -> dict:
    url = f"{HELIX}/{path}?" + urllib.parse.urlencode(
        {k: v for k, v in params.items() if v is not None}, doseq=True
    )
    req = urllib.request.Request(
        url,
        headers={
            "Client-Id": config.TWITCH_CLIENT_ID,
            "Authorization": f"Bearer {_app_token()}",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"Twitch API {exc.code} on /{path}: {detail}") from exc


def _paged(path: str, limit: int, **params) -> list[dict]:
    """Walk Helix cursor pagination until we have `limit` items."""
    items: list[dict] = []
    cursor = None
    while len(items) < limit:
        page = _get(path, first=min(100, limit - len(items)), after=cursor, **params)
        batch = page.get("data", [])
        if not batch:
            break
        items.extend(batch)
        cursor = page.get("pagination", {}).get("cursor")
        if not cursor:
            break
    return items[:limit]


# --------------------------------------------------------------------------
# Public reads
# --------------------------------------------------------------------------

def get_user(login: str) -> dict:
    """Resolve a channel login name to its user record (id, display name, ...)."""
    data = _get("users", login=login).get("data", [])
    if not data:
        raise RuntimeError(f"No Twitch channel named '{login}'.")
    return data[0]


def list_vods(user_id: str, limit: int = 10, video_type: str = "archive") -> list[dict]:
    """List a channel's videos.

    video_type: 'archive' (past broadcasts), 'highlight', 'upload', or 'all'.
    """
    return _paged("videos", limit, user_id=user_id, type=video_type, sort="time")


def list_clips(broadcaster_id: str, limit: int = 20) -> list[dict]:
    """List a channel's most-viewed clips. Clips are short, which suits demos."""
    return _paged("clips", limit, broadcaster_id=broadcaster_id)


# twitch.tv paths that are site features, not channels.
RESERVED_PATHS = {
    "search", "directory", "videos", "settings", "subscriptions", "wallet",
    "drops", "downloads", "friends", "inventory", "prime", "store", "turbo",
    "u", "popout", "team", "teams", "p", "products",
}


def channel_from_url(url_or_name: str) -> str:
    """Pull a channel login out of whatever the user pasted.

    Handles the plain channel page, /videos and other sub-pages, a bare name,
    and the site search URL (?term=). Raises with a useful message when the
    input is a VOD link or some other non-channel page.
    """
    raw = url_or_name.strip().rstrip("/")
    if not raw:
        raise RuntimeError("Enter a Twitch channel URL or name.")

    if "twitch.tv" not in raw:
        return raw.lstrip("@").split("/", 1)[0]

    tail = raw.split("twitch.tv/", 1)[1]
    path, _, query = tail.partition("?")
    segments = [seg for seg in path.split("/") if seg]
    first = (segments[0] if segments else "").lower()

    # Search URL: the channel the user meant is in ?term=
    if first == "search" or (not first and query):
        params = urllib.parse.parse_qs(query)
        term = (params.get("term") or params.get("q") or [""])[0].strip()
        if not term:
            raise RuntimeError(
                "That's a Twitch search page with no search term. Paste the "
                "channel's own URL, e.g. https://www.twitch.tv/<channel>."
            )
        return term.lstrip("@").split("/", 1)[0]

    if first == "videos":
        raise RuntimeError(
            "That's a single VOD link, not a channel. Use the "
            "“Or paste one VOD URL directly” box below for it."
        )

    if not first or first in RESERVED_PATHS:
        raise RuntimeError(
            f"'{raw}' isn't a channel page. Paste the channel's own URL, "
            "e.g. https://www.twitch.tv/<channel>."
        )

    return first.lstrip("@")


def list_vods_scrape(channel: str, limit: int = 12) -> list[dict]:
    """List a channel's VODs via yt-dlp — no Twitch API credentials needed.

    Returns the same shape the Helix path produces (id/url/title/duration
    seconds/thumbnail), so the UI can use either interchangeably.
    """
    name = channel_from_url(channel)
    cmd = [
        _ytdlp(), "-J", "--flat-playlist",
        "--playlist-end", str(limit),
        f"https://www.twitch.tv/{name}/videos?filter=archives&sort=time",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Timed out listing VODs for '{name}' — try again.") from None

    if result.returncode != 0 or not result.stdout.strip():
        # Report everything we have: an empty stderr with a nonzero exit is
        # otherwise indistinguishable from a real failure.
        detail = (result.stderr or "").strip() or (result.stdout or "").strip()
        detail = detail.splitlines()[-1][:300] if detail else f"exit code {result.returncode}, no output"
        raise RuntimeError(f"Could not list VODs for '{name}': {detail}")

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(
            f"Unexpected response listing VODs for '{name}': {result.stdout[:200]}"
        ) from None
    vods = []
    for entry in (payload.get("entries") or [])[:limit]:
        vid = str(entry.get("id") or "").lstrip("v")
        if not vid:
            continue
        vods.append({
            "id": vid,
            "url": f"https://www.twitch.tv/videos/{vid}",
            "title": entry.get("title") or f"VOD {vid}",
            "duration_s": int(entry.get("duration") or 0),
            "thumbnail": entry.get("thumbnails", [{}])[-1].get("url", "") if entry.get("thumbnails") else "",
        })
    return vods


def list_vods_any(channel: str, limit: int = 12) -> list[dict]:
    """Helix when credentials exist (richer metadata), yt-dlp scrape otherwise."""
    if config.TWITCH_CLIENT_ID and config.TWITCH_CLIENT_SECRET:
        user = get_user(channel_from_url(channel))
        rows = list_vods(user["id"], limit=limit)
        return [
            {
                "id": v["id"],
                "url": v["url"],
                "title": v["title"],
                "duration_s": parse_duration(v.get("duration", "")),
                "thumbnail": (v.get("thumbnail_url") or "").replace("%{width}", "320").replace("%{height}", "180"),
            }
            for v in rows
        ]
    return list_vods_scrape(channel, limit)


def parse_duration(duration: str) -> int:
    """Twitch reports VOD length as '3h21m17s'. Return total seconds."""
    total, number = 0, ""
    units = {"h": 3600, "m": 60, "s": 1}
    for char in duration:
        if char.isdigit():
            number += char
        elif char in units:
            total += int(number or 0) * units[char]
            number = ""
    return total


# --------------------------------------------------------------------------
# Download + segment
# --------------------------------------------------------------------------

def download(url: str, out_dir: Path, *, max_height: int = 720) -> Path:
    """Download a Twitch VOD or clip with yt-dlp. Returns the local mp4 path.

    Capped at 720p on purpose: TwelveLabs indexes fine at that resolution and
    it's several times faster to download and upload than source quality.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    template = str(out_dir / "%(id)s.%(ext)s")

    cmd = [
        _ytdlp(),
        "-f", f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]/best",
        "--merge-output-format", "mp4",
        "-o", template,
        "--no-playlist",
        "--print", "after_move:filepath",
        "--no-simulate",
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed:\n{result.stderr[-1500:]}")

    paths = [Path(p) for p in result.stdout.strip().splitlines() if p.strip()]
    existing = [p for p in paths if p.exists()]
    if not existing:
        raise RuntimeError(f"yt-dlp reported success but no file found. stdout:\n{result.stdout}")
    return existing[-1]


def probe_duration(path: Path) -> float:
    """Seconds of media in a local file, via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr[-500:]}")
    return float(result.stdout.strip())


# Pegasus (video analysis) accepts up to 1 hour per video, so stay under it.
MAX_SEGMENT_SECONDS = 45 * 60


def segment(path: Path, seconds: int = MAX_SEGMENT_SECONDS) -> list[tuple[Path, int]]:
    """Split a long video into chunks.

    Returns [(chunk_path, offset_seconds), ...]. The offset is what makes graph
    timestamps line up with the original VOD — a scene at 0:30 of chunk 3 is
    really at 1:30:30 of the broadcast, and the ingest step needs to know that.

    Short videos are returned as-is with offset 0, no re-encoding.
    """
    duration = probe_duration(path)
    if duration <= seconds:
        return [(path, 0)]

    out_dir = path.parent / f"{path.stem}_segments"
    out_dir.mkdir(exist_ok=True)

    # Stream copy — no re-encode, so this is fast even on multi-hour VODs.
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(path),
         "-c", "copy", "-map", "0",
         "-f", "segment", "-segment_time", str(seconds),
         "-reset_timestamps", "1",
         str(out_dir / f"{path.stem}_%03d.mp4")],
        check=True,
    )

    chunks = sorted(out_dir.glob(f"{path.stem}_*.mp4"))
    if not chunks:
        raise RuntimeError(f"ffmpeg produced no segments for {path}")

    # Measure each chunk rather than assuming exact `seconds` boundaries —
    # segment cuts land on keyframes, so real lengths drift.
    result, offset = [], 0
    for chunk in chunks:
        result.append((chunk, offset))
        offset += int(round(probe_duration(chunk)))
    return result
