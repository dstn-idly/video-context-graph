"""Find the moments worth clipping, and the stretches worth fixing.

Chat is the cheapest high-quality signal a stream produces. When something
funny, impressive, or awkward happens, chat reacts within a second or two —
so message velocity locates candidate moments without touching a single frame
of video. We only spend TwelveLabs analysis on windows chat already flagged.

Two outputs:
  * peaks     — spikes worth clipping, tagged funny / hype / awkward / tense
  * dead_spots — sustained low-engagement stretches, i.e. what to tighten up
"""
import re
from collections import Counter
from dataclasses import dataclass, field, asdict

BUCKET_SECONDS = 10

# Emote and phrase vocabularies. Twitch chat is emote-driven, so these carry
# most of the signal; tune them for your community and the detector follows.
CATEGORIES: dict[str, set[str]] = {
    "funny": {
        "kekw", "lulw", "omegalul", "lul", "lmao", "lmfao", "kek", "icant",
        "4head", "haha", "hahaha", "pepelaugh", "kekl", "deadass", "im dead",
        "crying", "😂", "🤣", "lol",
    },
    "hype": {
        "pog", "pogchamp", "poggers", "pogu", "pogging", "letsgo", "lets go",
        "hypers", "ezclap", "ez clap", "gigachad", "insane", "no way", "noway",
        "clip it", "clipped", "sheesh", "goated", "w", "🔥", "🎉",
    },
    "awkward": {
        "yikes", "awkward", "oof", "sadge", "pepehands", "cringe", "monkas",
        "monkaw", "d:", "uhh", "umm", "oh no", "ohno", "💀", "😬",
    },
    "tense": {
        "pausechamp", "copium", "monkaeyes", "clutch", "close one", "so close",
        "nervous", "😰",
    },
}

CLIP_REQUEST = re.compile(r"\bclip\s?(it|that|this)\b", re.I)
_WORD = re.compile(r"[a-z0-9:😂🤣🔥🎉💀😬😰]+")


@dataclass
class Bucket:
    start: int
    messages: int = 0
    chatters: int = 0
    emotes: int = 0
    bits: int = 0
    clip_requests: int = 0
    categories: dict = field(default_factory=dict)
    heat: float = 0.0       # 0-100, for the timeline chart
    spike: float = 0.0      # robust z-score vs the stream's own baseline


@dataclass
class Moment:
    start: int
    end: int
    kind: str               # funny | hype | awkward | tense | action
    score: float            # 0-100 "worth clipping" score
    messages: int
    chatters: int
    spike: float
    sample: list = field(default_factory=list)   # representative chat lines
    reason: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class DeadSpot:
    start: int
    end: int
    messages: int
    severity: float         # 0-100, how far below baseline
    note: str = ""

    def to_dict(self):
        return asdict(self)


# --------------------------------------------------------------------------
# Timeline
# --------------------------------------------------------------------------

