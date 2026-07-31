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


_PCT = re.compile(r"\[STATUS\]\s*-\s*([A-Za-z ]+?)\s+(\d+)%")


def _describe_failure(what: str, code: int, out: str, err: str) -> str:
    """Build an error a human can act on.

    TwitchDownloader writes progress to stdout and often exits nonzero with an
    EMPTY stderr, which previously produced the useless "chat download failed:"
    with nothing after the colon.
    """
    lines = [ln.strip() for ln in (err + "\n" + out).splitlines() if ln.strip()]
    signal = [ln for ln in lines
              if any(w in ln for w in ("Exception", "Invalid", "Error", "not found",
                                       "Unauthorized", "403", "404"))]
    if signal:
        detail = signal[0][:300]
    elif lines:
        detail = f"exited {code} after: {lines[-1][:200]}"
    else:
        detail = (f"exited {code} with no output — the VOD may be deleted, "
                  f"sub-only, or still live")
    return f"{what} failed: {detail}"


def _run(args: list[str], what: str, *, progress=None, timeout: int = 900):
    """Run the CLI fork-safely (posix_spawn), streaming [STATUS] progress.

    subprocess's fork path killed children with SIGSEGV when the parent was
    the threaded Streamlit process — see procs.py for the full story.
    """
    from . import procs

    def tail(line: str):
        match = _PCT.search(line)
        if match and progress:
            progress(int(match.group(2)), match.group(1).strip())

    try:
        result = procs.run([cli_path(), *args], timeout=timeout,
                           tail=tail if progress else None)
    except TimeoutError:
        raise RuntimeError(f"{what} timed out after {timeout // 60} min") from None

    if result.returncode != 0:
        raise RuntimeError(_describe_failure(what, result.returncode,
                                             result.stdout, result.stderr))
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
    progress=None,
) -> Path:
    """Download VOD chat as JSON. No video is touched — this is the cheap stage.

    A full multi-hour VOD's chat is tens of MB and takes minutes, so pass
    `progress(pct, label)` to keep the UI honest about what's happening.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    args = ["chatdownload", "--id", vod_id(video), "-o", str(out_path), "--collision", "overwrite"]
    if begin is not None:
        args += ["-b", _ts(begin)]
    if end is not None:
        args += ["-e", _ts(end)]
    if oauth:
        args += ["--oauth", oauth]
    _run(args, "chat download", progress=progress)
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError(
            "chat download produced no file — the VOD may have chat disabled or be expired"
        )
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
