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


def analyze_vod(video: str, *, cache: bool = True, oauth: str = "") -> dict:
    """Stage 1 — chat only. Fast, and the whole timeline comes from here."""
    vid = downloader.vod_id(video)
    chat_path = CLIPS_DIR / vid / "chat.json"

    if not (cache and chat_path.exists()):
        downloader.download_chat(vid, chat_path, oauth=oauth)

    comments = downloader.load_chat(chat_path)
    buckets = highlights.build_timeline(comments)
    peaks = highlights.find_peaks(buckets, comments)
    dead = highlights.find_dead_spots(buckets)

    return {
        "vod_id": vid,
        "url": f"https://www.twitch.tv/videos/{vid}",
        "buckets": buckets,
        "peaks": peaks,
        "dead_spots": dead,
        "summary": highlights.summarize(buckets, peaks, dead),
        "chat_path": str(chat_path),
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
            enriched.append({**clip, "tl_video_id": tl_id, "ai_verdict": described})
        except Exception as exc:
            enriched.append({**clip, "ai_verdict": None, "error": str(exc)})

    graph.rebuild_co_occurrences()
    return enriched


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
