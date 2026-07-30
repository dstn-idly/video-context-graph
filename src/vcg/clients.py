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


def twelvelabs() -> TwelveLabs:
    global _tl
    if _tl is None:
        _tl = TwelveLabs(api_key=config.require("TWELVELABS_API_KEY"))
    return _tl


def openai_client() -> OpenAI:
    global _oai
    if _oai is None:
        _oai = OpenAI(api_key=config.require("OPENAI_API_KEY"))
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

    return indexed.id


def search(index_id: str, query: str, limit: int = 10) -> list[dict]:
    """Semantic search over indexed video. Returns clip dicts."""
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
    return clips


PEGASUS_MODEL = "pegasus1.5"


def analyze(video_id: str, prompt: str, temperature: float = 0.2) -> str:
    """Ask Pegasus an open-ended question about one indexed video."""
    client = twelvelabs()

    if hasattr(client, "analyze"):  # SDK >= 1.x
        result = client.analyze(
            model_name=PEGASUS_MODEL,
            video_id=video_id,
            prompt=prompt,
            temperature=temperature,
        )
        if getattr(result, "error", None):
            raise RuntimeError(f"TwelveLabs analyze failed: {result.error}")
        return result.data or ""

    # 0.x fallback
    return client.generate.text(video_id=video_id, prompt=prompt, temperature=temperature).data
