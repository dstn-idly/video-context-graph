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


# ---------------------------------------------------------------------------
# The clip verdict — the only TwelveLabs output a judge actually reads.
#
# Pegasus is a video model: it is excellent at reporting what is on screen and
# bad at editorial judgement. Asking it "rate 1-10 how viral this is" produced
# two bland sentences and a number, every clip landing on 6 or 7, which tells a
# creator nothing and tells a judge less.
#
# So the two jobs are split. Pegasus WATCHES and reports concretely — quotes,
# reactions, beats, timestamps inside the clip. OpenAI structured outputs then
# does the CRITICISM over that report and has to fill a typed schema, which is
# what forces specificity: you cannot answer "why_it_works" with "it's engaging"
# when the field is named after a mechanism, and you cannot skip `risks` at all.
# ---------------------------------------------------------------------------

CLIP_DESCRIBE_PROMPT = """You are reviewing one short clip from a livestream for a
clipping team. Report only what is actually on screen and in the audio — no
opinions, no ratings, no "this is engaging".

Cover, concretely:
1. The beats in order: what happens first, what changes, how it ends.
2. Direct quotes of the lines that carry the moment, with who says them.
3. The visible reaction: faces, body language, anyone else on camera, and any
   on-screen text, alerts, chat overlay or game state that matters.
4. The single most attention-grabbing instant, given as seconds from the START
   of this clip, in the form "PEAK AT: n".
5. Anything that could be a moderation or out-of-context problem: profanity,
   slurs, threats, nudity, personal info, an argument that reads badly clipped.
   Say "none" if there is none.

If the clip is uneventful — someone talking, routine gameplay, dead air — say so
plainly and describe it anyway."""

VERDICT_SYSTEM = """You are a brutally honest short-form video strategist grading ONE
livestream clip for a creator who has to decide whether to spend an editing hour
on it. You are working from a video model's literal description of the footage.

Rules:
- Be SPECIFIC to this clip. Every field must reference something that actually
  happened in the description — a quote, a reaction, a name, a beat. Generic
  copy that could describe any clip ("high energy moment", "viewers love this",
  "great engagement") is a failure.
- Do NOT invent footage. If the description does not support a detail, leave it
  out. If the description is thin or the clip is uneventful, say that, score it
  low, and set confidence to "low".
- Name the actual mechanism in why_it_works: surprise, escalation, reversal,
  relatability, secondhand embarrassment, payoff of an earlier setup, dramatic
  irony, status flip, parasocial intimacy. Say which one and point at the beat
  that delivers it. "It is funny" is not a mechanism.
- viral_score is 1-10 and MOST CLIPS ARE A 3-5. Reserve 8+ for something you
  would genuinely bet on: a clean self-contained beat with an instant hook. A
  clip that needs context to land is capped at 6.
- risks is mandatory and must be honest. Say why it could flop (needs context,
  slow first three seconds, audio-only payoff, inside joke, already-saturated
  format) and flag any moderation or out-of-context danger. "No risks" is
  almost never true — if it really is clean, say what specifically limits it.
- best_clip_start is seconds INTO THE CLIP where a short should begin so the
  payoff lands fast. Use the description's PEAK AT if present, backed up a few
  seconds for setup. 0 is a valid answer when the clip already opens hot.
- headline is a complete, self-contained title the creator could post as-is,
  naming the thing that happens ("He expelled the entire class on stream"). It
  must stand alone: never a sentence fragment, never a bare noun phrase like
  "The Strangest" or "A Wild Moment", no hashtags, no punctuation spam, under
  about 70 characters. If the clip is uneventful, say so in the headline rather
  than inventing drama, and return an empty platforms list.
- hook_line is the on-screen text or spoken line for the first three seconds.
  It must create a question the viewer needs answered.
- platforms: only the ones that actually fit this clip's length, pacing and
  content. Choose from TikTok, YouTube Shorts, Reels, X, Twitch.
- tags: 3-6 lowercase searchable tags, no "#"."""

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "what_happens": {"type": "string"},
        "why_it_works": {"type": "string"},
        "viral_score": {"type": "integer"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "best_clip_start": {"type": "integer"},
        "hook_line": {"type": "string"},
        "platforms": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "headline", "what_happens", "why_it_works", "viral_score", "confidence",
        "best_clip_start", "hook_line", "platforms", "risks", "tags",
    ],
    "additionalProperties": False,
}

VERDICT_FIELDS = tuple(VERDICT_SCHEMA["required"])

CONFIDENCE_LEVELS = ("low", "medium", "high")

