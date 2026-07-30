"""Neo4j layer: the context graph itself.

Schema
------
(:Video {video_id, title, summary})
(:Scene {scene_id, video_id, start, end, description})
(:Entity {name, type})          # person / object / place / org
(:Topic {name})

(:Video)-[:HAS_SCENE]->(:Scene)
(:Video)-[:HAS_SEGMENT]->(:Segment {segment_id, start, end, embedding})
(:Segment)-[:NEXT]->(:Segment)  # temporal order within one video
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


@contextmanager
def read_session():
    """Server-enforced read-only session for agent-authored queries."""
    from neo4j import READ_ACCESS

    with get_driver().session(
        database=config.NEO4J_DATABASE, default_access_mode=READ_ACCESS
    ) as s:
        yield s


def run_cypher_readonly(query: str, params: dict | None = None) -> list[dict]:
    """Like run_cypher, but Aura itself rejects any write — the safety net
    under the agent's string-level guard, which can never be airtight."""
    with read_session() as s:
        return [record.data() for record in s.run(query, params or {})]


# Mirrors cypher/schema.cypher — kept here so the app is self-provisioning.
CONSTRAINTS = [
    "CREATE CONSTRAINT video_id IF NOT EXISTS FOR (v:Video) REQUIRE v.video_id IS UNIQUE",
    "CREATE CONSTRAINT scene_id IF NOT EXISTS FOR (s:Scene) REQUIRE s.scene_id IS UNIQUE",
    "CREATE CONSTRAINT moment_id IF NOT EXISTS FOR (m:Moment) REQUIRE m.moment_id IS UNIQUE",
    "CREATE CONSTRAINT dead_id IF NOT EXISTS FOR (d:DeadSpot) REQUIRE d.dead_id IS UNIQUE",
    "CREATE CONSTRAINT segment_id IF NOT EXISTS FOR (sg:Segment) REQUIRE sg.segment_id IS UNIQUE",
    "CREATE CONSTRAINT viral_id IF NOT EXISTS FOR (m:ViralMoment) REQUIRE m.moment_id IS UNIQUE",
    # Keyed by NORMALIZED name so the same thing in two streams is one node.
    "CREATE CONSTRAINT entity_key IF NOT EXISTS FOR (e:Entity) REQUIRE e.key IS UNIQUE",
    "CREATE CONSTRAINT topic_key IF NOT EXISTS FOR (t:Topic) REQUIRE t.key IS UNIQUE",
    "CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.name)",
    "CREATE INDEX segment_video IF NOT EXISTS FOR (sg:Segment) ON (sg.video_id)",
]

VECTOR_INDEX = """
CREATE VECTOR INDEX segment_embedding IF NOT EXISTS
FOR (sg:Segment) ON (sg.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 512,
  `vector.similarity_function`: 'cosine'
}}
"""

EMBED_DIMS = 512


def normalize_key(name: str) -> str:
    """Collapse a display name to a merge key.

    'The Bus', 'bus', 'Buses' all have to land on one node or the graph
    duplicates instead of accumulating. Deliberately conservative: casefold,
    strip punctuation and a leading article, and shed a trailing plural 's'.
    Semantic aliasing ("KaiCenat" vs "Kai") is OpenAI's job upstream.
    """
    import re

    text = re.sub(r"[^\w\s]", "", (name or "").casefold()).strip()
    text = re.sub(r"^(the|a|an)\s+", "", text)
    text = re.sub(r"\s+", " ", text)
    if len(text) > 3 and text.endswith("s") and not text.endswith("ss"):
        text = text[:-1]
    return text


# Entity/Topic used to be keyed by display name. Those UNIQUE constraints now
# actively block key-based merging ("funny" already exists as a Topic.name, so
# MERGE on key raises ConstraintValidationFailed). Dropping a constraint removes
# no data — and leaving them makes entity extraction fail outright.
OBSOLETE_CONSTRAINTS = ["entity_name", "topic_name"]


