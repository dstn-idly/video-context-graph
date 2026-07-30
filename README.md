# Video Agent Context Graph

Hackathon project — *Hack the Video Agent Context Graph*, AWS Builder Loft SF, 30 Jul 2026.

An agent that answers questions about video by reasoning over a **knowledge graph built from what it saw**, rather than over a flat transcript.

```
video ──▶ TwelveLabs ──▶ OpenAI ──▶ Neo4j ──▶ Strands agent ──▶ answer
          (watches it)   (structures)  (context   (reasons, cites
                                        graph)     timestamps)
```

## Sponsor stack

| Tool | Where it's used |
|---|---|
| **TwelveLabs** | `src/vcg/clients.py` — indexes video, semantic clip search, Pegasus scene analysis |
| *Twitch* (source) | `src/vcg/twitch.py` Helix API · `src/vcg/downloader.py` TwitchDownloaderCLI for VOD video **and chat** |
| *Detection* | `src/vcg/highlights.py` — chat-velocity moment finding and dead-air coaching |
| **OpenAI** | `src/vcg/ingest.py` — structured outputs turn scene prose into typed nodes/edges; also the agent's default model |
| **Neo4j** | `src/vcg/graph.py` — the context graph (Video → Scene → Entity/Topic, entity co-occurrence) |
| **Strands Agents** | `src/vcg/agent.py`, `src/vcg/tools.py` — the agent and its four tools |
| **AWS** | Bedrock as an alternate agent backend (`AGENT_BACKEND=bedrock`), S3 for hosting video for ingest |

## Setup

```bash
./setup.sh
```

Then edit `.env` and run the preflight check — it tells you exactly what's missing:

```bash
source .venv/bin/activate
python scripts/check_env.py
```

### Getting the keys

