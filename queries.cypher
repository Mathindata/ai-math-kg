// Starter questions for the OpenAI math knowledge graph.
// Paste one block at a time into Neo4j Browser (or Desktop's Query tab).
//
// Nodes:  Manuscript (extra labels Formalized, Withdrawn), Family, Discipline,
//         Paper (cited human work), Author, ArxivCategory
// Edges:  (Manuscript)-[:IN_FAMILY]->(Family)-[:IN_DISCIPLINE]->(Discipline)
//         (Manuscript)-[:CITES {n_cites}]->(Paper)<-[:AUTHORED]-(Author)
//         (Manuscript)-[:DEPENDS_ON {n_cites}]->(Manuscript)   uses a result of (cited in the body)
//         (Manuscript)-[:MENTIONS]->(Manuscript)               cited only in the introduction
//         (Manuscript)-[:PREVIOUS_VERSION]->(Manuscript)       newer -> older edition
//         (Manuscript)-[:SUBSUMED_BY]->(Manuscript)
//         (Paper)-[:IN_CATEGORY]->(ArxivCategory)


// 0. A picture of the schema
CALL db.schema.visualization();


// 1. Where is the work? Families, manuscripts and Lean coverage per discipline
MATCH (d:Discipline)<-[:IN_DISCIPLINE]-(f:Family)<-[:IN_FAMILY]-(m:Manuscript {status: 'current'})
RETURN d.name AS discipline,
       count(DISTINCT f) AS families,
       count(m) AS manuscripts,
       round(100.0 * sum(CASE WHEN m.lean_formalized THEN 1 ELSE 0 END) / count(m)) AS pct_lean
ORDER BY manuscripts DESC;


// 2. Foundations: manuscripts that many others build on. Are they checked by Lean?
MATCH (m:Manuscript {status: 'current'})<-[d:DEPENDS_ON]-(user:Manuscript)
RETURN m.title AS manuscript, m.lean_formalized AS lean,
       count(user) AS used_by, sum(d.n_cites) AS times_cited
ORDER BY used_by DESC LIMIT 15;


// 3. The October withdrawal: one flawed paper and everything resting on it
MATCH path = (x:Manuscript)-[:DEPENDS_ON*1..]->(w:Withdrawn)
RETURN path;


// 4. Exposure: current manuscripts that rest (directly or not) on many results
//    no Lean proof has checked. A first, crisp version of the review list.
MATCH (m:Manuscript {status: 'current'})-[:DEPENDS_ON*1..]->(p:Manuscript)
WITH m, collect(DISTINCT p) AS prereqs
WITH m, size(prereqs) AS n_prereqs,
     size([p IN prereqs WHERE NOT p.lean_formalized]) AS n_unchecked
WHERE n_unchecked > 0
RETURN m.title AS manuscript, m.lean_formalized AS lean_itself, n_prereqs, n_unchecked
ORDER BY n_unchecked DESC, n_prereqs DESC LIMIT 20;


// 5. The longest chains of reasoning inside the release
MATCH path = (a:Manuscript)-[:DEPENDS_ON*]->(b:Manuscript)
WHERE NOT ()-[:DEPENDS_ON]->(a) AND NOT (b)-[:DEPENDS_ON]->()
RETURN length(path) AS depth, [n IN nodes(path) | n.title] AS chain
ORDER BY depth DESC LIMIT 5;


// 6. Which human literature does the AI lean on most?
MATCH (m:Manuscript)-[c:CITES]->(p:Paper)
RETURN p.title AS work, p.year AS year, count(DISTINCT m) AS manuscripts,
       sum(c.n_cites) AS times_cited
ORDER BY manuscripts DESC LIMIT 20;


// 7. Bridge works: cited from the most different disciplines
MATCH (d:Discipline)<-[:IN_DISCIPLINE]-(:Family)<-[:IN_FAMILY]-(:Manuscript)-[:CITES]->(p:Paper)
WITH p, collect(DISTINCT d.name) AS disciplines
WHERE size(disciplines) >= 4
RETURN p.title AS work, p.year AS year, size(disciplines) AS n_disciplines, disciplines
ORDER BY n_disciplines DESC LIMIT 15;


// 8. Most cited human authors
MATCH (a:Author)-[:AUTHORED]->(p:Paper)<-[:CITES]-(m:Manuscript)
RETURN a.name AS author, count(DISTINCT p) AS works, count(DISTINCT m) AS manuscripts
ORDER BY manuscripts DESC LIMIT 20;


// 9. How old is the literature the AI builds on? (cited works by decade)
MATCH (:Manuscript)-[:CITES]->(p:Paper) WHERE p.year IS NOT NULL
RETURN (p.year / 10) * 10 AS decade, count(*) AS citations
ORDER BY decade;


// 10. A first look at similarity: family pairs from DIFFERENT disciplines
//     that cite many of the same works (Jaccard on cited works).
//     This is the plain baseline the OWA similarity will be compared with.
MATCH (f1:Family)<-[:IN_FAMILY]-(:Manuscript)-[:CITES]->(p:Paper)
      <-[:CITES]-(:Manuscript)-[:IN_FAMILY]->(f2:Family)
WHERE f1.fid < f2.fid
WITH f1, f2, count(DISTINCT p) AS shared WHERE shared >= 3
MATCH (f1)-[:IN_DISCIPLINE]->(d1), (f2)-[:IN_DISCIPLINE]->(d2) WHERE d1 <> d2
RETURN f1.title AS family_1, d1.name AS discipline_1, f2.title AS family_2, d2.name AS discipline_2,
       shared, round(1000.0 * shared / (f1.n_refs + f2.n_refs - shared)) / 1000 AS jaccard
ORDER BY jaccard DESC LIMIT 15;


// 11. Circular support: pairs of manuscripts that each use a result of the other.
//     Not wrong by itself, but a reviewer should check that no proof goes in a circle.
MATCH (a:Manuscript)-[:DEPENDS_ON]->(b:Manuscript)-[:DEPENDS_ON]->(a)
WHERE a.mid < b.mid
RETURN a.title AS manuscript_1, b.title AS manuscript_2,
       a.lean_formalized AS lean_1, b.lean_formalized AS lean_2;