def init_schema():
    with session() as s:
        for name in OBSOLETE_CONSTRAINTS:
            try:
                s.run(f"DROP CONSTRAINT {name} IF EXISTS")
            except Exception:
                pass
        for stmt in CONSTRAINTS:
            s.run(stmt)
        try:
            s.run(VECTOR_INDEX)
        except Exception:
            # Older Neo4j without vector indexes — everything else still works,
            # only vector_search degrades.
            pass


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
            // Only create a ViralMoment when there is REAL viral analysis to
            // store. enrich_clips calls this with no viral fields, and an
            // unconditional MERGE minted blank nodes (score 0, "Untitled")
            // that then outranked the genuine moments in the UI's ranked panel.
            FOREACH (_ IN CASE
                WHEN $viral_score > 0 OR $clip_title <> '' OR $hook <> ''
                THEN [1] ELSE [] END |
              MERGE (m:ViralMoment {moment_id: $scene_id})
              SET m.score = $viral_score, m.hook = $hook, m.emotion = $emotion,
                  m.why_viral = $why_viral, m.clip_title = $clip_title,
                  m.start = $start, m.end = $end
              MERGE (sc)-[:HAS_VIRAL_MOMENT]->(m))
            // FOREACH, not UNWIND: UNWIND over an empty list yields zero rows
            // and silently kills every later clause.
            // MERGE on `key` (normalized), not display name — that is what makes
            // the same entity in two different streams collapse to ONE node.
            FOREACH (ent IN $entities |
              MERGE (e:Entity {key: ent.key})
              SET e.name = coalesce(e.name, ent.name), e.type = ent.type
              MERGE (sc)-[:MENTIONS]->(e))
            FOREACH (tp IN $topics |
              MERGE (t:Topic {key: tp.key})
              SET t.name = coalesce(t.name, tp.name)
              MERGE (sc)-[:ABOUT]->(t))
            """,
            video_id=video_id,
            scene_id=scene["scene_id"],
            start=scene.get("start", 0),
            end=scene.get("end", 0),
            description=scene.get("description", ""),
            # Normalize here so every write path merges consistently.
            entities=[
                {"key": normalize_key(e["name"]), "name": e["name"],
                 "type": e.get("type", "other")}
                for e in scene.get("entities", []) if e.get("name")
            ],
            topics=[
                {"key": normalize_key(t_), "name": t_}
                for t_ in scene.get("topics", []) if t_
            ],
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


def save_performance(video_id: str, title: str, url: str, summary: dict,
                     peaks: list, dead_spots: list):
    """Persist a chat-analysis run as graph data.

    This is what makes Neo4j the product's datastore rather than a cache:
    every analyzed VOD contributes (:Moment) and (:DeadSpot) nodes, so
    cross-stream questions — is dead air trending down, which kind of moment
    do I produce most — are single Cypher queries.
    """
    init_schema()
    with session() as s:
        s.run(
            """
            MERGE (v:Video {video_id: $video_id})
            SET v.source_url = $url,
                v.title = CASE
                    WHEN v.title IS NULL OR v.title = '' OR v.title STARTS WITH 'VOD '
                    THEN $title ELSE v.title
                END,
                v.duration_s = $duration, v.total_messages = $messages,
                v.msgs_per_min = $mpm, v.dead_pct = $dead_pct,
                v.peak_count = $peaks,
                // keep first-analysis time so re-analyzing an old VOD
                // doesn't shuffle it to the end of the dashboard trend
                v.analyzed_at = coalesce(v.analyzed_at, datetime())
            """,
            video_id=video_id,
            url=url,
            title=title,
            duration=summary.get("duration_seconds", 0),
            messages=summary.get("total_messages", 0),
            mpm=summary.get("messages_per_minute", 0),
            dead_pct=summary.get("dead_time_pct", 0),
            peaks=len(peaks),
        )
        # Re-analysis prunes chat-derived nodes that no longer exist in the new
        # peak set — otherwise detector tuning accumulates stale moments.
        # Surviving moments are MERGEd below, which preserves any ai_verdict
        # already attached. TwelveLabs scout finds are never touched.
        s.run(
            """
            MATCH (v:Video {video_id: $video_id})-[:HAS_MOMENT]->(m:Moment)
            WHERE coalesce(m.detector, 'chat') = 'chat'
              AND NOT m.moment_id IN $keep
            DETACH DELETE m
            """,
            video_id=video_id,
            keep=[f"{video_id}:m{m.start}" for m in peaks],
        )
        s.run(
            """
            MATCH (v:Video {video_id: $video_id})-[:HAS_DEAD_SPOT]->(d:DeadSpot)
            WHERE NOT d.dead_id IN $keep
            DETACH DELETE d
            """,
            video_id=video_id,
            keep=[f"{video_id}:d{d.start}" for d in dead_spots],
        )
        for m in peaks:
            s.run(
                """
                MATCH (v:Video {video_id: $video_id})
                MERGE (mo:Moment {moment_id: $mid})
                SET mo.kind = $kind, mo.score = $score, mo.start = $start,
                    mo.end = $end, mo.reason = $reason, mo.chatters = $chatters,
                    mo.sample = $sample, mo.detector = 'chat'
                MERGE (v)-[:HAS_MOMENT]->(mo)
                """,
                video_id=video_id, mid=f"{video_id}:m{m.start}",
                kind=m.kind, score=m.score, start=m.start, end=m.end,
                reason=m.reason, chatters=m.chatters, sample=m.sample[:4],
            )
        for d in dead_spots:
            s.run(
                """
                MATCH (v:Video {video_id: $video_id})
                MERGE (ds:DeadSpot {dead_id: $did})
                SET ds.start = $start, ds.end = $end, ds.severity = $severity,
                    ds.note = $note
                MERGE (v)-[:HAS_DEAD_SPOT]->(ds)
                """,
                video_id=video_id, did=f"{video_id}:d{d.start}",
                start=d.start, end=d.end, severity=d.severity, note=d.note,
            )


def set_moment_verdict(video_id: str, start: int, verdict: str, tl_video_id: str):
    """Attach TwelveLabs' watched verdict to the chat-detected moment."""
    with session() as s:
        s.run(
            """
            MATCH (:Video {video_id: $video_id})-[:HAS_MOMENT]->(m:Moment {moment_id: $mid})
            SET m.ai_verdict = $verdict, m.tl_video_id = $tl
            """,
            video_id=video_id, mid=f"{video_id}:m{start}", verdict=verdict, tl=tl_video_id,
        )