# Free-text platform names collapse onto the handful the UI has room for, so a
# model answering "Instagram Reels" and one answering "reels" produce the same
# chip instead of two.
PLATFORM_ALIASES = {
    "tiktok": "TikTok", "tik tok": "TikTok",
    "youtube shorts": "YouTube Shorts", "shorts": "YouTube Shorts",
    "youtube": "YouTube Shorts", "yt shorts": "YouTube Shorts",
    "reels": "Reels", "instagram reels": "Reels", "instagram": "Reels",
    "ig reels": "Reels", "ig": "Reels",
    "x": "X", "twitter": "X", "x twitter": "X",
    "twitch": "Twitch", "twitch clips": "Twitch",
}

# "PEAK AT: 12" / "peak at 12s" / "**PEAK AT:** 0:12" — same drift problem as
# RATING, same tolerant treatment. Used only as a hint when the structured pass
# cannot run.
PEAK_RE = re.compile(r"peak\s*at[\s:*_\-–—]{0,8}(?:(\d{1,2}):)?(\d{1,3})", re.IGNORECASE)


def _clean_str(value, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


def _clean_list(values, *, limit: int, lower: bool = False,
                aliases: dict | None = None) -> list[str]:
    """Strip, canonicalize, dedupe, cap. Always returns a list of plain strings.

    Neo4j stores lists of primitives natively, so these go onto the Moment node
    as real arrays — no JSON string to decode in the UI.
    """
    out, seen = [], set()
    for raw in values or []:
        if not isinstance(raw, str):
            continue
        name = raw.strip().lstrip("#").strip()
        if not name:
            continue
        if aliases:
            name = aliases.get(name.lower(), name)
        if lower:
            name = name.lower()
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name[:60])
        if len(out) >= limit:
            break
    return out


def _peak_hint(description: str) -> int:
    """Best-effort 'PEAK AT: n' out of the Pegasus report. Unparseable -> 0."""
    match = PEAK_RE.search(description or "")
    if not match:
        return 0
    minutes = int(match.group(1) or 0)
    return max(0, minutes * 60 + int(match.group(2)))


def normalize_verdict(data: dict, *, clip_seconds: int = 0,
                      description: str = "") -> dict:
    """Coerce whatever came back into the exact typed shape the graph stores.

    Structured outputs guarantees the *keys*; it does not guarantee a sane
    viral_score or a best_clip_start that lands inside the clip. Clamping here
    means the UI can render every field without defending itself.
    """
    # A fragment headline ("The Strangest") is worse on screen than no headline,
    # and the model does produce them on thin clips. Anything too short to be a
    # real title gets replaced by the first sentence of what_happens.
    headline = _clean_str(data.get("headline"), 160)
    what_happens = _clean_str(data.get("what_happens"))
    if len(headline) < 15:
        first = what_happens.split(".")[0].strip()
        headline = first[:110] or headline or "Untitled moment"

    verdict = {
        "headline": headline,
        "what_happens": what_happens,
        "why_it_works": _clean_str(data.get("why_it_works")),
        "risks": _clean_str(data.get("risks")) or "Not assessed.",
        "hook_line": _clean_str(data.get("hook_line"), 200),
        "platforms": _clean_list(data.get("platforms"), limit=4, aliases=PLATFORM_ALIASES),
        "tags": _clean_list(data.get("tags"), limit=8, lower=True),
    }

    try:
        score = int(float(data.get("viral_score") or 0))
    except (TypeError, ValueError):
        score = 0
    verdict["viral_score"] = max(1, min(10, score or 1))

    confidence = str(data.get("confidence") or "").strip().lower()
    verdict["confidence"] = confidence if confidence in CONFIDENCE_LEVELS else "medium"

    try:
        best = int(float(data.get("best_clip_start") or 0))
    except (TypeError, ValueError):
        best = 0
    best = max(0, best)
    if best <= 0:
        best = _peak_hint(description)
    # A start past the end of the clip is worse than useless — it sends the
    # editor to footage that does not exist. Leave a couple of seconds of clip.
    if clip_seconds > 2:
        best = min(best, clip_seconds - 2)
    verdict["best_clip_start"] = max(0, best)

    return verdict


def verdict_text(verdict: dict) -> str:
    """Render the typed verdict as the prose blob stored in Moment.ai_verdict.

    Everything already reads `ai_verdict`, and the older UI surfaces truncate it,
    so the headline and the concrete description go first and the metadata goes
    last. Plain text only — callers escape it.
    """
    if not verdict:
        return ""
    lines = [verdict.get("headline", "").strip()]
    if verdict.get("what_happens"):
        lines.append(verdict["what_happens"])
    if verdict.get("why_it_works"):
        lines.append(f"Why it works: {verdict['why_it_works']}")
    if verdict.get("hook_line"):
        lines.append(f"Hook: “{verdict['hook_line']}”")
    if verdict.get("risks"):
        lines.append(f"Risk: {verdict['risks']}")

    meta = [f"Viral {verdict.get('viral_score', 0)}/10 ({verdict.get('confidence', 'medium')} confidence)"]
    if verdict.get("best_clip_start"):
        meta.append(f"start the short at +{verdict['best_clip_start']}s")
    if verdict.get("platforms"):
        meta.append(", ".join(verdict["platforms"]))
    lines.append(" · ".join(meta))

    return "\n\n".join(part for part in lines if part)


