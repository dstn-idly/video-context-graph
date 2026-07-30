"""Strands tools. Each one is a capability the agent can choose to call."""
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
      (:Video {video_id, title, summary, source_url})
        -[:HAS_SCENE]->(:Scene {scene_id, start, end, description, tl_video_id})
      (:Scene)-[:MENTIONS]->(:Entity {name, type})
      (:Scene)-[:ABOUT]->(:Topic {name})
      (:Scene)-[:HAS_VIRAL_MOMENT]->(:ViralMoment {
        moment_id, score, hook, emotion, why_viral, clip_title, start, end
      })
      (:Entity)-[:CO_OCCURS_WITH {count}]->(:Entity)

    Scene.start/end are seconds into the original video. Scene.tl_video_id is
    what describe_video needs (for a long VOD it is the segment, not the Video).

    Args:
        cypher: A read-only Cypher query. Must not contain write clauses.

    Returns:
        Query results as text, or an error message.
    """
    forbidden = ("CREATE", "MERGE", "DELETE", "SET ", "REMOVE", "DROP", "DETACH")
    if any(word in cypher.upper() for word in forbidden):
        return "Refused: this tool is read-only. Use a MATCH/RETURN query."
    try:
        rows = graph.run_cypher(cypher)
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
    rows = graph.run_cypher(
        "MATCH (v:Video {video_id: $id}) RETURN v.source_url AS url, v.title AS title",
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


ALL_TOOLS = [
    search_video_moments,
    query_context_graph,
    describe_video,
    graph_overview,
    rank_viral_moments,
    timestamp_link,
]
