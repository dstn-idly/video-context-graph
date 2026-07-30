"""TwelveLabs-only pipeline: VOD in, analyzed moments out. No chat involved.

The chat path needs a full chat export (tens of MB, minutes of waiting) before
it can say anything. This path skips it entirely: yt-dlp reports the VOD's
length, we sample evenly-spaced windows across it, TwelveLabs watches each one,
and Pegasus decides what is worth clipping.

Trade-off worth knowing: chat tells you what the *audience* reacted to, which is
the better virality signal. This tells you what is actually *on screen*, which
works on any video — no chat, no viewers, muted VODs, uploads. For a demo it is
also far faster to first result.

    from vcg import scout
    result = scout.scout_vod("2823602117", samples=5)
"""
import json
import logging
import re
import subprocess
import time

from . import clients, config, downloader, eventlog, graph
from .twitch import _ytdlp

log = logging.getLogger(__name__)

CLIPS_DIR = config.ROOT / "clips"

# Pegasus does the watching; the structure comes back in the text so we can
# parse it without a second model call.
SCOUT_PROMPT = """You are scouting a livestream recording for clip-worthy footage.

Describe what actually happens in this clip in two or three concrete sentences.
Name what you see — people, objects, actions, reactions, on-screen text.
Do not narrate generically ("a person is talking"); be specific.

Then output exactly these lines:
RATING: n/10
KIND: one of funny, hype, awkward, tense, action, routine
HOOK: a first-three-seconds hook line for a short-form clip
WHY: one sentence on the mechanism — surprise, escalation, reversal, payoff,
     secondhand embarrassment, or skill
RISK: one honest sentence on why this might flop, or "none"

Be harsh. Routine gameplay or plain talking is 2-3. Only genuinely remarkable
footage earns 7+."""

_FIELD = {
    "rating": re.compile(r"RATING:?\s*\**\s*(\d+)", re.I),
    "kind": re.compile(r"KIND:?\s*\**\s*([A-Za-z]+)", re.I),
    "hook": re.compile(r"HOOK:?\s*\**\s*(.+)", re.I),
    "why": re.compile(r"WHY:?\s*\**\s*(.+)", re.I),
    "risk": re.compile(r"RISK:?\s*\**\s*(.+)", re.I),
}

VALID_KINDS = {"funny", "hype", "awkward", "tense", "action", "routine"}


def vod_info(video: str, *, duration_s: int = 0, attempts: int = 3) -> dict:
    """Title + duration from yt-dlp metadata. No download, no chat.

    Twitch rate-limits this endpoint and returns intermittent 403s, which yt-dlp
    surfaces as a warning plus empty stdout rather than a nonzero exit — so a
    single attempt fails spuriously. Retries, then falls back to a caller-supplied
    duration (the VOD list already carries one) so a flaky metadata call cannot
    block a scan.
    """
    vid = downloader.vod_id(video)
    url = f"https://www.twitch.tv/videos/{vid}"
    last = ""

    for attempt in range(attempts):
        try:
            result = subprocess.run(
                [_ytdlp(), "-J", "--no-playlist", url],
                capture_output=True, text=True, timeout=180,
            )
        except subprocess.TimeoutExpired:
            last = "timed out"
            continue

        text = (result.stdout or "").strip()
        if text:
            try:
                data = json.loads(text)
                if not isinstance(data, dict):  # yt-dlp can emit literal null
                    raise json.JSONDecodeError("not an object", text, 0)
                length = int(data.get("duration") or 0) or duration_s
                if length > 0:
                    return {"vod_id": vid, "url": url,
                            "title": data.get("title") or f"VOD {vid}",
                            "duration_s": length}
                last = "metadata had no duration"
            except json.JSONDecodeError:
                last = "unreadable metadata"
        else:
            stderr = (result.stderr or "")
            last = ("Twitch rate-limited the metadata request (HTTP 403)"
                    if "403" in stderr else
                    (stderr.strip().splitlines() or ["no output"])[-1][:160])
        if attempt < attempts - 1:
            time.sleep(2 * (attempt + 1))  # brief backoff; 403s are transient

    if duration_s > 0:
        log.warning("vod_info fell back to caller duration for %s (%s)", vid, last)
        return {"vod_id": vid, "url": url, "title": f"VOD {vid}",
                "duration_s": int(duration_s)}

    raise RuntimeError(
        f"Could not read VOD {vid} after {attempts} tries: {last}. "
        "Twitch throttles this — wait a few seconds and retry, or pick another VOD."
    )


