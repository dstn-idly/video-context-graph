"""Pipeline: video -> TwelveLabs understanding -> OpenAI structuring -> Neo4j graph.

This is the "context graph" build step. TwelveLabs watches the video and
describes it; OpenAI turns that prose into typed nodes and edges; Neo4j stores
the result so the agent can traverse it later.
"""
import json
import uuid

from . import clients, config, graph

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


def ingest_video(video_id: str, title: str, index_id: str | None = None) -> dict:
    """Build the graph for one already-indexed TwelveLabs video."""
    index_id = index_id or config.require("TWELVELABS_INDEX_ID")

    print(f"[1/5] Summarizing {title} ...")
    summary = clients.analyze(video_id, "Summarize this video in five sentences.")

    print("[2/5] Extracting scenes ...")
    raw = clients.analyze(video_id, SCENE_PROMPT)

    print("[3/5] Structuring into graph shape ...")
    scenes = structure_scenes(raw)

    print(f"[4/5] Writing {len(scenes)} scenes to Neo4j ...")
    graph.init_schema()
    graph.upsert_video(video_id, title, summary)
    for i, scene in enumerate(scenes):
        scene["scene_id"] = f"{video_id}:{i}"
        graph.upsert_scene(video_id, scene)

    print("[5/5] Linking co-occurrences ...")
    graph.link_co_occurrences(video_id)

    return {"video_id": video_id, "title": title, "scenes": len(scenes), "summary": summary}


def ingest_from_source(title: str, *, url: str | None = None, path: str | None = None) -> dict:
    """Upload a brand new video, then build its graph."""
    index_id = config.require("TWELVELABS_INDEX_ID")
    print(f"[0/5] Uploading {title} to TwelveLabs ...")
    video_id = clients.upload_video(index_id, url=url, path=path)
    return ingest_video(video_id, title, index_id)
