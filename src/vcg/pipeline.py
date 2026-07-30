"""End-to-end: VOD -> chat analysis -> timeline -> clips -> TwelveLabs -> graph.

Staged deliberately, because the stages have wildly different costs:

  1. analyze_vod()   chat only. Seconds, no video. Produces the timeline,
                     the peaks, and the dead spots.
  2. clip_moments()  downloads *only* the peak windows using TwitchDownloader's
                     server-side crop. Ten 35s clips out of a 6-hour VOD is a
                     ~6 minute download, not a 6 hour one.
  3. enrich_clips()  sends those short clips to TwelveLabs and the graph.

You can stop after stage 1 and still have the whole coaching timeline.
"""
import json
from pathlib import Path

from . import clients, config, downloader, graph, highlights

CLIPS_DIR = config.ROOT / "clips"


def analyze_vod(video: str, *, title: str = "", cache: bool = True, oauth: str = "") -> dict:
    """Stage 1 — chat only. Fast, and the whole timeline comes from here."""
    vid = downloader.vod_id(video)
    chat_path = CLIPS_DIR / vid / "chat.json"

    if not (cache and chat_path.exists()):
        downloader.download_chat(vid, chat_path, oauth=oauth)

    comments = downloader.load_chat(chat_path)
    buckets = highlights.build_timeline(comments)
    peaks = highlights.find_peaks(buckets, comments)
    dead = highlights.find_dead_spots(buckets)
    summary = highlights.summarize(buckets, peaks, dead)
    url = f"https://www.twitch.tv/videos/{vid}"

    # Persist to Neo4j so the dashboard and cross-stream queries see this run.
    # Best-effort: the timeline still renders if the graph is unreachable.
    persisted = False
    try:
        graph.save_performance(f"twitch:{vid}", title or f"VOD {vid}", url, summary, peaks, dead)
        persisted = True
    except Exception:
        pass

    return {
        "vod_id": vid,
        "url": url,
        "buckets": buckets,
        "peaks": peaks,
        "dead_spots": dead,
        "summary": summary,
        "chat_path": str(chat_path),
        "persisted": persisted,
    }


def clip_moments(
    vod: str,
    moments: list[highlights.Moment],
    *,
    quality: str = "480p30",
    oauth: str = "",
    progress=None,
) -> list[dict]:
    """Stage 2 — download just the peak windows."""
    vid = downloader.vod_id(vod)
    out_dir = CLIPS_DIR / vid
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for i, moment in enumerate(moments, start=1):
        name = f"{i:02d}_{moment.kind}_{moment.start}s.mp4"
        path = out_dir / name
        if progress:
            progress(i, len(moments), name)
        try:
            if not path.exists():
                downloader.download_video(
                    vid, path,
                    quality=quality,
                    begin=moment.start,
                    end=moment.end,
                    oauth=oauth,
                )
            results.append({**moment.to_dict(), "path": str(path), "error": None})
        except Exception as exc:
            results.append({**moment.to_dict(), "path": None, "error": str(exc)})

    (out_dir / "moments.json").write_text(json.dumps(results, indent=2))
    return results