def parse_verdict(text: str) -> dict:
    """Pull the structured fields out of Pegasus's reply.

    Tolerant on purpose — the model drifts between "RATING: 8/10",
    "**RATING:** 8" and "Rating 8". A parse miss must not lose the verdict.
    """
    out = {"rating": 0, "kind": "action", "hook": "", "why": "", "risk": ""}
    for key, pattern in _FIELD.items():
        match = pattern.search(text or "")
        if not match:
            continue
        value = match.group(1).strip().rstrip("*").strip()
        if key == "rating":
            out["rating"] = max(0, min(10, int(value)))
        elif key == "kind":
            lowered = value.lower()
            out["kind"] = lowered if lowered in VALID_KINDS else "action"
        else:
            out[key] = value[:400]

    # Everything before the first RATING: line is the description.
    split = re.split(r"\n\s*RATING:?", text or "", maxsplit=1, flags=re.I)
    out["description"] = split[0].strip()
    return out


def plan_windows(duration: int, chunks: int) -> list[tuple[int, int]]:
    """Tile the WHOLE VOD into contiguous [start, end) windows.

    Contiguous, not sampled: sparse samples leave most of the timeline blank,
    which is both useless as a scrub bar and misleading — a gap reads as "we
    checked and there was nothing here" when nobody looked at all.
    """
    chunks = max(1, chunks)
    size = duration / chunks
    edges = [int(round(size * i)) for i in range(chunks + 1)]
    edges[-1] = duration
    return [(edges[i], edges[i + 1]) for i in range(chunks) if edges[i + 1] > edges[i]]


# Pegasus accepts up to 1 hour per video; stay well under it.
MAX_WINDOW_SECONDS = 45 * 60


