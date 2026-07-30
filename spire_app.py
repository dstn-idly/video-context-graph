"""SPIRE — cinematic frontend for the Video Agent Context Graph.

Run with:
    streamlit run app.py

The interface uses real Neo4j data when the backend is available and falls back
to representative demo data so the product can still be presented standalone.
"""

from __future__ import annotations

import html
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import streamlit as st  # noqa: E402

from vcg import config, graph  # noqa: E402
from vcg.agent import build_agent  # noqa: E402


st.set_page_config(
    page_title="SPIRE · Video Context Graph",
    page_icon="◢",
    layout="wide",
    initial_sidebar_state="collapsed",
)


DEMO_DATA = {
    "stats": {"videos": 12, "scenes": 184, "entities": 67, "topics": 23},
    "videos": [
        {
            "title": "Builder Loft — Opening Session",
            "id": "demo-opening-session",
            "scenes": 42,
            "duration": "48:12",
        },
        {
            "title": "Graph Architecture Walkthrough",
            "id": "demo-graph-architecture",
            "scenes": 31,
            "duration": "24:08",
        },
        {
            "title": "Sponsor Demo Highlights",
            "id": "demo-sponsor-highlights",
            "scenes": 26,
            "duration": "16:44",
        },
    ],
    "entities": [
        {"name": "Knowledge Graph", "count": 38, "type": "concept"},
        {"name": "Video Agent", "count": 32, "type": "concept"},
        {"name": "Dustin", "count": 24, "type": "person"},
        {"name": "Neo4j", "count": 21, "type": "organization"},
        {"name": "TwelveLabs", "count": 18, "type": "organization"},
        {"name": "OpenAI", "count": 15, "type": "organization"},
        {"name": "Context", "count": 12, "type": "topic"},
        {"name": "Twitch", "count": 9, "type": "platform"},
    ],
    "scenes": [
        {
            "title": "Builder Loft — Opening Session",
            "start": 122,
            "description": "The team introduces a graph that remembers relationships across video.",
        },
        {
            "title": "Graph Architecture Walkthrough",
            "start": 487,
            "description": "Entity co-occurrence reveals a connection missed by transcript search.",
        },
        {
            "title": "Sponsor Demo Highlights",
            "start": 63,
            "description": "A semantic search result resolves to an exact source timestamp.",
        },
    ],
}


def format_time(seconds: float | int) -> str:
    """Format seconds as M:SS or H:MM:SS."""
    value = max(0, int(seconds or 0))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


@st.cache_data(ttl=20, show_spinner=False)
def load_context_data() -> tuple[dict, bool, str]:
    """Read the dashboard projection from Neo4j, with a demo-safe fallback."""
    try:
        if (
            not config.NEO4J_PASSWORD
            or "xxxx" in config.NEO4J_URI
            or config.NEO4J_PASSWORD.startswith("<")
        ):
            return DEMO_DATA, False, "Demo signal · backend ready to connect"
        stats = graph.stats()
        videos = graph.run_cypher(
            """
            MATCH (v:Video)
            OPTIONAL MATCH (v)-[:HAS_SCENE]->(s:Scene)
            RETURN v.title AS title, v.video_id AS id, count(s) AS scenes,
                   max(s.end) AS duration
            ORDER BY scenes DESC
            LIMIT 8
            """
        )
        entities = graph.run_cypher(
            """
            MATCH (e:Entity)<-[:MENTIONS]-(s:Scene)
            RETURN e.name AS name, coalesce(e.type, 'entity') AS type,
                   count(DISTINCT s) AS count
            ORDER BY count DESC
            LIMIT 12
            """
        )
        scenes = graph.run_cypher(
            """
            MATCH (v:Video)-[:HAS_SCENE]->(s:Scene)
            RETURN v.title AS title, s.start AS start,
                   s.description AS description
            ORDER BY s.start DESC
            LIMIT 8
            """
        )
        if not videos:
            return DEMO_DATA, False, "Graph connected · waiting for first ingest"

        for video in videos:
            video["duration"] = format_time(video.get("duration") or 0)
        return {
            "stats": stats,
            "videos": videos,
            "entities": entities,
            "scenes": scenes,
        }, True, "Neo4j graph online"
    except Exception as exc:
        return DEMO_DATA, False, f"Demo signal · {type(exc).__name__}"


