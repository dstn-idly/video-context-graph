"""Strands tools. Each one is a capability the agent can choose to call."""
import re

from strands import tool

from . import clients, config, graph


@tool
def search_video_moments(query: str, limit: int = 5) -> str:
    """Search all indexed videos for moments matching a natural-language query.

    Use this when the user asks about something *visual or spoken* that happens
    in a video ("where does someone open the door?").

    Args:
        query: What to look for, in plain language.
        limit: Maximum number of clips to return.

    Returns:
        Matching clips with their video id and timestamps.
    """
    index_id = config.require("TWELVELABS_INDEX_ID")
    clips = clients.search(index_id, query, limit=limit)
    if not clips:
        return "No matching moments found."
    lines = []
    for c in clips:
        line = f"- video={c['video_id']} {c['start']:.1f}s-{c['end']:.1f}s (rank {c['rank']})"
        if c.get("transcription"):
            line += f'\n    transcript: "{c["transcription"]}"'
        lines.append(line)
    return "\n".join(lines)


@tool
def query_context_graph(cypher: str) -> str:
    """Run a read-only Cypher query against the video context graph.

    Use this for structural questions: which entities appear together, which
    scenes share a topic, how two people are connected across videos.

    Schema:
      (:Video {video_id, title, summary, source_url,
               duration_s, total_messages, msgs_per_min, dead_pct,
               peak_count, analyzed_at})
        -[:HAS_SCENE]->(:Scene {scene_id, start, end, description, tl_video_id})
        -[:HAS_MOMENT]->(:Moment {kind, score, start, end, reason,
                                  ai_verdict, tl_video_id, chatters, sample})
        -[:HAS_DEAD_SPOT]->(:DeadSpot {start, end, severity, note})
        -[:HAS_SEGMENT]->(:Segment {segment_id, video_id, start, end,
                                    tl_video_id, embedding})
      (:Segment)-[:NEXT]->(:Segment)
      (:Scene)-[:MENTIONS]->(:Entity {key, name, type})
      (:Scene)-[:ABOUT]->(:Topic {key, name})
      (:Scene)-[:HAS_VIRAL_MOMENT]->(:ViralMoment {
        moment_id, score, hook, emotion, why_viral, clip_title, start, end
      })
      (:Entity)-[:CO_OCCURS_WITH {count}]->(:Entity)

    Performance questions (dead air, chat velocity, best/most viral moments,
    trends across streams) live on Video/Moment/DeadSpot. Moment.kind is one of
    funny/hype/awkward/tense/action; Moment.ai_verdict is TwelveLabs' watched
    description. All start/end are seconds into the original video.
    Scene.tl_video_id is what describe_video needs.

    Entity and Topic are merged on `key`, a normalized form of the name, so the
    SAME real-world thing seen in two different streams is ONE node — that is
    what makes cross-video questions work. Match on e.key for identity and
    return e.name for display; count DISTINCT videos to find what recurs.

    (:Segment) nodes are ~10s Marengo embedding windows covering a video, chained
    in time by :NEXT, with start/end already on the original video's clock. Never
    RETURN sg.embedding — it is a 512-float vector and will flood your context.
    Use it only via the find_similar_footage tool, which does the vector search.

    Args:
        cypher: A read-only Cypher query. Must not contain write clauses.

    Returns:
        Query results as text, or an error message.
    """
    # Word-boundary match: "SET " with a trailing space misses "SET\n" (a
    # reproduced bypass) and false-refuses words like "asset". The real
    # enforcement is the READ_ACCESS session underneath — Aura rejects writes
    # server-side even if a query slips past this filter.
    if re.search(
        r"\b(CREATE|MERGE|DELETE|SET|REMOVE|DROP|DETACH|FOREACH|LOAD\s+CSV)\b",
        cypher, re.IGNORECASE,
    ):
        return "Refused: this tool is read-only. Use a MATCH/RETURN query."
    try:
        rows = graph.run_cypher_readonly(cypher)
    except Exception as exc:
        return f"Cypher error: {exc}"
    if not rows:
        return "No results."
    return "\n".join(str(row) for row in rows[:50])


@tool
def describe_video(video_id: str, question: str) -> str:
    """Ask a specific indexed video an open-ended question about its content.

    Use this when the graph lacks the detail and you need to re-watch the video.

    Args:
        video_id: The TwelveLabs video id.
        question: What you want to know about that video.

    Returns:
        The model's answer grounded in the video.
    """
    try:
        return clients.analyze(video_id, question)
    except Exception as exc:
        return f"Analysis failed: {exc}"


@tool
def graph_overview() -> str:
    """Get counts of what is currently in the context graph.

    Call this first when you are unsure what videos or entities exist.

    Returns:
        Node counts and the list of video titles.
    """
    try:
        counts = graph.stats()
        videos = graph.run_cypher("MATCH (v:Video) RETURN v.video_id AS id, v.title AS title")
    except Exception as exc:
        return f"Graph unavailable: {exc}"
    listing = "\n".join(f"- {v['title']} ({v['id']})" for v in videos) or "  (none)"
    return f"Counts: {counts}\nVideos:\n{listing}"