def scout_vod(
    video: str,
    *,
    chunks: int = 10,
    quality: str = "480p30",
    keep_routine: bool = True,
    duration_s: int = 0,
    limit_seconds: int = 1800,
    max_mb: int = 500,
    progress=None,
) -> dict:
    """Watch the ENTIRE VOD with TwelveLabs and write what it finds to Neo4j.

    The VOD is split into `chunks` contiguous windows covering 100% of its
    runtime — every second is looked at. More chunks means finer timestamps but
    more Pegasus calls; 10 is a good balance for a ~1 hour VOD.

    No chat is involved at any point.

    Returns {vod_id, title, duration_s, url, coverage, moments: [...]}.
    """
    info = vod_info(video, duration_s=duration_s)
    vid, duration = info["vod_id"], info["duration_s"]
    node_id = f"twitch:{vid}"

    if duration <= 0:
        raise RuntimeError(f"VOD {vid} reports no duration — it may still be live.")

    full_duration = duration
    # Analyze only the opening stretch. A 7-hour VOD is hours of downloading
    # before the first verdict; the first 30 minutes is enough to prove the
    # product and keeps the whole run inside the size budget.
    if limit_seconds and duration > limit_seconds:
        duration = limit_seconds
        eventlog.emit("pipeline",
                      f"Capping analysis to first {limit_seconds // 60} min "
                      f"of {full_duration // 60} min VOD")

    # 480p30 runs ~11 MB/min; keep the whole run under the download budget.
    est_mb = int(duration / 60 * 11)
    if est_mb > max_mb:
        duration = int(max_mb / 11 * 60)
        eventlog.emit("pipeline",
                      f"Trimming to {duration // 60} min to stay under {max_mb} MB "
                      f"(estimated {est_mb} MB)")

    # Force enough chunks that no single window exceeds the analysis ceiling.
    chunks = max(chunks, -(-duration // MAX_WINDOW_SECONDS))
    windows = plan_windows(duration, chunks)
    eventlog.emit("twitch", f"{info['title'][:60]} — {len(windows)} windows over "
                            f"{duration // 60} min", vod=vid)

    index_id = config.require("TWELVELABS_INDEX_ID")

    # Record the video up front so the UI has something even if a window fails.
    graph.init_schema()
    graph.upsert_video(node_id, info["title"], "", info["url"])
    try:
        with graph.session() as s:
            s.run(
                "MATCH (v:Video {video_id: $id}) SET v.duration_s = $d, "
                "v.analyzed_at = coalesce(v.analyzed_at, datetime())",
                id=node_id, d=duration,
            )
    except Exception as exc:
        log.warning("could not set duration: %s", exc)

    downloaded_mb = [0.0]
    moments = []
    for i, (start, end) in enumerate(windows, start=1):
        entry = {"start": start, "end": end, "rating": 0, "kind": "action",
                 "description": "", "hook": "", "why": "", "risk": "",
                 "tl_video_id": None, "path": None, "saved": False, "error": None}
        moments.append(entry)  # exactly one entry per window, always

        try:
            if progress:
                progress(i, len(windows),
                         f"{start // 60}:{start % 60:02d}–{end // 60}:{end % 60:02d}")
            path = CLIPS_DIR / vid / f"scout_{start}s.mp4"
            if not path.exists():
                with eventlog.step("twitch", f"downloading {start//60}:{start%60:02d}"
                                             f"-{end//60}:{end%60:02d} @ {quality}"):
                    downloader.download_video(vid, path, quality=quality,
                                              begin=start, end=end)
            size_mb = path.stat().st_size / 1_048_576 if path.exists() else 0
            downloaded_mb[0] += size_mb
            entry["path"] = str(path)
            entry["size_mb"] = round(size_mb, 1)
            eventlog.emit("twitch", f"window {i}/{len(windows)} ready "
                                    f"({size_mb:.0f} MB, {downloaded_mb[0]:.0f} MB total)")

            with eventlog.step("twelvelabs", f"indexing window {i}/{len(windows)}"):
                tl_id = clients.upload_video(index_id, path=str(path))
            entry["tl_video_id"] = tl_id
            with eventlog.step("twelvelabs", f"Pegasus watching window {i}/{len(windows)}"):
                verdict_text = clients.analyze(tl_id, SCOUT_PROMPT)
            entry.update(parse_verdict(verdict_text))
            eventlog.emit("twelvelabs",
                          f"verdict: {entry['kind']} {entry['rating']}/10 — "
                          f"{(entry['description'] or '')[:70]}")
        except Exception as exc:
            entry["error"] = str(exc)
            log.warning("scout window %ss failed: %s", start, exc)
            _write_json(CLIPS_DIR / vid / "scout.json", moments)
            continue

        if entry["rating"] < 6 and not keep_routine:
            continue
        try:
            _save(node_id, entry)
            entry["saved"] = True
            eventlog.emit("neo4j", f"saved moment {start//60}:{start%60:02d} "
                                   f"({entry['kind']} {entry['rating']}/10)")
        except Exception as exc:
            entry["error"] = f"analyzed, but not saved to graph: {exc}"

        # Checkpoint after every window: these verdicts cost money, and a
        # long run must never lose everything to one late failure.
        _write_json(CLIPS_DIR / vid / "scout.json", moments)

    covered = sum(m["end"] - m["start"] for m in moments if m["tl_video_id"])
    eventlog.emit("pipeline", f"scan complete — {len(moments)} windows, "
                              f"{downloaded_mb[0]:.0f} MB downloaded")
    return {**info, "analyzed_s": duration, "full_duration_s": full_duration,
            "downloaded_mb": round(downloaded_mb[0], 1),
            "coverage": round(covered / duration * 100, 1), "moments": moments}


def local_duration(path) -> int:
    """Seconds of media in a local file, via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {result.stderr[-200:]}")
    return int(float(result.stdout.strip()))


def scout_local(
    path,
    *,
    title: str = "",
    video_id: str = "",
    chunks: int = 8,
    source_url: str = "",
    keep_routine: bool = True,
    progress=None,
) -> dict:
    """Same scan, but over a file already on disk. No Twitch, no download.

    ffmpeg splits the file into contiguous chunks with a stream copy (no
    re-encode, so it is near-instant), and each chunk goes to TwelveLabs. Use
    this when you already have the footage — it removes the slowest and
    flakiest stage of the pipeline entirely.
    """
    from pathlib import Path

    src = Path(path).expanduser()
    if not src.exists():
        raise RuntimeError(f"No such file: {src}")

    duration = local_duration(src)
    if duration <= 0:
        raise RuntimeError(f"{src.name} reports no duration")

    vid = video_id or re.sub(r"\W+", "", src.stem)[:40] or "local"
    node_id = f"local:{vid}" if not video_id else f"twitch:{video_id}"
    display = title or src.stem

    index_id = config.require("TWELVELABS_INDEX_ID")
    windows = plan_windows(duration, max(1, chunks))
    eventlog.emit("pipeline", f"{display[:50]} — {len(windows)} windows over "
                              f"{duration // 60}:{duration % 60:02d}", file=src.name)

    graph.init_schema()
    graph.upsert_video(node_id, display, "", source_url)
    try:
        with graph.session() as s:
            s.run("MATCH (v:Video {video_id: $id}) SET v.duration_s = $d, "
                  "v.analyzed_at = coalesce(v.analyzed_at, datetime())",
                  id=node_id, d=duration)
        eventlog.emit("neo4j", f"video node ready ({duration // 60}:{duration % 60:02d})")
    except Exception as exc:
        log.warning("could not set duration: %s", exc)

    out_dir = CLIPS_DIR / vid / "local"
    out_dir.mkdir(parents=True, exist_ok=True)

    moments = []
    for i, (start, end) in enumerate(windows, start=1):
        entry = {"start": start, "end": end, "rating": 0, "kind": "action",
                 "description": "", "hook": "", "why": "", "risk": "",
                 "tl_video_id": None, "path": None, "saved": False, "error": None}
        moments.append(entry)
        try:
            if progress:
                progress(i, len(windows),
                         f"{start // 60}:{start % 60:02d}–{end // 60}:{end % 60:02d}")
            # length in the name: a cached chunk from a different chunking
            # must never be silently reused as if it were this window.
            chunk = out_dir / f"cut_{start}s_{end - start}s.mp4"
            if not chunk.exists():
                with eventlog.step("pipeline", f"cutting {start // 60}:{start % 60:02d}"
                                               f"–{end // 60}:{end % 60:02d}"):
                    # -ss before -i seeks fast; re-encode keeps cuts frame-accurate
                    # and guarantees TwelveLabs gets a self-contained playable file.
                    subprocess.run(
                        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                         "-ss", str(start), "-i", str(src), "-t", str(end - start),
                         "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                         "-c:a", "aac", "-movflags", "+faststart", str(chunk)],
                        check=True, timeout=600,
                    )
            entry["path"] = str(chunk)

            with eventlog.step("twelvelabs", f"indexing window {i}/{len(windows)}"):
                tl_id = clients.upload_video(index_id, path=str(chunk))
            entry["tl_video_id"] = tl_id
            with eventlog.step("twelvelabs", f"Pegasus watching {i}/{len(windows)}"):
                entry.update(parse_verdict(clients.analyze(tl_id, SCOUT_PROMPT)))
            eventlog.emit("twelvelabs",
                          f"verdict: {entry['kind']} {entry['rating']}/10 — "
                          f"{(entry['description'] or '')[:70]}")
        except Exception as exc:
            entry["error"] = str(exc)
            eventlog.emit("pipeline", f"window {i} failed: {exc}")
            _write_json(out_dir / "scout.json", moments)
            continue

        if entry["rating"] < 6 and not keep_routine:
            continue
        try:
            _save(node_id, entry)
            entry["saved"] = True
            eventlog.emit("neo4j", f"saved moment {start // 60}:{start % 60:02d} "
                                   f"({entry['kind']} {entry['rating']}/10)")
        except Exception as exc:
            entry["error"] = f"analyzed, but not saved: {exc}"
        _write_json(out_dir / "scout.json", moments)

    covered = sum(m["end"] - m["start"] for m in moments if m["tl_video_id"])
    eventlog.emit("pipeline", f"scan complete — {len(moments)} windows analyzed")
    return {"vod_id": vid, "title": display, "url": source_url,
            "duration_s": duration, "analyzed_s": duration,
            "coverage": round(covered / duration * 100, 1), "moments": moments}


def _save(node_id: str, entry: dict):
    """Persist one watched window as a Moment the rest of the app understands."""
    with graph.session() as s:
        s.run(
            """
            MATCH (v:Video {video_id: $node_id})
            MERGE (m:Moment {moment_id: $mid})
            SET m.kind = $kind, m.score = $score, m.start = $start, m.end = $end,
                m.detector = 'twelvelabs',
                m.reason = $why,
                m.ai_verdict = $description,
                m.hook = $hook, m.risk = $risk, m.tl_video_id = $tl
            MERGE (v)-[:HAS_MOMENT]->(m)
            """,
            node_id=node_id, mid=f"{node_id}:v{entry['start']}",
            kind=entry["kind"], score=float(entry["rating"]) * 10,
            start=entry["start"], end=entry["end"],
            why=entry["why"] or "TwelveLabs watched this window",
            description=entry["description"], hook=entry["hook"],
            risk=entry["risk"], tl=entry["tl_video_id"],
        )


def _write_json(path, payload):
    """Results hit disk before the graph — Pegasus verdicts are already paid for."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2))
    except Exception as exc:
        log.warning("could not write %s: %s", path, exc)
