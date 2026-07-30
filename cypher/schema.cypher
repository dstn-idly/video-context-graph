// SPIRE graph schema — run once per database.
//   cat cypher/schema.cypher | cypher-shell -a "$NEO4J_URI" -u "$NEO4J_USERNAME" -p "$NEO4J_PASSWORD"
// or just start the app: graph.init_schema() applies all of this idempotently.

// ---- identity -------------------------------------------------------------
CREATE CONSTRAINT video_id IF NOT EXISTS
  FOR (v:Video) REQUIRE v.video_id IS UNIQUE;
CREATE CONSTRAINT scene_id IF NOT EXISTS
  FOR (s:Scene) REQUIRE s.scene_id IS UNIQUE;
CREATE CONSTRAINT moment_id IF NOT EXISTS
  FOR (m:Moment) REQUIRE m.moment_id IS UNIQUE;
CREATE CONSTRAINT dead_id IF NOT EXISTS
  FOR (d:DeadSpot) REQUIRE d.dead_id IS UNIQUE;
CREATE CONSTRAINT segment_id IF NOT EXISTS
  FOR (sg:Segment) REQUIRE sg.segment_id IS UNIQUE;

// Entities and Topics are keyed by NORMALIZED name, not display name.
// This is the whole point of the context graph: the same real-world thing
// seen in two different streams collapses to ONE node instead of duplicating.
CREATE CONSTRAINT entity_key IF NOT EXISTS
  FOR (e:Entity) REQUIRE e.key IS UNIQUE;
CREATE CONSTRAINT topic_key IF NOT EXISTS
  FOR (t:Topic) REQUIRE t.key IS UNIQUE;

// ---- lookup indexes -------------------------------------------------------
CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.name);
CREATE INDEX moment_start IF NOT EXISTS FOR (m:Moment) ON (m.start);
CREATE INDEX segment_video IF NOT EXISTS FOR (sg:Segment) ON (sg.video_id);

// ---- vector index over Marengo segment embeddings -------------------------
// 512 dims, cosine — matches marengo3.0 visual embeddings.
CREATE VECTOR INDEX segment_embedding IF NOT EXISTS
  FOR (sg:Segment) ON (sg.embedding)
  OPTIONS { indexConfig: {
    `vector.dimensions`: 512,
    `vector.similarity_function`: 'cosine'
  }};

// ---- shape ----------------------------------------------------------------
// (:Video {video_id, title, source_url, duration_s, msgs_per_min, dead_pct, analyzed_at})
//   -[:HAS_MOMENT]->   (:Moment {kind, score, start, end, reason, ai_verdict, detector})
//   -[:HAS_DEAD_SPOT]->(:DeadSpot {start, end, severity})
//   -[:HAS_SCENE]->    (:Scene {scene_id, start, end, description, tl_video_id})
//   -[:HAS_SEGMENT]->  (:Segment {segment_id, start, end, embedding})
// (:Segment)-[:NEXT]->(:Segment)          temporal order within a video
// (:Scene)-[:MENTIONS]->(:Entity {key, name, type})   MERGE'd across videos
// (:Scene)-[:ABOUT]->(:Topic {key, name})             MERGE'd across videos
// (:Entity)-[:CO_OCCURS_WITH {count}]->(:Entity)
