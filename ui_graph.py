"""Graph — what's in Neo4j, plus doors into the real Neo4j webapps."""
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import streamlit as st  # noqa: E402

import ui_common as ui  # noqa: E402
from vcg import config, graph  # noqa: E402

ui.inject_css()
ui.page_header("Graph", "Every stream, moment, and verdict lives in Neo4j Aura — open it directly.")

try:
    stats = graph.stats()
    counts = graph.run_cypher(
        """
        MATCH (m:Moment) WITH count(m) AS moments
        OPTIONAL MATCH (d:DeadSpot) RETURN moments, count(d) AS dead_spots
        """
    )
    extra = counts[0] if counts else {"moments": 0, "dead_spots": 0}
    ui.metric_row([
        ("Videos", str(stats.get("videos", 0)), ""),
        ("Scenes", str(stats.get("scenes", 0)), ""),
        ("Moments", str(extra.get("moments", 0)), ""),
        ("Dead spots", str(extra.get("dead_spots", 0)), ""),
        ("Entities", str(stats.get("entities", 0)), ""),
    ])
except Exception as exc:
    st.error(f"Neo4j unreachable: {exc}")

st.html('<div class="tl-kicker">Open the Neo4j webapp</div>')
uri = config.NEO4J_URI
connect = urllib.parse.quote(uri, safe="")
c1, c2, c3 = st.columns(3)
c1.link_button("🔵 Neo4j Workspace (query + visualize)",
               f"https://workspace.neo4j.io/connection/connect?connectURL={connect}",
               use_container_width=True)
c2.link_button("🧭 Aura Console", "https://console.neo4j.io", use_container_width=True)
c3.link_button("🌐 Browser", f"https://browser.neo4j.io/?connectURL={connect}",
               use_container_width=True)
st.caption(f"Instance: `{uri}` — sign in with the team credentials (shared over DM, never committed).")

st.html('<div class="tl-kicker">The schema</div>')
st.code(
    """(:Video {title, duration_s, msgs_per_min, dead_pct, analyzed_at})
  -[:HAS_MOMENT]->(:Moment {kind, score, start, reason, ai_verdict, detector})
  -[:HAS_DEAD_SPOT]->(:DeadSpot {start, end, severity})
  -[:HAS_SCENE]->(:Scene {start, end, description, tl_video_id})
(:Scene)-[:MENTIONS]->(:Entity)   (:Scene)-[:ABOUT]->(:Topic)""",
    language="cypher",
)

st.html('<div class="tl-kicker">Try these in Workspace</div>')
st.code(
    """// The whole graph at a glance
MATCH p=(v:Video)-[]->(x) RETURN p LIMIT 100;

// Moments both detectors agree on (chat spike + TwelveLabs verdict)
MATCH (v:Video)-[:HAS_MOMENT]->(m:Moment)
WHERE m.ai_verdict IS NOT NULL
RETURN v.title, m.kind, m.score, m.start, m.ai_verdict
ORDER BY m.score DESC;

// Is dead air trending down across my streams?
MATCH (v:Video) WHERE v.analyzed_at IS NOT NULL
RETURN v.title, v.dead_pct ORDER BY v.analyzed_at;""",
    language="cypher",
)