- **OpenAI** — redeem the hackathon credit code at [platform.openai.com billing](https://platform.openai.com/settings/organization/billing/overview), then create a key under API keys.
- **TwelveLabs** — [playground.twelvelabs.io](https://playground.twelvelabs.io) → API Key.
- **Neo4j** — [console.neo4j.io](https://console.neo4j.io), create a free **AuraDB Free** instance. Download the credentials file when it's shown; the password is displayed exactly once. Use Aura rather than local Docker so both of us hit the same graph.
- **AWS** — only needed for the Bedrock backend or S3 hosting.

Local Neo4j instead of Aura (single-machine only):

```bash
docker compose up -d
```

## Running it

```bash
# once — create the TwelveLabs index, paste the id into .env
python scripts/create_index.py "hackathon-index"

# ingest a video (direct link to a raw .mp4, or a local file)
python scripts/ingest.py --url https://example.com/clip.mp4 --title "Demo clip"

# ask the agent from the terminal
python -m vcg.agent "Who appears in the most scenes, and where?"

# the app — SPIRE dashboard + Stream Autopsy workflow (top nav)
streamlit run app.py
```

Note: TwelveLabs needs a **direct link to a raw media file**. YouTube and Google Drive share links will not work — upload to S3 and use a presigned URL, or pass a local file with `--path`.

## The app

```bash
streamlit run app.py
```

Two pages behind the top nav:

- **SPIRE** — Barrat's presentation layer: live metrics, entity-graph topology, recent moments, and the agent terminal, all reading from Neo4j (with a demo-safe fallback when the graph is empty).
- **Stream Autopsy** — the workflow: **browse VODs → timeline → clips → ask**.

Paste a VOD URL (or browse a channel), and it downloads *chat only* and scores the stream. No video is touched until you ask for clips.

**Why chat first.** Chat is the cheapest high-quality signal a stream produces — when something funny, impressive, or awkward happens, chat reacts within a couple of seconds. So message velocity locates the moments for free, and TwelveLabs only ever analyzes the 30–60s windows chat already flagged. A 6-hour VOD becomes ~6 minutes of video to download and analyze, and the 1-hour analysis cap stops mattering.

**The timeline** shows engagement per 10s bucket, with:
- **dots** = clippable moments, colored by kind (funny / hype / awkward / tense / action)
- **red bands** = sustained dead air — the "tighten this up" coaching signal

Scoring is relative to *that stream's own* median, using median/MAD rather than mean/stddev. A single huge spike can't flatten the rest of the curve, and the same thresholds work for a 20-viewer stream and a 20,000-viewer one.

Moment classification comes from the emote and phrase vocabularies in `CATEGORIES` at the top of [highlights.py](src/vcg/highlights.py) — edit those to match your community and detection follows.

Clips are cut with TwitchDownloader's server-side crop, starting **15s before** the spike: chat reacts *after* the thing happens, so starting at the spike would cut off the punchline.

### Offline demo

Venue wifi is not to be trusted. This generates a realistic 3-hour synthetic chat log:

```bash
python scripts/make_demo.py
```

Then paste `999000111` into the app. The timeline, moments, and coaching view all work with no network and no API keys. (Clip cutting won't — there's no real video behind it.)

## Twitch ingest (CLI)

Pull real content in from Twitch. Use this on **your own channel**, or one that's given you permission — Twitch's ToS doesn't allow downloading broadcasts you don't have rights to.

Add a client id/secret to `.env` from [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps) (free, ~2 min; redirect URL can be `http://localhost`). Then:

```bash
# see what's there — downloads nothing
python scripts/twitch_ingest.py --channel yourname --list

# top clips: short, fast, best for a demo
python scripts/twitch_ingest.py --channel yourname --clips 10

# recent past broadcasts
python scripts/twitch_ingest.py --channel yourname --vods 3

# one specific VOD or clip
python scripts/twitch_ingest.py --url https://www.twitch.tv/videos/123456789
```

**Prefer clips for the demo.** They're under a minute, so ten of them ingest in the time one VOD takes — and ten clips make a far denser, more interesting graph than one long stream.

How it works, and why it's built this way:

1. **Helix API** lists VODs/clips (`src/vcg/twitch.py`). App access token, no user login needed.
2. **yt-dlp downloads** at 720p. Twitch serves HLS, not plain mp4, so TwelveLabs cannot fetch the URL itself — it has to come down locally first.
3. **ffmpeg splits** anything long into ≤45-min chunks, because **TwelveLabs analysis caps at 1 hour per video** and Twitch VODs routinely run 4–8. Splitting is a stream copy, so it's fast even on a multi-hour broadcast.
4. **Offsets are re-applied** so a scene 30s into chunk 3 is stored at its true broadcast time (e.g. 1:30:30). All segments attach to **one** `:Video` node; each `:Scene` keeps a `tl_video_id` pointing at the segment to re-watch.
5. The agent's `timestamp_link` tool turns any cited moment into a `?t=1h15m0s` deep link.

Chunk boundaries land on keyframes, so segments can run slightly longer than requested — that's why `--segment-minutes` is capped at 55 rather than 60, and why offsets are measured per chunk instead of assumed.

Downloaded video lands in `data/` and is gitignored. Segment files are deleted after ingest unless you pass `--keep-files`.

## Graph schema

```cypher
(:Video {video_id, title, summary})-[:HAS_SCENE]->(:Scene {scene_id, start, end, description})
(:Scene)-[:MENTIONS]->(:Entity {name, type})
(:Scene)-[:ABOUT]->(:Topic {name})
(:Entity)-[:CO_OCCURS_WITH {count}]->(:Entity)
```

Useful queries:

```cypher
// who shows up with whom
MATCH (a:Entity)-[r:CO_OCCURS_WITH]->(b:Entity)
RETURN a.name, b.name, r.count ORDER BY r.count DESC LIMIT 20;

// every scene featuring an entity, with timestamps
MATCH (v:Video)-[:HAS_SCENE]->(s:Scene)-[:MENTIONS]->(e:Entity {name: 'Alice'})
RETURN v.title, s.start, s.end, s.description ORDER BY s.start;

// bridge entities connecting two videos
MATCH (v1:Video)-[:HAS_SCENE]->(:Scene)-[:MENTIONS]->(e:Entity)<-[:MENTIONS]-(:Scene)<-[:HAS_SCENE]-(v2:Video)
WHERE v1.video_id < v2.video_id
RETURN e.name, v1.title, v2.title;
```

## Working together

Never commit `.env` — it's gitignored. Share keys over Discord DM.

```bash
git checkout -b your-name/what-youre-doing
# ... work ...
git add -A && git commit -m "what you did"
git push -u origin your-name/what-youre-doing
```

Open a PR, or just merge to `main` if we're moving fast. Pull often:

```bash
git pull --rebase origin main
```

Rough split — one person on ingest/graph quality (`ingest.py`, `graph.py`), one on agent/UI (`tools.py`, `agent.py`, `app.py`). They touch different files, so merges stay clean.

## Submission checklist

- [ ] Project name + README on the HackerSquad dashboard
- [ ] Stack: AWS, OpenAI, TwelveLabs, Neo4j, Strands Agents
- [ ] Git remote set to this repo
- [ ] Demo video recorded
- [ ] `./give_developer_feedback.sh` submitted for each sponsor (earns points)
- [ ] Save, then Submit