def save_visual_moment(video_id: str, start: int, end: int, score: float,
                       description: str, tl_video_id: str):
    """A moment TwelveLabs found by *watching* — independent of chat.

    Uses the same Moment label so leaderboards and the agent see both
    detectors side by side; `detector` says who found it.
    """
    with session() as s:
        s.run(
            """
            MERGE (v:Video {video_id: $video_id})
            MERGE (mo:Moment {moment_id: $mid})
            SET mo.kind = 'visual', mo.detector = 'twelvelabs',
                mo.score = $score, mo.start = $start, mo.end = $end,
                mo.reason = 'TwelveLabs spotted this by watching the footage',
                mo.ai_verdict = $description, mo.tl_video_id = $tl
            MERGE (v)-[:HAS_MOMENT]->(mo)
            """,
            video_id=video_id, mid=f"{video_id}:v{start}",
            score=score, start=start, end=end,
            description=description, tl=tl_video_id,
        )


def upsert_segments(video_id: str, tl_video_id: str, segments: list[dict],
                    offset: float = 0) -> int:
    """Store Marengo segment embeddings as (:Segment) nodes on the VOD clock.

    `segments` is exactly what clients.segment_embeddings returns —
    [{start, end, vector}] with times relative to whatever media was uploaded to
    TwelveLabs. We usually upload a *clip*, so `offset` (the clip's start inside
    the full VOD) is added to every timestamp; without it a hit at 0:12 of a clip
    would link to 0:12 of a 51-minute stream instead of the real moment.

    Idempotent by construction: segment_id is derived from the absolute start, so
    re-running the same clip overwrites in place, and the :NEXT chain is dropped
    and rebuilt from the stored order so a later insert can't leave a link that
    hops over it.

    Returns the number of segments written.
    """
    if not segments:
        return 0

    rows = []
    for seg in segments:
        vector = seg.get("vector") or []
        # A wrong-width vector is silently unindexable — drop it here rather
        # than discover it as a permanently empty vector_search.
        if len(vector) != EMBED_DIMS:
            continue
        start = float(seg.get("start", 0) or 0) + float(offset or 0)
        end = float(seg.get("end", 0) or 0) + float(offset or 0)
        rows.append(
            {
                "segment_id": f"{video_id}:sg{int(round(start))}",
                "start": start,
                "end": end,
                "embedding": [float(x) for x in vector],
            }
        )
    if not rows:
        return 0

    # Self-provisioning, same contract as save_performance: the vector index has
    # to exist before the first write or the embeddings sit there unsearchable.
    try:
        init_schema()
    except Exception:
        pass

    with session() as s:
        s.run(
            """
            // MERGE, not MATCH: segments can land before the chat pass has
            // created the Video, and a MATCH miss would silently write nothing.
            MERGE (v:Video {video_id: $video_id})
            WITH v
            UNWIND $rows AS row
            MERGE (sg:Segment {segment_id: row.segment_id})
            SET sg.video_id = $video_id, sg.tl_video_id = $tl_video_id,
                sg.start = row.start, sg.end = row.end,
                sg.embedding = row.embedding
            MERGE (v)-[:HAS_SEGMENT]->(sg)
            """,
            video_id=video_id, tl_video_id=tl_video_id, rows=rows,
        )
        s.run(
            """
            MATCH (:Video {video_id: $video_id})-[:HAS_SEGMENT]->(:Segment)-[r:NEXT]->(:Segment)
            DELETE r
            """,
            video_id=video_id,
        )
        s.run(
            """
            MATCH (:Video {video_id: $video_id})-[:HAS_SEGMENT]->(sg:Segment)
            WITH sg ORDER BY sg.start
            WITH collect(sg) AS segs
            UNWIND range(0, size(segs) - 2) AS i
            WITH segs[i] AS a, segs[i + 1] AS b
            MERGE (a)-[:NEXT]->(b)
            """,
            video_id=video_id,
        )
    return len(rows)