def structured_verdict(description: str, *, context: str = "",
                       clip_seconds: int = 0) -> dict:
    """Pegasus prose -> typed critical verdict via OpenAI structured outputs.

    Raises on API failure; every caller falls back rather than losing the clip.
    """
    text = (description or "").strip()
    if not text:
        raise ValueError("no description to analyze")

    user = text if not context else f"Stream context: {context}\n\nWhat the video model saw:\n{text}"
    if clip_seconds > 0:
        user = f"{user}\n\nThis clip is {clip_seconds} seconds long."

    response = clients.openai_client().chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": VERDICT_SYSTEM},
            {"role": "user", "content": user},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "clip_verdict", "schema": VERDICT_SCHEMA, "strict": True},
        },
    )
    data = json.loads(response.choices[0].message.content or "{}")
    return normalize_verdict(data, clip_seconds=clip_seconds, description=text)


def fallback_verdict(description: str) -> dict:
    """A typed verdict built from raw Pegasus text when the structured pass dies.

    Deliberately honest about being degraded: confidence "low", and the score is
    whatever rating Pegasus happened to state rather than an invented one. The
    clip is never dropped just because OpenAI was down.
    """
    text = (description or "").strip()
    first = text.split(".")[0].strip() if text else ""
    return normalize_verdict(
        {
            "headline": (first[:110] or "Unscored moment"),
            "what_happens": text,
            "why_it_works": "",
            "viral_score": parse_rating(text) or 5,
            "confidence": "low",
            "best_clip_start": 0,
            "hook_line": "",
            "platforms": [],
            "risks": "Structured analysis unavailable — this is the raw video-model "
                     "description, not a graded verdict. Review it yourself.",
            "tags": [],
        },
        description=text,
    )


# Native lists for platforms/tags, an int for the score, plus verdict_json as a
# belt-and-braces copy of the whole thing for anything that would rather parse
# once than read eleven properties.
_MOMENT_VERDICT_CYPHER = """
MATCH (:Video {video_id: $video_id})-[:HAS_MOMENT]->(m:Moment {moment_id: $mid})
SET m.ai_verdict      = $ai_verdict,
    m.tl_video_id     = coalesce($tl, m.tl_video_id),
    m.headline        = $headline,
    m.what_happens    = $what_happens,
    m.why_it_works    = $why_it_works,
    m.viral_score     = $viral_score,
    m.confidence      = $confidence,
    m.best_clip_start = $best_clip_start,
    m.best_clip_abs   = $best_clip_abs,
    m.hook_line       = $hook_line,
    m.platforms       = $platforms,
    m.risks           = $risks,
    m.tags            = $tags,
    m.raw_description = $raw_description,
    m.verdict_json    = $verdict_json,
    m.verdict_model   = $model,
    m.verdict_at      = datetime()
RETURN m.moment_id AS moment_id
"""


def save_moment_verdict(video_id: str, start: int, verdict: dict, *,
                        tl_video_id: str = "", description: str = "",
                        moment_id: str = "") -> bool:
    """Write the full typed verdict onto the existing Moment node.

    graph.set_moment_verdict only knows about ai_verdict/tl_video_id, so the
    extra properties go through graph.run_cypher — parameterized, never
    string-formatted, and graph.py is not touched.

    Returns True if a Moment actually matched.
    """
    if not verdict:
        return False
    rows = graph.run_cypher(_MOMENT_VERDICT_CYPHER, {
        "video_id": video_id,
        "mid": moment_id or f"{video_id}:m{start}",
        "ai_verdict": verdict_text(verdict),
        "tl": tl_video_id or None,
        "headline": verdict.get("headline", ""),
        "what_happens": verdict.get("what_happens", ""),
        "why_it_works": verdict.get("why_it_works", ""),
        "viral_score": int(verdict.get("viral_score", 0) or 0),
        "confidence": verdict.get("confidence", "medium"),
        "best_clip_start": int(verdict.get("best_clip_start", 0) or 0),
        "best_clip_abs": int(start) + int(verdict.get("best_clip_start", 0) or 0),
        "hook_line": verdict.get("hook_line", ""),
        "platforms": list(verdict.get("platforms") or []),
        "risks": verdict.get("risks", ""),
        "tags": list(verdict.get("tags") or []),
        "raw_description": description or "",
        "verdict_json": json.dumps(verdict),
        "model": config.OPENAI_MODEL,
    })
    return bool(rows)