def _classify_text(body: str) -> list[str]:
    """Which reaction categories a message hits. A message can hit several."""
    lowered = body.lower()
    tokens = set(_WORD.findall(lowered))
    hits = []
    for name, vocab in CATEGORIES.items():
        # token match for short emotes, substring for multi-word phrases
        if tokens & vocab or any(" " in term and term in lowered for term in vocab):
            hits.append(name)
    return hits


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def build_timeline(comments: list[dict], bucket_seconds: int = BUCKET_SECONDS) -> list[Bucket]:
    """Bucket chat into fixed windows and score each one against the stream's baseline."""
    if not comments:
        return []

    grouped: dict[int, list[dict]] = {}
    for comment in comments:
        offset = int(comment.get("content_offset_seconds", 0) or 0)
        grouped.setdefault(offset // bucket_seconds, []).append(comment)

    last_index = max(grouped)
    buckets: list[Bucket] = []

    for index in range(last_index + 1):
        items = grouped.get(index, [])
        bucket = Bucket(start=index * bucket_seconds, messages=len(items))
        names, cats = set(), Counter()

        for comment in items:
            message = comment.get("message") or {}
            body = message.get("body") or ""
            commenter = comment.get("commenter") or {}
            names.add(commenter.get("display_name") or commenter.get("name") or "?")
            bucket.bits += int(message.get("bits_spent") or 0)
            bucket.emotes += sum(
                1 for frag in (message.get("fragments") or []) if frag.get("emoticon")
            )
            if CLIP_REQUEST.search(body):
                bucket.clip_requests += 1
            for category in _classify_text(body):
                cats[category] += 1

        bucket.chatters = len(names)
        bucket.categories = dict(cats)
        buckets.append(bucket)

    _score(buckets)
    return buckets


def _score(buckets: list[Bucket]):
    """Robust spike scoring, using median/MAD so one huge peak can't flatten the rest."""
    counts = [b.messages for b in buckets]
    baseline = _median(counts)
    deviations = [abs(c - baseline) for c in counts]
    scale = _median(deviations) or 1.0

    chatter_counts = [b.chatters for b in buckets]
    chatter_baseline = _median(chatter_counts)
    chatter_scale = _median([abs(c - chatter_baseline) for c in chatter_counts]) or 1.0

    raw = []
    for bucket in buckets:
        msg_z = (bucket.messages - baseline) / scale
        # Breadth matters: 50 messages from 50 people beats 50 from one spammer.
        chatter_z = (bucket.chatters - chatter_baseline) / chatter_scale
        emote_rate = bucket.emotes / bucket.messages if bucket.messages else 0
        bucket.spike = msg_z + 0.6 * chatter_z + 0.4 * emote_rate + 0.5 * bucket.clip_requests
        raw.append(bucket.spike)

    top = max(raw) if raw else 1.0
    for bucket in buckets:
        bucket.heat = max(0.0, min(100.0, (bucket.spike / top) * 100)) if top > 0 else 0.0


# --------------------------------------------------------------------------
# Peaks -> clips
# --------------------------------------------------------------------------

def find_peaks(
    buckets: list[Bucket],
    comments: list[dict],
    *,
    top_n: int = 10,
    pre_roll: int = 15,
    post_roll: int = 20,
    min_gap: int = 60,
    min_score: float = 15.0,
) -> list[Moment]:
    """Pick the top spikes, widened into clippable windows.

    pre_roll matters: chat reacts *after* the thing happens, so the moment
    itself sits a beat before the spike. Starting the clip at the spike would
    cut off the punchline.

    min_score is a percentage of the stream's biggest spike, not an absolute
    threshold. Spike magnitudes scale with chat size, so an absolute floor lets
    random baseline clustering through as junk "action" clips on quiet streams
    and filters out real moments on busy ones.
    """
    top_spike = max((b.spike for b in buckets), default=1.0) or 1.0
    ranked = sorted(
        (b for b in buckets if (b.spike / top_spike) * 100 >= min_score),
        key=lambda b: b.spike,
        reverse=True,
    )

    chosen: list[Bucket] = []
    for bucket in ranked:
        if all(abs(bucket.start - other.start) >= min_gap for other in chosen):
            chosen.append(bucket)
        if len(chosen) >= top_n:
            break

    moments = []
    for bucket in sorted(chosen, key=lambda b: b.start):
        start = max(0, bucket.start - pre_roll)
        end = bucket.start + post_roll
        window = [
            c for c in comments
            if start <= int(c.get("content_offset_seconds", 0) or 0) <= end
        ]
        kind, reason = _describe(window, bucket)
        moments.append(
            Moment(
                start=start,
                end=end,
                kind=kind,
                score=round(min(100.0, (bucket.spike / top_spike) * 100), 1),
                messages=bucket.messages,
                chatters=bucket.chatters,
                spike=round(bucket.spike, 2),
                sample=_sample(window),
                reason=reason,
            )
        )
    return moments


def _describe(window: list[dict], bucket: Bucket) -> tuple[str, str]:
    """Name the moment from how chat reacted to it."""
    tally = Counter()
    for comment in window:
        for category in _classify_text((comment.get("message") or {}).get("body") or ""):
            tally[category] += 1

    if not tally:
        return "action", f"chat volume spiked {bucket.spike:.1f}x above baseline"

    kind, hits = tally.most_common(1)[0]
    parts = [f"{count} '{name}' reactions" for name, count in tally.most_common(3)]
    reason = ", ".join(parts)
    if bucket.clip_requests:
        reason += f"; {bucket.clip_requests} viewers asked to clip it"
    return kind, reason


def _sample(window: list[dict], limit: int = 6) -> list[str]:
    """Representative chat lines, deduped, for showing in the UI.

    Reaction messages come first. The window deliberately starts before the
    spike (see pre_roll), so a purely chronological sample would show the
    small talk that preceded the moment rather than the reaction that defines
    it — the evidence has to match the label.
    """
    reactions, filler, seen = [], [], set()

    for comment in window:
        body = ((comment.get("message") or {}).get("body") or "").strip()
        key = body.lower()
        if not body or key in seen:
            continue
        seen.add(key)
        (reactions if _classify_text(body) else filler).append(body[:120])

    return (reactions + filler)[:limit]


# --------------------------------------------------------------------------
# Dead spots -> coaching
# --------------------------------------------------------------------------

def find_dead_spots(
    buckets: list[Bucket],
    *,
    min_duration: int = 120,
    quiet_ratio: float = 0.4,
) -> list[DeadSpot]:
    """Sustained stretches where engagement fell well below this stream's norm.

    Measured against the stream's own median rather than an absolute threshold,
    so it works the same for a 20-viewer stream and a 20,000-viewer one.
    """
    if not buckets:
        return []

    baseline = _median([b.messages for b in buckets])
    if baseline <= 0:
        return []

    threshold = baseline * quiet_ratio
    spots, run = [], []

    def flush():
        if not run:
            return
        span = (run[-1].start + BUCKET_SECONDS) - run[0].start
        if span >= min_duration:
            total = sum(b.messages for b in run)
            average = total / len(run)
            severity = round(min(100.0, (1 - average / baseline) * 100), 1)
            spots.append(
                DeadSpot(
                    start=run[0].start,
                    end=run[-1].start + BUCKET_SECONDS,
                    messages=total,
                    severity=severity,
                    note=f"{span // 60}m at {average:.1f} msgs/{BUCKET_SECONDS}s "
                         f"vs {baseline:.1f} baseline",
                )
            )

    for bucket in buckets:
        if bucket.messages <= threshold:
            run.append(bucket)
        else:
            flush()
            run = []
    flush()

    return sorted(spots, key=lambda s: s.severity, reverse=True)


def summarize(buckets: list[Bucket], peaks: list[Moment], dead: list[DeadSpot]) -> dict:
    """Headline numbers for the timeline view."""
    total = sum(b.messages for b in buckets)
    duration = (buckets[-1].start + BUCKET_SECONDS) if buckets else 0
    dead_time = sum(s.end - s.start for s in dead)
    return {
        "duration_seconds": duration,
        "total_messages": total,
        "messages_per_minute": round(total / (duration / 60), 1) if duration else 0,
        "peak_moments": len(peaks),
        "dead_time_seconds": dead_time,
        "dead_time_pct": round(dead_time / duration * 100, 1) if duration else 0,
        "kinds": dict(Counter(m.kind for m in peaks)),
    }