def render_graph(entities: list[dict]) -> None:
    """Render a dependency-free, responsive context-graph preview."""
    nodes = entities[:8] or DEMO_DATA["entities"][:8]
    width, height = 760, 430
    center_x, center_y = width / 2, height / 2
    radii = [165, 155, 170, 150, 168, 158, 172, 152]
    positions: list[tuple[float, float]] = []

    for index, _ in enumerate(nodes):
        angle = (-math.pi / 2) + (2 * math.pi * index / len(nodes))
        radius = radii[index % len(radii)]
        positions.append(
            (center_x + math.cos(angle) * radius, center_y + math.sin(angle) * radius)
        )

    lines = []
    labels = []
    max_count = max((int(node.get("count", 1)) for node in nodes), default=1)
    for index, (node, (x, y)) in enumerate(zip(nodes, positions)):
        count = int(node.get("count", 1))
        size = 7 + (count / max_count) * 8
        color = "#b7ff5c" if index < 3 else "#7b8798"
        angle = math.degrees(math.atan2(y - center_y, x - center_x))
        distance_pct = math.hypot(x - center_x, y - center_y) / width * 100
        lines.append(
            f'<i class="edge" style="width:{distance_pct:.2f}%;transform:rotate({angle:.2f}deg)"></i>'
        )
        safe_name = html.escape(str(node.get("name", "Unknown")))
        labels.append(
            f"""
            <div class="node" style="left:{x / width * 100:.2f}%;top:{y / height * 100:.2f}%">
              <i style="width:{size * 2:.1f}px;height:{size * 2:.1f}px;background:{color}"></i>
              <b>{safe_name}</b>
              <small>{count} scenes</small>
            </div>
            """
        )

    graph_markup = f"""
    <div class="graph-shell">
      <div class="scanline"></div>
      <div class="edges">{''.join(lines)}</div>
      <div class="graph-core"><strong>SPIRE</strong><small>CONTEXT CORE</small></div>
      {''.join(labels)}
      <div class="graph-key"><i></i> LIVE ENTITY RELATIONSHIPS</div>
    </div>
    <style>
      * {{ box-sizing: border-box; }}
      body {{ margin: 0; background: transparent; overflow: hidden; }}
      .graph-shell {{
        position: relative; height: 430px; overflow: hidden;
        border: 1px solid rgba(255,255,255,.08); border-radius: 18px;
        background:
          radial-gradient(circle at 50% 48%, rgba(151,235,62,.09), transparent 29%),
          linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px),
          linear-gradient(90deg, rgba(255,255,255,.025) 1px, transparent 1px),
          #0c1015;
        background-size: auto, 32px 32px, 32px 32px, auto;
      }}
      .edge {{
        position: absolute; z-index: 0; left: 50%; top: 50%; height: 1px;
        transform-origin: left center; opacity: .65;
        background: repeating-linear-gradient(90deg, rgba(183,255,92,.38) 0 4px, transparent 4px 10px);
      }}
      .graph-core {{
        position: absolute; z-index: 1; left: 50%; top: 50%;
        display: grid; place-content: center; width: 68px; height: 68px;
        transform: translate(-50%,-50%); border-radius: 99px;
        color: #11170b; text-align: center;
        background: radial-gradient(circle at 40% 35%, #dcffa7, #8fe229);
        box-shadow: 0 0 0 18px rgba(183,255,92,.04), 0 0 32px rgba(183,255,92,.25);
      }}
      .graph-core strong {{ font: 800 9px Inter,sans-serif; letter-spacing: .14em; }}
      .graph-core small {{ margin-top: 2px; font: 700 5px Inter,sans-serif; letter-spacing: .08em; }}
      .node {{
        position: absolute; z-index: 1; display: flex; flex-direction: column; align-items: center;
        width: 120px; transform: translate(-50%,-50%); color: #dce3eb;
        font-family: Inter,ui-sans-serif,system-ui; text-align: center;
      }}
      .node i {{ display:block; border-radius:99px; box-shadow:0 0 0 8px rgba(183,255,92,.06); }}
      .node b {{ margin-top: 10px; font-size: 10px; font-weight: 600; }}
      .node small {{ margin-top: 2px; color:#647080; font-size:7px; letter-spacing:.08em; text-transform:uppercase; }}
      .graph-key {{
        position: absolute; left: 18px; bottom: 14px; color: #66717f;
        font: 600 9px Inter, sans-serif; letter-spacing: .12em;
      }}
      .graph-key i {{
        display: inline-block; width: 6px; height: 6px; margin-right: 7px;
        border-radius: 99px; background: #b7ff5c; box-shadow: 0 0 10px #b7ff5c;
      }}
      .scanline {{
        position: absolute; z-index: 2; inset: -40% 0 auto; height: 40%;
        background: linear-gradient(transparent, rgba(183,255,92,.025), transparent);
        animation: scan 7s linear infinite; pointer-events: none;
      }}
      @keyframes scan {{ to {{ transform: translateY(350%); }} }}
    </style>
    """
    st.html(graph_markup)