def deep_analyze(tl_video_id: str, context: str = "") -> dict:
    """Rich, typed analysis of ONE already-indexed clip. On-demand entry point.

    This is what the UI's "Analyze this moment" button calls: give it a
    tl_video_id that is already in the TwelveLabs index (every enriched clip and
    every scout find stores one) and it re-watches with the deep prompt and runs
    the critical pass over the result.

    Never raises. The returned dict always carries every VERDICT_FIELDS key plus:
        tl_video_id, description (raw Pegasus), verdict_text (prose blob),
        structured (bool — did the OpenAI pass succeed), ok, error.
    """
    result = {
        "tl_video_id": tl_video_id,
        "description": "",
        "structured": False,
        "ok": False,
        "error": None,
    }

    described = ""
    try:
        described = (clients.analyze(tl_video_id, CLIP_DESCRIBE_PROMPT) or "").strip()
    except Exception as exc:
        log.warning("deep_analyze: Pegasus failed for %s: %s", tl_video_id, exc)
        result["error"] = str(exc)

    result["description"] = described

    if not described:
        verdict = normalize_verdict({
            "headline": "Could not analyze this moment",
            "what_happens": "",
            "risks": "The video model returned nothing for this clip.",
            "confidence": "low",
            "viral_score": 1,
        })
        result.update(verdict)
        result["verdict_text"] = verdict_text(verdict)
        return result

    try:
        verdict = structured_verdict(described, context=context)
        result["structured"] = True
    except Exception as exc:
        log.warning("deep_analyze: structured pass failed for %s: %s", tl_video_id, exc)
        result["error"] = str(exc)
        verdict = fallback_verdict(described)

    result.update(verdict)
    result["verdict_text"] = verdict_text(verdict)
    result["ok"] = True
    return result


def enrich_clips(vod: str, clips: list[dict], *, title: str = "", progress=None) -> list[dict]:
    """Stage 3 — TwelveLabs watches each clip and confirms what actually happened.

    Chat tells us *that* something happened; this tells us *what*. Disagreements
    are useful signal, so the model's verdict is stored alongside chat's guess
    rather than replacing it.

    Two model passes per clip: Pegasus describes the footage concretely, then
    structured_verdict() grades it into the typed shape the Moment node carries.
    Both are wrapped — a dead OpenAI key degrades a clip to the raw description,
    it never costs us a clip we already paid to download and index.
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
            described = (clients.analyze(tl_id, CLIP_DESCRIBE_PROMPT) or "").strip()

            # Own try/except: a bad extraction must cost us the entities, not
            # the clip. An empty list still writes a perfectly good Scene.
            try:
                entities, topics = extract_entities(described, context=title)
            except Exception as exc:
                log.warning("entity extraction failed for %s@%ss: %s", vid, clip["start"], exc)
                entities, topics = [], []

            # Likewise for the verdict: falling back to the raw Pegasus text is
            # exactly the old behaviour, so the worst case is what we shipped
            # before rather than a lost clip.
            clip_seconds = max(0, int(clip.get("end", 0)) - int(clip.get("start", 0)))
            try:
                verdict = structured_verdict(described, context=title,
                                             clip_seconds=clip_seconds)
                structured = True
            except Exception as exc:
                log.warning("verdict pass failed for %s@%ss: %s", vid, clip["start"], exc)
                verdict = fallback_verdict(described)
                structured = False
            blob = verdict_text(verdict) or described

            # The chat detector's own label stays a topic; extraction adds to it,
            # and so do the verdict's tags — they are the searchable handles a
            # creator would actually type.
            scene_topics, seen = [], set()
            for topic in [clip.get("kind", ""), *topics, *verdict.get("tags", [])]:
                key = graph.normalize_key(topic or "")
                if key and key not in seen:
                    seen.add(key)
                    scene_topics.append(topic)

            graph.upsert_scene(node_id, {
                "scene_id": f"{vid}:{clip['start']}",
                "start": clip["start"],
                "end": clip["end"],
                "description": blob,
                "entities": entities,
                "topics": scene_topics,
                "tl_video_id": tl_id,
            })
            # Base write first — ai_verdict and tl_video_id land even if the
            # richer property write below trips over an old Moment node.
            graph.set_moment_verdict(node_id, clip["start"], blob, tl_id)
            try:
                saved = save_moment_verdict(node_id, clip["start"], verdict,
                                            tl_video_id=tl_id, description=described)
            except Exception as exc:
                log.warning("verdict write failed for %s@%ss: %s", vid, clip["start"], exc)
                saved = False

            enriched.append({
                **clip,
                "tl_video_id": tl_id,
                "ai_verdict": blob,
                "description": described,
                "verdict": verdict,
                "structured": structured,
                "verdict_saved": saved,
                "entities": entities,
                "topics": scene_topics,
                **verdict,
            })
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
