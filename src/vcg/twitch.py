"""Twitch Helix API + VOD/clip acquisition.

Scope note: point this at a channel whose content you own or have permission to
use. Twitch's Terms of Service prohibit downloading broadcasts you don't have
rights to, so `--channel` is always explicit — there is no bulk crawl here.

Auth uses an *app access token* (client-credentials grant), which is enough for
every public read endpoint we need. Register an app at
https://dev.twitch.tv/console/apps to get a client id + secret.
"""
import json
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import config

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
        "yt-dlp",
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
