// Questions that use the analysis results (run add_results_to_neo4j.py first).
// Paste one block at a time.


// A. Review first: unchecked results whose failure would cost the release the most trust
MATCH (m:Manuscript)
WHERE m.trust_lost_if_wrong IS NOT NULL
RETURN m.title AS manuscript, m.used_by_all_levels AS rests_on_it,
       m.trust_lost_if_wrong AS trust_lost_if_wrong
ORDER BY trust_lost_if_wrong DESC LIMIT 15;


// B. Who should review this manuscript?  Authors of the works it cites (weighted by how
//    often it cites them), plus, at half weight, authors cited by its most similar manuscripts.
//    Change the title to try another manuscript.
MATCH (m:Manuscript) WHERE m.title STARTS WITH 'The Exponential Time Hypothesis'
CALL (m) {
  MATCH (m)-[c:CITES]->(:Paper)<-[:AUTHORED]-(a:Author)
  RETURN a, sum(1 - exp(-(c.n_cites + 1) / 2.0)) AS s
  UNION ALL
  MATCH (m)-[r:SIMILAR_TO]->(:Manuscript)-[c:CITES]->(:Paper)<-[:AUTHORED]-(a:Author)
  RETURN a, sum(0.5 * r.score * (1 - exp(-(c.n_cites + 1) / 2.0))) AS s
}
RETURN a.name AS suggested_reviewer, round(sum(s), 2) AS score
ORDER BY score DESC LIMIT 15;


// C. Nearest neighbours of one manuscript (picture)
MATCH p = (m:Manuscript)-[:SIMILAR_TO]->(:Manuscript)
WHERE m.title STARTS WITH 'The Exponential Time Hypothesis'
RETURN p;


// D. Bridges: the most similar pairs that sit in DIFFERENT disciplines
MATCH (a:Manuscript)-[s:SIMILAR_TO]->(b:Manuscript),
      (a)-[:IN_FAMILY]->(:Family)-[:IN_DISCIPLINE]->(da:Discipline),
      (b)-[:IN_FAMILY]->(:Family)-[:IN_DISCIPLINE]->(db:Discipline)
WHERE da <> db AND a.mid < b.mid
RETURN a.title AS manuscript_1, da.name AS discipline_1,
       b.title AS manuscript_2, db.name AS discipline_2, round(s.score, 3) AS similarity
ORDER BY similarity DESC LIMIT 15;


// E. Map of disciplines: how strongly each pair of disciplines is linked by similarity
MATCH (a:Manuscript)-[s:SIMILAR_TO {same_family: false}]->(b:Manuscript),
      (a)-[:IN_FAMILY]->(:Family)-[:IN_DISCIPLINE]->(da:Discipline),
      (b)-[:IN_FAMILY]->(:Family)-[:IN_DISCIPLINE]->(db:Discipline)
WHERE da.name < db.name
RETURN da.name AS discipline_1, db.name AS discipline_2, count(*) AS links,
       round(sum(s.score), 2) AS strength
ORDER BY strength DESC LIMIT 20;


// F. Low trust, high use: where the shaky results sit in the foundation picture
MATCH p = (m:Manuscript)-[:DEPENDS_ON*1..2]->(f:Manuscript)
WHERE f.title STARTS WITH 'Log abundance in characteristic zero'
RETURN p;
// Then click a node: its trust and trust_lost_if_wrong are in the side panel.