def enrich_clips(vod: str, clips: list[dict], *, title: str = "", progress=None) -> list[dict]:
    """Stage 3 — TwelveLabs watches each clip and confirms what actually happened.

    Chat tells us *that* something happened; this tells us *what*. Disagreements
    are useful signal, so the model's verdict is stored alongside chat's guess
    rather than replacing it.
    """
    index_id = config.require("TWELVELABS_INDEX_ID")
    vid = downloader.vod_id(vod)
    node_id = f"twitch:{vid}"
    url = f"https://www.twitch.tv/videos/{vid}"

    graph.init_schema()
    graph.upsert_video(node_id, title or f"Twitch VOD {vid}", "", url)

    enriched = []
    for i, clip in enumerate(clips, start=1):
        if not clip.get("path"):
            enriched.append(clip)
            continue
        if progress:
            progress(i, len(clips), Path(clip["path"]).name)
        try:
            tl_id = clients.upload_video(index_id, path=clip["path"])
            described = clients.analyze(
                tl_id,
                "Describe what happens in this clip in two sentences. Then say whether it is "
                "funny, hype, awkward, tense, or routine, and rate 1-10 how likely it is to "
                "go viral as a short. Be blunt — most clips are not viral.",
            )
            graph.upsert_scene(node_id, {
                "scene_id": f"{vid}:{clip['start']}",
                "start": clip["start"],
                "end": clip["end"],
                "description": described,
                "entities": [],
                "topics": [clip["kind"]],
                "tl_video_id": tl_id,
            })
            graph.set_moment_verdict(node_id, clip["start"], described, tl_id)
            enriched.append({**clip, "tl_video_id": tl_id, "ai_verdict": described})
        except Exception as exc:
            enriched.append({**clip, "ai_verdict": None, "error": str(exc)})

    graph.rebuild_co_occurrences()
    return enriched


SCOUT_PROMPT = """You are scouting a livestream recording for clip-worthy footage.
Describe the most notable thing that happens in this clip in two sentences.
Then on a new line write exactly: RATING: n/10
where n rates how exciting, funny, or unusual the footage is for a short-form
clip. Be harsh — routine gameplay or talking is 2-3, genuinely remarkable
footage is 7+."""


def visual_scout(vod: str, *, samples: int = 4, window: int = 40,
                 quality: str = "480p30", progress=None) -> list[dict]:
    """TwelveLabs finds moments by WATCHING — no chat involved.

    Chat only reacts to what the streamer's audience noticed. This samples
    evenly-spaced windows across the whole VOD, has Pegasus rate each one,
    and keeps the remarkable footage. It catches what chat slept through.
    """
    import re

    vid = downloader.vod_id(vod)
    node_id = f"twitch:{vid}"
    rows = graph.run_cypher(
        "MATCH (v:Video {video_id: $id}) RETURN v.duration_s AS d", {"id": node_id}
    )
    duration = int(rows[0]["d"]) if rows and rows[0].get("d") else 0
    if duration <= window * 2:
        raise RuntimeError("Analyze the VOD first so its duration is in the graph.")

    index_id = config.require("TWELVELABS_INDEX_ID")
    # Skip the first/last 3% (intros and raid-outs), spread the rest evenly.
    margin = int(duration * 0.03)
    span = duration - 2 * margin - window
    offsets = [margin + int(span * i / max(1, samples - 1)) for i in range(samples)]

    finds = []
    for i, off in enumerate(offsets, start=1):
        if progress:
            progress(i, samples, f"watching {off // 60}m mark")
        try:
            path = CLIPS_DIR / vid / f"scout_{off}s.mp4"
            if not path.exists():
                downloader.download_video(vid, path, quality=quality,
                                          begin=off, end=off + window)
            tl_id = clients.upload_video(index_id, path=str(path))
            verdict = clients.analyze(tl_id, SCOUT_PROMPT).strip()
            match = re.search(r"RATING:\s*(\d+)", verdict)
            rating = int(match.group(1)) if match else 0
            finds.append({"start": off, "end": off + window, "rating": rating,
                          "verdict": verdict, "tl_video_id": tl_id, "path": str(path)})
            if rating >= 6:
                graph.save_visual_moment(node_id, off, off + window,
                                         float(rating * 10), verdict, tl_id)
        except Exception as exc:
            finds.append({"start": off, "end": off + window, "rating": 0,
                          "verdict": f"failed: {exc}", "tl_video_id": None, "path": None})
    return sorted(finds, key=lambda f: f["rating"], reverse=True)


def timeline_rows(buckets: list[highlights.Bucket]) -> list[dict]:
    """Flatten buckets for charting."""
    return [
        {
            "minute": b.start / 60,
            "seconds": b.start,
            "heat": round(b.heat, 1),
            "messages": b.messages,
            "chatters": b.chatters,
            **{k: b.categories.get(k, 0) for k in highlights.CATEGORIES},
        }
        for b in buckets
    ]
