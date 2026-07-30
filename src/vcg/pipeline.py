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
import logging
import re
from pathlib import Path

from . import clients, config, downloader, graph, highlights

CLIPS_DIR = config.ROOT / "clips"

log = logging.getLogger(__name__)


def _write_json(path: Path, payload) -> None:
    """Persist a stage's results next to its clips. Never fatal.

    Every stage past chat analysis costs real money (downloads, TwelveLabs
    indexing, Pegasus verdicts), so results land on disk before they land in
    Neo4j — a graph outage must never throw away work we already paid for.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2))
    except Exception as exc:
        log.warning("could not write %s: %s", path, exc)


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


# ---------------------------------------------------------------------------
# Entity extraction — what turns a moment log into a context graph.
#
# TwelveLabs describes a clip in prose. Prose does not join across streams:
# "Kai reacts to the chat" and "KaiCenat laughs at chat" are unrelated strings.
# OpenAI turns that prose into typed names, graph.upsert_scene MERGEs them on a
# normalized key, and the SAME real-world entity seen in two different VODs
# collapses onto ONE node. That is the whole premise — the graph has to get
# richer as VODs are added, not just longer.
#
# Canonicalization is therefore the entire job of this prompt: normalize_key()
# in graph.py only handles casing, punctuation, articles and plurals. Semantic
# aliasing ("Kai" -> "Kai Cenat", "GTA" -> "Grand Theft Auto V") has to happen
# here or the merge never fires.
# ---------------------------------------------------------------------------

ENTITY_SYSTEM = """You extract graph nodes from a description of one short clip
from a livestream. Return the entities and topics that the description actually
supports — nothing inferred, nothing invented.

CANONICALIZE every entity name, because the same name string is how two
different streams end up pointing at the same node:
- Use the most common real-world name for the thing, expanded and spelled the
  way most people would write it: "Kai Cenat" not "Kai" or "kaicenat",
  "Grand Theft Auto V" not "GTA 5", "PlayStation 5" not "ps5".
- Title Case. Singular, never plural. No leading article ("Nintendo Switch",
  not "The Nintendo Switch").
- No possessives, no descriptive padding: "Elden Ring", not "the game Elden
  Ring he is playing".

Skip anything that is not a specific, identifiable thing. Generic roles with no
identity ("the streamer", "a viewer", "some guy", "the game") are NOT entities —
drop them unless the context below tells you the actual name.

Types: person, object, place, organization, other.