st.html(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Manrope:wght@400;500;600;700;800&display=swap');

      :root {
        --ink: #eef2f6;
        --muted: #7b8796;
        --line: rgba(255, 255, 255, .08);
        --panel: rgba(14, 18, 24, .88);
        --acid: #b7ff5c;
        --acid-soft: rgba(183, 255, 92, .12);
      }

      html, body, [class*="css"] { font-family: "Manrope", sans-serif; }
      .stApp {
        color: var(--ink);
        background:
          radial-gradient(circle at 78% -5%, rgba(183,255,92,.08), transparent 25%),
          radial-gradient(circle at 0% 60%, rgba(86,104,128,.08), transparent 28%),
          #080b0f;
      }
      [data-testid="stHeader"], [data-testid="stToolbar"] { background: transparent; }
      [data-testid="stSidebar"] { display: none; }
      .block-container { max-width: 1480px; padding: 1.6rem 2.2rem 3rem; }

      .spire-nav {
        display: flex; align-items: center; justify-content: space-between;
        padding: 4px 0 22px; border-bottom: 1px solid var(--line);
      }
      .brand { display: flex; align-items: center; gap: 12px; }
      .brand-mark {
        display: grid; place-items: center; width: 34px; height: 34px;
        color: #0b0e0a; background: var(--acid); border-radius: 9px;
        font-size: 17px; font-weight: 900; box-shadow: 0 0 24px rgba(183,255,92,.18);
      }
      .brand-name { font-size: 15px; font-weight: 800; letter-spacing: .17em; }
      .brand-name small {
        display: block; color: #566171; font: 500 8px "DM Mono", monospace;
        letter-spacing: .18em; margin-top: 2px;
      }
      .system-state {
        display: flex; align-items: center; gap: 8px; color: #7d8896;
        font: 500 9px "DM Mono", monospace; letter-spacing: .1em;
      }
      .system-state .pulse {
        width: 7px; height: 7px; border-radius: 99px; background: var(--acid);
        box-shadow: 0 0 12px var(--acid); animation: pulse 2s ease-in-out infinite;
      }
      @keyframes pulse { 50% { opacity: .35; } }

      .hero { padding: 48px 0 30px; }
      .eyebrow {
        color: var(--acid); font: 500 10px "DM Mono", monospace;
        letter-spacing: .16em; text-transform: uppercase;
      }
      .hero h1 {
        max-width: 770px; margin: 13px 0 14px; color: #f5f7f9;
        font-size: clamp(42px, 5.3vw, 76px); line-height: .98; letter-spacing: -.055em;
      }
      .hero h1 span { color: #596473; }
      .hero p { max-width: 650px; color: #8793a2; font-size: 14px; line-height: 1.7; }

      .metric-row {
        display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px;
        margin: 4px 0 26px; border: 1px solid var(--line);
        border-radius: 15px; overflow: hidden; background: var(--line);
      }
      .metric { padding: 17px 20px; background: #0d1117; }
      .metric label {
        color: #5d6877; font: 500 8px "DM Mono", monospace; letter-spacing: .15em;
      }
      .metric strong {
        display: block; margin-top: 6px; color: #e9edf1;
        font: 700 22px "DM Mono", monospace;
      }

      .section-kicker {
        color: #717d8c; font: 500 9px "DM Mono", monospace;
        letter-spacing: .16em; text-transform: uppercase; margin: 3px 0 12px;
      }
      .panel {
        min-height: 430px; padding: 18px; border: 1px solid var(--line);
        border-radius: 18px; background: linear-gradient(145deg, rgba(17,22,29,.94), rgba(10,14,19,.96));
      }
      .panel-head {
        display: flex; align-items: center; justify-content: space-between;
        margin-bottom: 18px;
      }
      .panel-title { font-size: 13px; font-weight: 700; }
      .panel-meta {
        color: #596575; font: 500 8px "DM Mono", monospace; letter-spacing: .08em;
      }
      .video-card {
        margin-bottom: 10px; padding: 13px; border: 1px solid rgba(255,255,255,.06);
        border-radius: 12px; background: rgba(255,255,255,.018);
      }
      .video-card:hover { border-color: rgba(183,255,92,.25); background: rgba(183,255,92,.025); }
      .video-top { display: flex; gap: 11px; align-items: flex-start; }
      .video-icon {
        display: grid; flex: 0 0 31px; place-items: center; width: 31px; height: 31px;
        border-radius: 8px; color: var(--acid); background: var(--acid-soft);
        font-size: 10px;
      }
      .video-title { color: #dce2e8; font-size: 11px; font-weight: 650; line-height: 1.35; }
      .video-meta { margin-top: 5px; color: #5f6b79; font: 400 8px "DM Mono", monospace; }
      .bar { height: 2px; margin-top: 12px; overflow: hidden; border-radius: 2px; background: #202731; }
      .bar i { display: block; height: 100%; background: linear-gradient(90deg, #769f43, var(--acid)); }

      .scene {
        position: relative; padding: 0 0 21px 22px; border-left: 1px solid #252d37;
      }
      .scene:last-child { border-left-color: transparent; }
      .scene:before {
        content: ""; position: absolute; left: -4px; top: 2px; width: 7px; height: 7px;
        border-radius: 99px; background: #3e4856; border: 2px solid #11161c;
      }
      .scene:first-of-type:before { background: var(--acid); box-shadow: 0 0 10px rgba(183,255,92,.5); }
      .scene-time { color: var(--acid); font: 500 8px "DM Mono", monospace; }
      .scene-title { margin: 5px 0; color: #cbd2da; font-size: 10px; font-weight: 650; }
      .scene-copy { color: #687585; font-size: 9px; line-height: 1.5; }

      .ask-panel {
        margin-top: 26px; padding: 24px 26px 15px; border: 1px solid var(--line);
        border-radius: 18px;
        background: linear-gradient(120deg, rgba(183,255,92,.055), rgba(13,17,23,.92) 34%);
      }
      .ask-label { color: var(--acid); font: 500 9px "DM Mono", monospace; letter-spacing: .16em; }
      .ask-title { margin: 8px 0 0; font-size: 23px; font-weight: 750; letter-spacing: -.025em; }
      .answer {
        margin: 12px 0; padding: 15px 17px; color: #cbd2da; font-size: 13px;
        line-height: 1.65; border-left: 2px solid var(--acid); background: rgba(255,255,255,.025);
      }
      .user-query { color: #919dab; font-size: 12px; margin: 10px 0 4px; }

      .stChatInputContainer, [data-testid="stChatInput"] { border-color: rgba(183,255,92,.18) !important; }
      [data-testid="stChatInput"] {
        background: #0d1218 !important; border-radius: 12px !important;
      }
      [data-testid="stChatInput"] textarea {
        color: #e7ebef !important; font-family: "Manrope", sans-serif !important;
      }
      .stButton button {
        border: 1px solid rgba(183,255,92,.22); color: #b7ff5c;
        background: rgba(183,255,92,.06); border-radius: 9px; font-size: 10px;
      }

      @media (max-width: 850px) {
        .block-container { padding: 1.1rem; }
        .hero { padding-top: 34px; }
        .metric-row { grid-template-columns: repeat(2, 1fr); }
        .system-state span:last-child { display: none; }
      }
    </style>
    """
)


data, is_live, status_text = load_context_data()
stats = data["stats"]

st.html(
    f"""
    <nav class="spire-nav">
      <div class="brand">
        <div class="brand-mark">◢</div>
        <div class="brand-name">SPIRE<small>VIDEO INTELLIGENCE SYSTEM</small></div>
      </div>
      <div class="system-state">
        <span class="pulse"></span>
        <span>{html.escape(status_text.upper())}</span>
      </div>
    </nav>
    <section class="hero">
      <div class="eyebrow">Context, reconstructed</div>
      <h1>Ask what the footage <span>knows.</span></h1>
      <p>
        SPIRE turns hours of video into a living context graph—connecting
        people, places, topics, and moments so every answer leads back to evidence.
      </p>
    </section>
    <div class="metric-row">
      <div class="metric"><label>VIDEOS INDEXED</label><strong>{int(stats.get('videos', 0)):02d}</strong></div>
      <div class="metric"><label>SCENES MAPPED</label><strong>{int(stats.get('scenes', 0)):03d}</strong></div>
      <div class="metric"><label>ENTITIES FOUND</label><strong>{int(stats.get('entities', 0)):02d}</strong></div>
      <div class="metric"><label>TOPIC CLUSTERS</label><strong>{int(stats.get('topics', 0)):02d}</strong></div>
    </div>
    """
)

left, middle, right = st.columns([0.82, 2.25, 0.95], gap="medium")

with left:
    video_cards = []
    for index, video in enumerate(data["videos"][:5]):
        title = html.escape(str(video.get("title") or "Untitled video"))
        scenes = int(video.get("scenes") or 0)
        duration = html.escape(str(video.get("duration") or "—"))
        fill = max(14, min(100, scenes * 2))
        video_cards.append(
            f"""
            <div class="video-card">
              <div class="video-top">
                <div class="video-icon">{index + 1:02d}</div>
                <div>
                  <div class="video-title">{title}</div>
                  <div class="video-meta">{duration} &nbsp;·&nbsp; {scenes} SCENES</div>
                </div>
              </div>
              <div class="bar"><i style="width:{fill}%"></i></div>
            </div>
            """
        )
    st.html(
        f"""
        <div class="section-kicker">Source library</div>
        <div class="panel">
          <div class="panel-head">
            <div class="panel-title">Indexed footage</div>
            <div class="panel-meta">{len(data['videos'])} ACTIVE</div>
          </div>
          {''.join(video_cards)}
        </div>
        """
    )

with middle:
    st.html('<div class="section-kicker">Context topology</div>')
    render_graph(data["entities"])

with right:
    scene_rows = []
    for scene in data["scenes"][:5]:
        scene_rows.append(
            f"""
            <div class="scene">
              <div class="scene-time">{format_time(scene.get('start', 0))}</div>
              <div class="scene-title">{html.escape(str(scene.get('title') or 'Untitled'))}</div>
              <div class="scene-copy">{html.escape(str(scene.get('description') or 'Scene indexed.'))}</div>
            </div>
            """
        )
    st.html(
        f"""
        <div class="section-kicker">Signal feed</div>
        <div class="panel">
          <div class="panel-head">
            <div class="panel-title">Recent moments</div>
            <div class="panel-meta">TIMECODE</div>
          </div>
          {''.join(scene_rows)}
        </div>
        """
    )

st.html(
    """
    <div class="ask-panel">
      <div class="ask-label">AGENT TERMINAL</div>
      <div class="ask-title">Interrogate the graph.</div>
    </div>
    """
)

if "messages" not in st.session_state:
    st.session_state.messages = []
if "agent" not in st.session_state:
    st.session_state.agent = None

for message in st.session_state.messages:
    css_class = "user-query" if message["role"] == "user" else "answer"
    prefix = "YOU · " if message["role"] == "user" else ""
    st.html(f'<div class="{css_class}">{prefix}{html.escape(message["content"])}</div>')

prompt = st.chat_input("Ask about a person, topic, relationship, or moment…")
if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.spinner("Traversing scenes, entities, and source moments…"):
        if not is_live:
            answer = (
                "SPIRE is currently displaying its demo signal. Connect Neo4j and the "
                "model credentials to answer from indexed footage; the frontend is "
                "already wired to the existing graph and agent interfaces."
            )
        else:
            try:
                if st.session_state.agent is None:
                    st.session_state.agent = build_agent()
                answer = str(st.session_state.agent(prompt))
            except Exception as exc:
                answer = f"The graph is online, but the reasoning agent is unavailable: {exc}"
    st.session_state.messages.append({"role": "assistant", "content": answer})
    st.rerun()
