"""TwitchDownloaderCLI wrapper.

Chosen over yt-dlp for one reason above all: it downloads **VOD chat**, and chat
is the strongest available signal for which moments are funny, hype, or awkward.
It also crops server-side with -b/-e, so we can pull a 60-second window out of a
6-hour broadcast without downloading the whole thing.

Run scripts/install_twitchdownloader.sh to fetch the binary into ./bin.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

from . import config

ROOT = config.ROOT
VOD_ID_RE = re.compile(r"(?:twitch\.tv/videos/)?(\d+)")


def cli_path() -> str:
    """Locate the CLI: project ./bin first, then PATH."""
    local = ROOT / "bin" / "TwitchDownloaderCLI"
    if local.exists():
        return str(local)
    found = shutil.which("TwitchDownloaderCLI")
    if found:
        return found
    raise RuntimeError(
        "TwitchDownloaderCLI not found. Run: ./scripts/install_twitchdownloader.sh"
    )


def vod_id(url_or_id: str) -> str:
    """Accept a full VOD URL or a bare id, return the id."""
    match = VOD_ID_RE.search(str(url_or_id))
    if not match:
        raise ValueError(f"Could not parse a VOD id from {url_or_id!r}")
    return match.group(1)


def _run(args: list[str], what: str):
    result = subprocess.run([cli_path(), *args], capture_output=True, text=True)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()
        detail = next(
            (ln for ln in tail if "Exception" in ln or "Invalid" in ln), tail[-1] if tail else ""
        )
        raise RuntimeError(f"{what} failed: {detail}")
    return result


def _ts(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    return f"{h}:{m:02d}:{s:02d}"


def download_chat(
    video: str,
    out_path: Path,
    *,
    begin: int | None = None,
    end: int | None = None,
    oauth: str = "",
) -> Path:
    """Download VOD chat as JSON. This is cheap — do it before touching video."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    args = ["chatdownload", "--id", vod_id(video), "-o", str(out_path), "--collision", "overwrite"]
    if begin is not None:
        args += ["-b", _ts(begin)]
    if end is not None:
        args += ["-e", _ts(end)]
    if oauth:
        args += ["--oauth", oauth]
    _run(args, "chat download")
    return out_path


def download_video(
    video: str,
    out_path: Path,
    *,
    quality: str = "480p30",
    begin: int | None = None,
    end: int | None = None,
    threads: int = 8,
    oauth: str = "",
) -> Path:
    """Download a VOD, optionally only the [begin, end] window.

    Cropping server-side is what makes highlight extraction fast: we pull just
    the candidate windows chat pointed us at, not the whole broadcast.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "videodownload", "--id", vod_id(video), "-o", str(out_path),
        "-q", quality, "-t", str(threads), "--collision", "overwrite",
    ]
    if begin is not None:
        args += ["-b", _ts(begin)]
    if end is not None:
        args += ["-e", _ts(end)]
    if oauth:
        args += ["--oauth", oauth]
    _run(args, "video download")
    if not out_path.exists():
        raise RuntimeError(f"video download reported success but {out_path} is missing")
    return out_path


def download_clip(clip_slug: str, out_path: Path, *, quality: str = "480") -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    slug = clip_slug.rstrip("/").split("/")[-1]
    _run(
        ["clipdownload", "--id", slug, "-o", str(out_path), "-q", quality,
         "--collision", "overwrite"],
        "clip download",
    )
    return out_path


def load_chat(path: Path) -> list[dict]:
    """Read a TwitchDownloader chat export and return its comments."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get("comments", [])
