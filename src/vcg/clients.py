"""Thin wrappers over TwelveLabs + OpenAI.

The TwelveLabs Python SDK renamed several surfaces between 0.x and 1.x
(`client.task` -> `client.tasks`, `client.generate.text` -> `client.analyze`,
plus the new `assets` / `indexed_assets` upload flow). The helpers here probe for
whichever one your installed version exposes so a version bump mid-hackathon
doesn't cost you an hour. Run `python scripts/check_env.py` to see what you have.
"""
import time

from openai import OpenAI
from twelvelabs import TwelveLabs

from . import config

_tl = None
_oai = None


def _log(source: str, message: str, **detail) -> None:
    """Push one line to the live event console. Never raises, never blocks."""
    try:
        from . import eventlog

        eventlog.emit(source, message, **detail)
    except Exception:
        pass


def twelvelabs() -> TwelveLabs:
    global _tl
    if _tl is None:
        _tl = TwelveLabs(api_key=config.require("TWELVELABS_API_KEY"))
        _log("twelvelabs", "TwelveLabs client connected")
    return _tl


def openai_client() -> OpenAI:
    global _oai
    if _oai is None:
        _oai = OpenAI(api_key=config.require("OPENAI_API_KEY"))
        _log("openai", f"OpenAI client connected ({config.OPENAI_MODEL})")
    return _oai


# --------------------------------------------------------------------------
# TwelveLabs helpers
# --------------------------------------------------------------------------

def create_index(name: str, model: str = "marengo3.0") -> str:
    """Create an index and return its id. Put that id in .env as TWELVELABS_INDEX_ID."""
    index = twelvelabs().indexes.create(
        index_name=name,
        models=[{"model_name": model, "model_options": ["visual", "audio"]}],
    )
    if not index.id:
        raise RuntimeError("TwelveLabs returned an index with no id.")
    return index.id


def upload_video(index_id: str, *, url: str | None = None, path: str | None = None) -> str:
    """Upload + index one video. Returns the id you use for search/analyze.

    Pass either a direct URL to a raw media file, or a local file path.
    """
    client = twelvelabs()
    started = time.time()
    source = url or path or "?"
    _log("twelvelabs", f"Uploading video to TwelveLabs ({str(source).rsplit('/', 1)[-1][:60]})",
         index_id=index_id)

    if url:
        asset = client.assets.create(method="url", url=url)
    elif path:
        with open(path, "rb") as fh:
            asset = client.assets.create(method="direct", file=fh)
    else:
        raise ValueError("upload_video needs either url= or path=")

    while True:
        asset = client.assets.retrieve(asset.id)
        if asset.status == "ready":
            break
        if asset.status == "failed":
            raise RuntimeError(f"Asset processing failed: {asset.id}")
        time.sleep(5)

    _log("twelvelabs", f"Asset ready ({asset.id}) — indexing with Marengo", asset_id=asset.id)

    indexed = client.indexes.indexed_assets.create(index_id=index_id, asset_id=asset.id)
    while True:
        indexed = client.indexes.indexed_assets.retrieve(
            index_id=index_id, indexed_asset_id=indexed.id
        )
        if indexed.status == "ready":
            break
        if indexed.status == "failed":
            raise RuntimeError(f"Indexing failed for asset {asset.id}")
        time.sleep(5)

    _log("twelvelabs",
         f"Video indexed in {time.time() - started:.0f}s (video {indexed.id})",
         asset_id=asset.id, video_id=indexed.id, index_id=index_id)
    return indexed.id


def search(index_id: str, query: str, limit: int = 10) -> list[dict]:
    """Semantic search over indexed video. Returns clip dicts."""
    started = time.time()
    _log("twelvelabs", f'Searching footage for "{str(query)[:70]}"', index_id=index_id, limit=limit)
    results = twelvelabs().search.query(
        index_id=index_id,
        query_text=query,
        search_options=["visual", "audio"],
    )
    clips = []
    for clip in results:  # SyncPager — iterating pulls pages as needed
        clips.append(
            {
                "video_id": clip.video_id,
                "start": clip.start,
                "end": clip.end,
                "rank": clip.rank,
                "transcription": getattr(clip, "transcription", None),
            }
        )
        if len(clips) >= limit:
            break
    _log("twelvelabs",
         f'Search "{str(query)[:50]}" → {len(clips)} clips ({time.time() - started:.1f}s)',
         hits=len(clips))
    return clips


