"""Neo4j layer: the context graph itself.

Schema
------
(:Video {video_id, title, summary})
(:Scene {scene_id, video_id, start, end, description})
(:Entity {name, type})          # person / object / place / org
(:Topic {name})

(:Video)-[:HAS_SCENE]->(:Scene)
(:Scene)-[:MENTIONS]->(:Entity)
(:Scene)-[:ABOUT]->(:Topic)
(:Entity)-[:CO_OCCURS_WITH {count}]->(:Entity)
(:Scene)-[:HAS_VIRAL_MOMENT]->(:ViralMoment {
    moment_id, score, hook, emotion, why_viral, clip_title
})
"""
from contextlib import contextmanager

from neo4j import GraphDatabase

from . import config

_driver = None


def get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            config.require("NEO4J_URI"),
            auth=(config.NEO4J_USERNAME, config.require("NEO4J_PASSWORD")),
        )
    return _driver


@contextmanager
def session():
    with get_driver().session(database=config.NEO4J_DATABASE) as s:
        yield s


CONSTRAINTS = [
    "CREATE CONSTRAINT video_id IF NOT EXISTS FOR (v:Video) REQUIRE v.video_id IS UNIQUE",
    "CREATE CONSTRAINT scene_id IF NOT EXISTS FOR (s:Scene) REQUIRE s.scene_id IS UNIQUE",
    "CREATE CONSTRAINT entity_name IF NOT EXISTS FOR (e:Entity) REQUIRE e.name IS UNIQUE",
    "CREATE CONSTRAINT topic_name IF NOT EXISTS FOR (t:Topic) REQUIRE t.name IS UNIQUE",
    "CREATE CONSTRAINT moment_id IF NOT EXISTS FOR (m:ViralMoment) REQUIRE m.moment_id IS UNIQUE",
]


def init_schema():
    with session() as s:
        for stmt in CONSTRAINTS:
            s.run(stmt)


def upsert_video(video_id: str, title: str, summary: str = "", source_url: str = ""):
    with session() as s:
        s.run(
            """
            MERGE (v:Video {video_id: $video_id})
            SET v.title = $title,
                v.source_url = $source_url,
                v.summary = CASE
                    WHEN $summary = '' THEN v.summary
                    WHEN v.summary IS NULL OR v.summary = '' THEN $summary
                    WHEN v.summary CONTAINS $summary THEN v.summary
                    ELSE v.summary + '\n\n' + $summary
                END
            """,
            video_id=video_id, title=title, summary=summary, source_url=source_url,
        )


def upsert_scene(video_id: str, scene: dict):
    """scene = {scene_id, start, end, description, entities, topics, tl_video_id}

    `start`/`end` are absolute seconds within the original video. `tl_video_id`
    is the TwelveLabs id to re-watch — for a segmented VOD that's the segment,
    which is not the same as `video_id`.
    """
    with session() as s:
        s.run(
            """
            MATCH (v:Video {video_id: $video_id})
            MERGE (sc:Scene {scene_id: $scene_id})
            SET sc.start = $start, sc.end = $end,
                sc.description = $description, sc.video_id = $video_id,
                sc.tl_video_id = $tl_video_id
            MERGE (v)-[:HAS_SCENE]->(sc)
            MERGE (m:ViralMoment {moment_id: $scene_id})
            SET m.score = $viral_score,
                m.hook = $hook,
                m.emotion = $emotion,
                m.why_viral = $why_viral,
                m.clip_title = $clip_title,
                m.start = $start,
                m.end = $end
            MERGE (sc)-[:HAS_VIRAL_MOMENT]->(m)
            WITH sc
            UNWIND $entities AS ent
              MERGE (e:Entity {name: ent.name})
              SET e.type = ent.type
              MERGE (sc)-[:MENTIONS]->(e)
            WITH sc
            UNWIND $topics AS topic
              MERGE (t:Topic {name: topic})
              MERGE (sc)-[:ABOUT]->(t)
            """,
            video_id=video_id,
            scene_id=scene["scene_id"],
            start=scene.get("start", 0),
            end=scene.get("end", 0),
            description=scene.get("description", ""),
            entities=scene.get("entities", []),
            topics=scene.get("topics", []),
            tl_video_id=scene.get("tl_video_id", video_id),
            viral_score=scene.get("viral_score", 0),
            hook=scene.get("hook", ""),
            emotion=scene.get("emotion", "other"),
            why_viral=scene.get("why_viral", ""),
            clip_title=scene.get("clip_title", ""),
        )


def rebuild_co_occurrences():
    """Recompute CO_OCCURS_WITH weights across the whole graph.

    Deliberately a full recompute with SET rather than an incremental +1: this
    runs once per ingested video (and once per VOD segment), and an incremental
    version would double-count every time you re-ingest or add a segment.
    """
    with session() as s:
        s.run(
            """
            MATCH (sc:Scene)-[:MENTIONS]->(a:Entity)
            MATCH (sc)-[:MENTIONS]->(b:Entity)
            WHERE a.name < b.name
            WITH a, b, count(DISTINCT sc) AS shared
            MERGE (a)-[r:CO_OCCURS_WITH]->(b)
            SET r.count = shared
            """
        )


def run_cypher(query: str, params: dict | None = None) -> list[dict]:
    """Read-only escape hatch used by the agent's graph tool."""
    with session() as s:
        return [record.data() for record in s.run(query, params or {})]


def stats() -> dict:
    rows = run_cypher(
        """
        OPTIONAL MATCH (v:Video)  WITH count(v) AS videos
        OPTIONAL MATCH (s:Scene)  WITH videos, count(s) AS scenes
        OPTIONAL MATCH (e:Entity) WITH videos, scenes, count(e) AS entities
        OPTIONAL MATCH (t:Topic)  RETURN videos, scenes, entities, count(t) AS topics
        """
    )
    return rows[0] if rows else {}


def wipe():
    with session() as s:
        s.run("MATCH (n) DETACH DELETE n")