def vector_search(query_vector: list[float], k: int = 5) -> list[dict]:
    """Nearest segments to a query vector, with the timestamps to jump to.

    Runs through the read-only session, so this can never mutate the graph even
    though it calls a procedure. Returns [] rather than raising when the vector
    index has not been created yet (a graph analyzed before the semantic layer
    existed) — the UI then just shows no semantic hits instead of an error.
    """
    if not query_vector:
        return []
    try:
        return run_cypher_readonly(
            """
            CALL db.index.vector.queryNodes('segment_embedding', $k, $vec)
            YIELD node AS sg, score
            OPTIONAL MATCH (v:Video)-[:HAS_SEGMENT]->(sg)
            RETURN v.video_id AS video_id, v.title AS title,
                   v.source_url AS url, sg.segment_id AS segment_id,
                   sg.start AS start, sg.end AS end,
                   sg.tl_video_id AS tl_video_id, score
            ORDER BY score DESC
            """,
            {"k": int(k), "vec": [float(x) for x in query_vector]},
        )
    except Exception:
        return []


def cross_video_entities(min_videos: int = 2) -> list[dict]:
    """Entities that show up in MORE THAN ONE video — the product thesis.

    A per-video summary is a feature; knowing that the same bit, guest or game
    recurs across a month of streams is the graph paying for itself. Cheap on
    purpose: one traversal, one aggregation, no path expansion.
    """
    return run_cypher(
        """
        MATCH (v:Video)-[:HAS_SCENE]->(:Scene)-[:MENTIONS]->(e:Entity)
        WITH e, count(DISTINCT v) AS video_count,
             collect(DISTINCT v.title) AS videos
        WHERE video_count >= $min_videos
        RETURN e.name AS name, e.key AS key, e.type AS type,
               videos, video_count
        ORDER BY video_count DESC, name
        """,
        {"min_videos": max(2, int(min_videos))},
    )