EMBED_MODEL = "marengo3.0"
EMBED_DIMS = 512

# The index was created with model_options ["visual", "audio"], so those plus
# "transcription" are the only embedding_option values it will answer for.
# "visual-text" is NOT valid here — asking for it 400s the whole request.
EMBED_OPTIONS = ("visual", "audio", "transcription")


def segment_embeddings(indexed_asset_id: str, *, index_id: str | None = None,
                       option: str = "visual") -> list[dict]:
    """Pull Marengo's per-segment vectors for one already-indexed asset.

    Returns [{start, end, vector}] with times in seconds *relative to the media
    that was uploaded* — for a clip that is clip-relative, so the caller has to
    add the clip's offset before storing (graph.upsert_segments does).

    This is the semantic layer's raw material: 512-dim visual embeddings, ~10s
    apart, which is what makes "find footage that looks like X" a vector query
    instead of another paid model call.

    Never raises — a missing asset, an option the index wasn't built with, or an
    SDK shape change all degrade to [] so ingestion keeps going.
    """
    try:
        resp = twelvelabs().indexes.indexed_assets.retrieve(
            index_id=index_id or config.require("TWELVELABS_INDEX_ID"),
            indexed_asset_id=indexed_asset_id,
            embedding_option=[option],
        )
    except Exception as exc:
        _log("twelvelabs", f"No {option} embeddings for {indexed_asset_id}: {exc}")
        return []

    # resp.embedding.video_embedding.segments — each a VideoSegment with
    # .float_ (the 512 floats), .start_offset_sec, .end_offset_sec.
    video_embedding = getattr(getattr(resp, "embedding", None), "video_embedding", None)
    segments = getattr(video_embedding, "segments", None) or []

    out: list[dict] = []
    for seg in segments:
        vector = getattr(seg, "float_", None)
        if not vector:
            continue
        out.append(
            {
                "start": float(getattr(seg, "start_offset_sec", 0.0) or 0.0),
                "end": float(getattr(seg, "end_offset_sec", 0.0) or 0.0),
                "vector": [float(x) for x in vector],
                "option": getattr(seg, "embedding_option", option),
            }
        )
    _log("twelvelabs",
         f"Marengo returned {len(out)} {option} embedding segments ({EMBED_DIMS}-dim)",
         asset=indexed_asset_id, segments=len(out))
    return out


PEGASUS_MODEL = "pegasus1.5"


def _asset_id_for(video_id: str) -> str:
    """Resolve an indexed-video id to its underlying asset id.

    pegasus1.5's analyze endpoint takes a video object (asset_id/url), not the
    indexed-video id that upload/search hand around. If the lookup fails, assume
    the caller already passed an asset id.
    """
    try:
        indexed = twelvelabs().indexes.indexed_assets.retrieve(
            index_id=config.require("TWELVELABS_INDEX_ID"),
            indexed_asset_id=video_id,
        )
        return indexed.asset_id or video_id
    except Exception:
        return video_id


def analyze(video_id: str, prompt: str, temperature: float = 0.2) -> str:
    """Ask Pegasus an open-ended question about one indexed video."""
    client = twelvelabs()
    started = time.time()
    prompt_len = len(prompt or "")
    _log("twelvelabs", f"{PEGASUS_MODEL} watching clip (prompt {prompt_len} chars)",
         video_id=video_id, model=PEGASUS_MODEL)

    if hasattr(client, "analyze"):  # SDK >= 1.x
        from twelvelabs.types.video_context import VideoContext_AssetId

        result = client.analyze(
            model_name=PEGASUS_MODEL,
            video=VideoContext_AssetId(type="asset_id", asset_id=_asset_id_for(video_id)),
            prompt=prompt,
            temperature=temperature,
        )
        if getattr(result, "error", None):
            _log("twelvelabs", f"Pegasus analyze failed: {result.error}", video_id=video_id)
            raise RuntimeError(f"TwelveLabs analyze failed: {result.error}")
        answer = result.data or ""
    else:
        # 0.x fallback
        answer = client.generate.text(
            video_id=video_id, prompt=prompt, temperature=temperature
        ).data or ""

    _log("twelvelabs",
         f"Pegasus analyzed clip ({time.time() - started:.1f}s, {len(answer)} chars)",
         model=PEGASUS_MODEL, prompt_chars=prompt_len, response_chars=len(answer),
         video_id=video_id)
    return answer