@tool
def rank_viral_moments(min_score: int = 60, limit: int = 10) -> str:
    """Rank the best clip candidates found across full-length videos.

    Args:
        min_score: Minimum viral-potential score from 0 to 100.
        limit: Maximum number of candidates to return.

    Returns:
        Ranked candidate moments with hooks, titles, reasons, and timestamps.
    """
    rows = graph.run_cypher(
        """
        MATCH (v:Video)-[:HAS_SCENE]->(s:Scene)-[:HAS_VIRAL_MOMENT]->(m:ViralMoment)
        WHERE m.score >= $min_score
        RETURN v.video_id AS video_id, v.title AS video, m.start AS start,
               m.end AS end, m.score AS score, m.clip_title AS title,
               m.hook AS hook, m.emotion AS emotion, m.why_viral AS why
        ORDER BY m.score DESC
        LIMIT $limit
        """,
        {"min_score": min_score, "limit": limit},
    )
    if not rows:
        return "No viral moments meet that threshold."
    return "\n".join(
        f"- {row['score']}/100 · {row['title']} · {row['video']} "
        f"{row['start']:.0f}s-{row['end']:.0f}s\n"
        f"  Hook: {row['hook']}\n  Why: {row['why']}"
        for row in rows
    )


@tool
def timestamp_link(video_id: str, seconds: int) -> str:
    """Build a shareable link that opens a video at an exact moment.

    Use this whenever you cite a moment, so the user can jump straight to it.

    Args:
        video_id: The Video node's video_id (not the scene's tl_video_id).
        seconds: How many seconds into the video the moment starts.

    Returns:
        A deep link, or a plain timestamp if the video has no source URL.
    """
    # Agents pass whatever identifier they saw last — the Video node id, the
    # bare VOD number, the video's title, or a scene's tl_video_id. Resolve all.
    rows = graph.run_cypher(
        """
        MATCH (v:Video)
        WHERE v.video_id = $id OR v.video_id = 'twitch:' + $id OR v.title = $id
           OR EXISTS { MATCH (v)-[:HAS_SCENE]->(s:Scene {tl_video_id: $id}) }
           OR EXISTS { MATCH (v)-[:HAS_MOMENT]->(m:Moment {tl_video_id: $id}) }
        RETURN v.source_url AS url, v.title AS title
        LIMIT 1
        """,
        {"id": video_id},
    )
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    stamp = f"{h:02d}:{m:02d}:{s:02d}"
    if not rows or not rows[0].get("url"):
        return f"{video_id} at {stamp} (no source URL recorded)"

    url = rows[0]["url"]
    if "twitch.tv/videos/" in url:  # Twitch wants 1h2m3s, not 01:02:03
        return f"{url}?t={h}h{m}m{s}s"
    return f"{url} at {stamp}"


@tool
def find_similar_footage(query: str, limit: int = 5) -> str:
    """Find stored footage that looks like a description, by meaning not keywords.

    Use this when the user describes a *vibe or visual* rather than a name —
    "that moment where he jumps out of his chair", "clips like the doorbell
    scare". It embeds the description and compares it against every video
    segment already in the graph, so it searches the whole back catalogue at
    once and costs nothing per video. Prefer search_video_moments only when you
    need TwelveLabs to re-scan the raw index instead.

    Args:
        query: A plain-language description of the footage you are looking for.
        limit: Maximum number of matching segments to return.

    Returns:
        Matching segments with video title, timestamps, and similarity score.
    """
    try:
        resp = clients.twelvelabs().embed.create(
            model_name=clients.EMBED_MODEL, text=query
        )
    except Exception as exc:
        return f"Could not embed that query: {exc}"

    # EmbeddingResponse.text_embedding.segments[0].float_ holds the 512 floats.
    text_embedding = getattr(resp, "text_embedding", None)
    segments = getattr(text_embedding, "segments", None) or []
    vector = next(
        (list(s.float_) for s in segments if getattr(s, "float_", None)), None
    )
    if not vector:
        err = getattr(text_embedding, "error_message", None)
        return f"Could not embed that query{': ' + err if err else '.'}"

    rows = graph.vector_search(vector, k=limit)
    if not rows:
        return (
            "No semantic matches. Either no video segments have been embedded "
            "yet, or nothing in the indexed footage resembles that description."
        )
    lines = []
    for row in rows:
        title = row.get("title") or row.get("video_id") or "unknown video"
        start, end = row.get("start") or 0, row.get("end") or 0
        lines.append(
            f"- {title} {start:.0f}s-{end:.0f}s "
            f"(similarity {row.get('score', 0):.3f}, video={row.get('video_id')})"
        )
    return "\n".join(lines)


@tool
def shared_entities(min_videos: int = 2) -> str:
    """List the people, things and places that recur across MULTIPLE videos.

    Use this for questions about patterns over a whole channel — what keeps
    coming back, what is this streamer's running bit, which guest shows up
    repeatedly. A single video's contents are not interesting here; the
    cross-video overlap is.

    Args:
        min_videos: How many different videos an entity must appear in to count.

    Returns:
        Recurring entities with their type, appearance count, and video titles.
    """
    try:
        rows = graph.cross_video_entities(min_videos=min_videos)
    except Exception as exc:
        return f"Graph unavailable: {exc}"
    if not rows:
        return (
            f"Nothing appears in {min_videos} or more videos yet. Either only one "
            "video has been analyzed, or the videos genuinely share no entities."
        )
    lines = []
    for row in rows:
        videos = ", ".join(row.get("videos") or [])
        lines.append(
            f"- {row['name']} ({row.get('type') or 'other'}) — in "
            f"{row['video_count']} videos: {videos}"
        )
    return "\n".join(lines)


ALL_TOOLS = [
    search_video_moments,
    query_context_graph,
    describe_video,
    graph_overview,
    rank_viral_moments,
    timestamp_link,
    find_similar_footage,
    shared_entities,
]