def entity_neighborhood(name: str) -> list[dict]:
    """Every appearance of one entity: which video, which scene, what second.

    Matches on the normalized key first (that is what MERGE collapsed on) and
    falls back to a case-insensitive display-name match, so the UI can hand this
    whatever text the user clicked.
    """
    return run_cypher(
        """
        MATCH (e:Entity)
        WHERE e.key = $key OR toLower(e.name) = toLower($name)
        MATCH (v:Video)-[:HAS_SCENE]->(sc:Scene)-[:MENTIONS]->(e)
        RETURN e.name AS entity, e.type AS type,
               v.video_id AS video_id, v.title AS title, v.source_url AS url,
               sc.scene_id AS scene_id, sc.start AS start, sc.end AS end,
               sc.description AS description, sc.tl_video_id AS tl_video_id
        ORDER BY v.title, sc.start
        """,
        {"key": normalize_key(name), "name": name or ""},
    )


def video_moments(video_id: str) -> list[dict]:
    """Every moment for one video, both detectors, ordered by time."""
    return run_cypher(
        """
        MATCH (v:Video {video_id: $id})-[:HAS_MOMENT]->(m:Moment)
        RETURN m.kind AS kind, m.score AS score, m.start AS start, m.end AS end,
               m.reason AS reason, m.ai_verdict AS ai_verdict,
               coalesce(m.detector, 'chat') AS detector
        ORDER BY m.start
        """,
        {"id": video_id},
    )


def video_dead_spots(video_id: str) -> list[dict]:
    return run_cypher(
        """
        MATCH (v:Video {video_id: $id})-[:HAS_DEAD_SPOT]->(d:DeadSpot)
        RETURN d.start AS start, d.end AS end, d.severity AS severity, d.note AS note
        ORDER BY d.start
        """,
        {"id": video_id},
    )


def performance_overview() -> list[dict]:
    """Per-VOD metrics, oldest first — the dashboard trend reads this."""
    return run_cypher(
        """
        MATCH (v:Video)
        WHERE v.analyzed_at IS NOT NULL
        RETURN v.video_id AS video_id, v.title AS title, v.source_url AS url,
               v.duration_s AS duration_s, v.msgs_per_min AS msgs_per_min,
               v.dead_pct AS dead_pct, v.peak_count AS peak_count,
               v.total_messages AS total_messages,
               toString(v.analyzed_at) AS analyzed_at
        ORDER BY v.analyzed_at
        """
    )


def top_moments(limit: int = 12) -> list[dict]:
    """Best moments across every analyzed stream, TwelveLabs verdicts included."""
    return run_cypher(
        """
        MATCH (v:Video)-[:HAS_MOMENT]->(m:Moment)
        RETURN v.video_id AS video_id, v.title AS title, v.source_url AS url,
               m.kind AS kind, m.score AS score, m.start AS start, m.end AS end,
               m.reason AS reason, m.ai_verdict AS ai_verdict, m.sample AS sample,
               coalesce(m.detector, 'chat') AS detector
        ORDER BY m.score DESC
        LIMIT $limit
        """,
        {"limit": limit},
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
