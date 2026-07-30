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

# demo UI — record this for the submission
streamlit run app.py
```

Note: TwelveLabs needs a **direct link to a raw media file**. YouTube and Google Drive share links will not work — upload to S3 and use a presigned URL, or pass a local file with `--path`.

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