Topics are short lowercase themes of the clip ("speedrun", "jump scare",
"donation reaction"), not entity names repeated. At most five."""

ENTITY_SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["person", "object", "place", "organization", "other"],
                    },
                },
                "required": ["name", "type"],
                "additionalProperties": False,
            },
        },
        "topics": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["entities", "topics"],
    "additionalProperties": False,
}

ENTITY_TYPES = {"person", "object", "place", "organization", "other"}


def extract_entities(description: str, *, context: str = "") -> tuple[list[dict], list[str]]:
    """Prose -> ([{name, type}], [topic]) via OpenAI structured outputs.

    `context` is the stream/VOD title, which is usually what resolves "the
    streamer" into a real name the next VOD can merge against.
    """
    if not (description or "").strip():
        return [], []

    user = description if not context else f"Stream context: {context}\n\nClip description:\n{description}"
    response = clients.openai_client().chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": ENTITY_SYSTEM},
            {"role": "user", "content": user},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "clip_entities", "schema": ENTITY_SCHEMA, "strict": True},
        },
    )
    data = json.loads(response.choices[0].message.content or "{}")

    entities, seen = [], set()
    for ent in data.get("entities") or []:
        if not isinstance(ent, dict):
            continue
        name = str(ent.get("name") or "").strip()
        if not name:
            continue
        key = graph.normalize_key(name)  # dedupe the way the graph will merge
        if not key or key in seen:
            continue
        seen.add(key)
        kind = str(ent.get("type") or "other").strip().lower()
        entities.append({"name": name, "type": kind if kind in ENTITY_TYPES else "other"})

    topics, seen_t = [], set()
    for topic in data.get("topics") or []:
        if not isinstance(topic, str):
            continue
        name = topic.strip()
        key = graph.normalize_key(name)
        if not name or not key or key in seen_t:
            continue
        seen_t.add(key)
        topics.append(name)

    return entities, topics


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
            # Own try/except: a bad extraction must cost us the entities, not
            # the clip. An empty list still writes a perfectly good Scene.
            try:
                entities, topics = extract_entities(described, context=title)
            except Exception as exc:
                log.warning("entity extraction failed for %s@%ss: %s", vid, clip["start"], exc)
                entities, topics = [], []

            # The chat detector's own label stays a topic; extraction adds to it.
            scene_topics, seen = [], set()
            for topic in [clip.get("kind", ""), *topics]:
                key = graph.normalize_key(topic or "")
                if key and key not in seen:
                    seen.add(key)
                    scene_topics.append(topic)

            graph.upsert_scene(node_id, {
                "scene_id": f"{vid}:{clip['start']}",
                "start": clip["start"],
                "end": clip["end"],
                "description": described,
                "entities": entities,
                "topics": scene_topics,
                "tl_video_id": tl_id,
            })
            graph.set_moment_verdict(node_id, clip["start"], described, tl_id)
            enriched.append({**clip, "tl_video_id": tl_id, "ai_verdict": described,
                             "entities": entities, "topics": scene_topics})
        except Exception as exc:
            enriched.append({**clip, "ai_verdict": None, "error": str(exc)})

    _write_json(CLIPS_DIR / vid / "enriched.json", enriched)
    graph.rebuild_co_occurrences()
    return enriched


SCOUT_PROMPT = """You are scouting a livestream recording for clip-worthy footage.
Describe the most notable thing that happens in this clip in two sentences.
Then on a new line write exactly: RATING: n/10
where n rates how exciting, funny, or unusual the footage is for a short-form
clip. Be harsh — routine gameplay or talking is 2-3, genuinely remarkable
footage is 7+."""


# Pegasus is asked for "RATING: n/10" but is prose-trained, so it drifts:
# "**RATING:** 8", "Rating - 7/10", "rating 8 / 10". One rigid pattern silently
# scored every one of those 0 and threw away a clip we had already paid to index.
# Anything between the word and the number is treated as decoration: colons,
# dashes, markdown emphasis, newlines. A trailing "/10" needs no special case —
# \d{1,2} stops at the slash.
RATING_RE = re.compile(r"rating[\s:*_\-–—]{0,8}(\d{1,2})", re.IGNORECASE)

KEEP_RATING = 6


def parse_rating(verdict: str) -> int:
    """Pull the 0-10 score out of a Pegasus verdict. Unparseable -> 0.

    Takes the LAST match: the prompt puts the rating on the final line, and the
    two description sentences above it may well contain the word "rating".
    """
    matches = RATING_RE.findall(verdict or "")
    if not matches:
        return 0
    return max(0, min(10, int(matches[-1])))


def visual_scout(vod: str, *, samples: int = 4, window: int = 40,
                 quality: str = "480p30", duration_s: int = 0,
                 progress=None) -> list[dict]:
    """TwelveLabs finds moments by WATCHING — no chat involved.

    Chat only reacts to what the streamer's audience noticed. This samples
    evenly-spaced windows across the whole VOD, has Pegasus rate each one,
    and keeps the remarkable footage. It catches what chat slept through.

    Args:
        duration_s: True length of the broadcast, if you know it — e.g. the
            `duration_s` from twitch.list_vods_any(). Strongly preferred over
            the fallback: the graph's Video.duration_s comes from the last chat
            bucket, and chat almost always stops before the broadcast does, so
            it is a LOWER BOUND on the real runtime. Scouting off that number
            silently never samples the final stretch of the VOD — exactly where
            the ending, the raid and the emotional peak tend to live.

    Returns one entry per sample, always: `len(result) == samples`, whatever
    fails. Entries carry `saved` (did it reach Neo4j) and `error`.
    """
    vid = downloader.vod_id(vod)
    node_id = f"twitch:{vid}"
    out_dir = CLIPS_DIR / vid

    duration = int(duration_s or 0)
    source = "caller"
    if duration <= 0:
        source = "chat timeline (lower bound)"
        try:
            rows = graph.run_cypher(
                "MATCH (v:Video {video_id: $id}) RETURN v.duration_s AS d", {"id": node_id}
            )
            duration = int(rows[0]["d"]) if rows and rows[0].get("d") else 0
        except Exception as exc:
            log.warning("duration lookup failed for %s: %s", node_id, exc)
            duration = 0
    if duration <= window * 2:
        raise RuntimeError(
            "No usable VOD duration. Analyze the VOD first so its duration is in "
            "the graph, or pass duration_s= with the real broadcast length."
        )
    log.info("scouting %s over %ss from %s", node_id, duration, source)

    index_id = config.require("TWELVELABS_INDEX_ID")
    # Skip the first/last 3% (intros and raid-outs), spread the rest evenly.
    margin = int(duration * 0.03)
    span = duration - 2 * margin - window
    offsets = [margin + int(span * i / max(1, samples - 1)) for i in range(samples)]

    finds: list[dict] = []
    for i, off in enumerate(offsets, start=1):
        # Exactly ONE entry per window, appended up front and mutated as work
        # proceeds. Appending on success *and* again in the handler used to
        # produce two contradictory rows for the same offset whenever a step
        # after the append failed — inflating the caller's "worth keeping"
        # count and burying the billed verdict under a rating-0 duplicate.
        entry = {
            "start": off,
            "end": off + window,
            "rating": 0,
            "verdict": "",
            "tl_video_id": None,
            "path": None,
            "saved": False,
            "error": None,
        }
        finds.append(entry)

        if progress:
            progress(i, samples, f"watching {off // 60}m mark")

        try:
            path = out_dir / f"scout_{off}s.mp4"
            if not path.exists():
                downloader.download_video(vid, path, quality=quality,
                                          begin=off, end=off + window)
            entry["path"] = str(path)
            entry["tl_video_id"] = clients.upload_video(index_id, path=str(path))
            entry["verdict"] = (clients.analyze(entry["tl_video_id"], SCOUT_PROMPT) or "").strip()
            entry["rating"] = parse_rating(entry["verdict"])
        except Exception as exc:
            entry["error"] = str(exc)
            entry["verdict"] = entry["verdict"] or f"failed: {exc}"

        # The graph write gets its own handler. Neo4j being unreachable is not a
        # reason to discard a Pegasus verdict we have already been billed for —
        # it annotates this entry, it never appends another.
        if entry["rating"] >= KEEP_RATING and entry["tl_video_id"]:
            try:
                graph.save_visual_moment(node_id, off, off + window,
                                         float(entry["rating"] * 10),
                                         entry["verdict"], entry["tl_video_id"])
                entry["saved"] = True
            except Exception as exc:
                log.warning("save_visual_moment failed for %s@%ss: %s", node_id, off, exc)
                entry["error"] = entry["error"] or f"graph write failed: {exc}"

        # Written every pass, so an interrupted scout still leaves the verdicts
        # it paid for on disk.
        _write_json(out_dir / "scout.json", finds)

    ranked = sorted(finds, key=lambda f: f["rating"], reverse=True)
    _write_json(out_dir / "scout.json", ranked)
    return ranked


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
