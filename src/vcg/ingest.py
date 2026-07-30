"""Pipeline: video -> TwelveLabs understanding -> OpenAI structuring -> Neo4j graph.

This is the "context graph" build step. TwelveLabs watches the video and
describes it; OpenAI turns that prose into typed nodes and edges; Neo4j stores
the result so the agent can traverse it later.
"""
import json
import uuid

from . import clients, config, graph, twitch

SCENE_PROMPT = """Break this video into its distinct scenes.

For each scene report:
- approximate start and end time in seconds
- a one-sentence description of what happens
- every named or clearly identifiable entity (people, objects, places, organizations)
- the topics the scene is about

Be concrete and exhaustive. Cover the whole video."""

SCENE_SCHEMA = {
    "type": "object",
    "properties": {
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "description": {"type": "string"},
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
                "required": ["start", "end", "description", "entities", "topics"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["scenes"],
    "additionalProperties": False,
}


def structure_scenes(raw_description: str) -> list[dict]:
    """Turn Pegasus prose into validated scene dicts using OpenAI structured outputs."""
    response = clients.openai_client().chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[
            {
                "role": "system",
                "content": "You convert video scene descriptions into structured graph data. "
                           "Normalize entity names (consistent casing, no duplicates).",
            },
            {"role": "user", "content": raw_description},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "scene_breakdown", "schema": SCENE_SCHEMA, "strict": True},
        },
    )
    return json.loads(response.choices[0].message.content)["scenes"]


def ingest_video(
    video_id: str,
    title: str,
    index_id: str | None = None,
    *,
    graph_video_id: str | None = None,
    offset: int = 0,
    source_url: str = "",
    link: bool = True,
) -> dict:
    """Build the graph for one already-indexed TwelveLabs video.

    Args:
        video_id: The TwelveLabs video id to analyze.
        graph_video_id: Node to attach scenes to. Defaults to `video_id`. For a
            segmented VOD, pass the same value for every segment so the whole
            broadcast stays one node.
        offset: Seconds to add to every scene timestamp. For segment N of a
            split VOD this is where that segment starts in the original.
        link: Recompute co-occurrence edges. Skip on all but the last segment.
    """
    index_id = index_id or config.require("TWELVELABS_INDEX_ID")
    node_id = graph_video_id or video_id

    print(f"[1/5] Summarizing {title} ...")
    summary = clients.analyze(video_id, "Summarize this video in five sentences.")

    print("[2/5] Extracting scenes ...")
    raw = clients.analyze(video_id, SCENE_PROMPT)

    print("[3/5] Structuring into graph shape ...")
    scenes = structure_scenes(raw)

    print(f"[4/5] Writing {len(scenes)} scenes to Neo4j ...")
    graph.init_schema()
    graph.upsert_video(node_id, title, summary, source_url)
    for i, scene in enumerate(scenes):
        scene["scene_id"] = f"{video_id}:{i}"
        scene["tl_video_id"] = video_id
        scene["start"] = scene.get("start", 0) + offset
        scene["end"] = scene.get("end", 0) + offset
        graph.upsert_scene(node_id, scene)

    if link:
        print("[5/5] Linking co-occurrences ...")
        graph.rebuild_co_occurrences()

    return {"video_id": node_id, "title": title, "scenes": len(scenes), "summary": summary}


def ingest_twitch(
    url: str,
    title: str,
    *,
    segment_seconds: int = twitch.MAX_SEGMENT_SECONDS,
    max_height: int = 720,
    keep_files: bool = False,
) -> dict:
    """Download a Twitch VOD or clip, split it if long, and graph the whole thing.

    Twitch serves HLS, not a plain mp4, so TwelveLabs can't fetch the URL
    itself — everything has to come down locally first. Long broadcasts are then
    split under the 1-hour analysis ceiling, and each segment's scene times are
    shifted back onto the original broadcast's clock.
    """
    index_id = config.require("TWELVELABS_INDEX_ID")
    data_dir = config.ROOT / "data"

    print(f"==> Downloading {url}")
    local = twitch.download(url, data_dir, max_height=max_height)
    duration = twitch.probe_duration(local)
    print(f"    {local.name}  ({duration / 60:.1f} min)")

    chunks = twitch.segment(local, segment_seconds)
    if len(chunks) > 1:
        print(f"==> Split into {len(chunks)} segments (analysis caps at 1h/video)")

    node_id = f"twitch:{local.stem}"
    total_scenes = 0
    summaries = []

    for i, (chunk, offset) in enumerate(chunks, start=1):
        label = title if len(chunks) == 1 else f"{title} (part {i}/{len(chunks)})"
        print(f"\n==> Segment {i}/{len(chunks)} — offset {offset // 60}m — uploading")
        tl_id = clients.upload_video(index_id, path=str(chunk))

        result = ingest_video(
            tl_id,
            title,
            index_id,
            graph_video_id=node_id,
            offset=offset,
            source_url=url,
            link=(i == len(chunks)),
        )
        total_scenes += result["scenes"]
        summaries.append(f"[{offset // 60}m+] {result['summary']}")
        print(f"    {label}: {result['scenes']} scenes")

    if not keep_files:
        for chunk, _ in chunks:
            if chunk != local:
                chunk.unlink(missing_ok=True)
        if len(chunks) > 1:
            seg_dir = local.parent / f"{local.stem}_segments"
            if seg_dir.is_dir():
                seg_dir.rmdir()

    return {
        "video_id": node_id,
        "title": title,
        "scenes": total_scenes,
        "segments": len(chunks),
        "summary": "\n\n".join(summaries),
        "local_file": str(local),
    }


def ingest_from_source(title: str, *, url: str | None = None, path: str | None = None) -> dict:
    """Upload a brand new video, then build its graph."""
    index_id = config.require("TWELVELABS_INDEX_ID")
    print(f"[0/5] Uploading {title} to TwelveLabs ...")
    video_id = clients.upload_video(index_id, url=url, path=path)
    return ingest_video(video_id, title, index_id)
