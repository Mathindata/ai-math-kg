"""Load the CSV files made by build_graph.py into Neo4j.

Works with Neo4j Desktop, Community Server or Aura: it talks to the database
through the official Python driver, so no import folder is needed.

    pip install neo4j
    python load_neo4j.py out            # asks for the password
    python load_neo4j.py out --wipe     # delete everything in the database first

Settings (environment variables, all optional except the password):
    NEO4J_URI       default bolt://localhost:7687
    NEO4J_USER      default neo4j
    NEO4J_PASSWORD  asked for if not set
    NEO4J_DATABASE  default neo4j
"""
from __future__ import annotations

import argparse
import csv
import getpass
import os
import sys
import time
from pathlib import Path

BATCH = 2000

INT_FIELDS = {"n_theorems", "tex_chars", "n_references", "n_manuscripts", "n_cites",
              "n_cites_intro", "order"}
BOOL_FIELDS = {"lean_formalized", "lean_doc"}

SCHEMA = [
    "CREATE CONSTRAINT manuscript_id IF NOT EXISTS FOR (n:Manuscript) REQUIRE n.mid IS UNIQUE",
    "CREATE CONSTRAINT family_id IF NOT EXISTS FOR (n:Family) REQUIRE n.fid IS UNIQUE",
    "CREATE CONSTRAINT discipline_name IF NOT EXISTS FOR (n:Discipline) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT paper_id IF NOT EXISTS FOR (n:Paper) REQUIRE n.pid IS UNIQUE",
    "CREATE CONSTRAINT author_id IF NOT EXISTS FOR (n:Author) REQUIRE n.aid IS UNIQUE",
    "CREATE CONSTRAINT category_code IF NOT EXISTS FOR (n:ArxivCategory) REQUIRE n.code IS UNIQUE",
    "CREATE INDEX manuscript_status IF NOT EXISTS FOR (n:Manuscript) ON (n.status)",
    "CREATE INDEX paper_year IF NOT EXISTS FOR (n:Paper) ON (n.year)",
]

# (csv file, Cypher run once per batch of rows)
STEPS = [
    ("disciplines.csv", """
        UNWIND $rows AS r
        MERGE (d:Discipline {name: r.name}) SET d.order = r.order"""),
    ("families.csv", """
        UNWIND $rows AS r
        MERGE (f:Family {fid: r.fid})
        SET f.title = r.title, f.description = r.description,
            f.n_manuscripts = r.n_manuscripts, f.lean_doc = r.lean_doc
        WITH f, r WHERE r.discipline <> ''
        MATCH (d:Discipline {name: r.discipline})
        MERGE (f)-[:IN_DISCIPLINE]->(d)"""),
    ("manuscripts.csv", """
        UNWIND $rows AS r
        MERGE (m:Manuscript {mid: r.mid})
        SET m.title = r.title, m.date = CASE r.date WHEN '' THEN null ELSE date(r.date) END,
            m.status = r.status, m.lean_formalized = r.lean_formalized,
            m.withdrawn_on = r.withdrawn_on, m.n_theorems = r.n_theorems,
            m.tex_chars = r.tex_chars, m.n_references = r.n_references,
            m.abstract = r.abstract
        WITH m, r WHERE r.family <> ''
        MATCH (f:Family {fid: r.family})
        MERGE (m)-[:IN_FAMILY]->(f)"""),
    ("papers.csv", """
        UNWIND $rows AS r
        MERGE (p:Paper {pid: r.pid})
        SET p.title = r.title, p.year = toInteger(r.year), p.doi = r.doi,
            p.arxiv = r.arxiv, p.venue = r.venue, p.n_manuscripts = r.n_manuscripts,
            p.citation = r.citation
        WITH p, r WHERE r.arxiv_class <> ''
        MERGE (c:ArxivCategory {code: r.arxiv_class})
        MERGE (p)-[:IN_CATEGORY]->(c)"""),
    ("authors.csv", """
        UNWIND $rows AS r
        MERGE (a:Author {aid: r.aid}) SET a.name = r.name"""),
    ("rel_cites.csv", """
        UNWIND $rows AS r
        MATCH (m:Manuscript {mid: r.mid}) MATCH (p:Paper {pid: r.pid})
        MERGE (m)-[c:CITES]->(p)
        SET c.key = r.key, c.n_cites = r.n_cites, c.n_cites_intro = r.n_cites_intro"""),
    ("rel_authored.csv", """
        UNWIND $rows AS r
        MATCH (a:Author {aid: r.aid}) MATCH (p:Paper {pid: r.pid})
        MERGE (a)-[:AUTHORED]->(p)"""),
    # kind = uses -> DEPENDS_ON (result used in the body); mentions -> MENTIONS (intro only)
    ("rel_depends_on.csv", """
        UNWIND [r IN $rows WHERE r.kind = 'uses'] AS r
        MATCH (a:Manuscript {mid: r.src}) MATCH (b:Manuscript {mid: r.dst})
        MERGE (a)-[d:DEPENDS_ON]->(b)
        SET d.n_cites = r.n_cites, d.n_cites_intro = r.n_cites_intro, d.source = r.source"""),
    ("rel_depends_on.csv", """
        UNWIND [r IN $rows WHERE r.kind = 'mentions'] AS r
        MATCH (a:Manuscript {mid: r.src}) MATCH (b:Manuscript {mid: r.dst})
        MERGE (a)-[d:MENTIONS]->(b)
        SET d.n_cites = r.n_cites, d.source = r.source"""),
    ("rel_previous_version.csv", """
        UNWIND $rows AS r
        MATCH (a:Manuscript {mid: r.new}) MATCH (b:Manuscript {mid: r.old})
        MERGE (a)-[:PREVIOUS_VERSION]->(b)"""),
    ("rel_subsumed_by.csv", """
        UNWIND $rows AS r
        MATCH (a:Manuscript {mid: r.old}) MATCH (b:Manuscript {mid: r.new})
        MERGE (a)-[:SUBSUMED_BY]->(b)"""),
    ("rel_related_family.csv", """
        UNWIND $rows AS r
        MATCH (a:Family {fid: r.a}) MATCH (b:Family {fid: r.b})
        MERGE (a)-[:RELATED_TO]->(b)"""),
]

# extra labels so queries read naturally: (:Formalized), (:Withdrawn)
FINISH = [
    "MATCH (m:Manuscript {lean_formalized: true}) SET m:Formalized",
    "MATCH (m:Manuscript {status: 'withdrawn'}) SET m:Withdrawn",
    # number of distinct cited works per family (used by similarity queries)
    """MATCH (f:Family) OPTIONAL MATCH (f)<-[:IN_FAMILY]-(:Manuscript)-[:CITES]->(p:Paper)
       WITH f, count(DISTINCT p) AS n SET f.n_refs = n""",
]


def read_rows(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            for k, v in r.items():
                if k in INT_FIELDS:
                    r[k] = int(v) if v not in ("", None) else 0
                elif k in BOOL_FIELDS:
                    r[k] = v == "True"
            rows.append(r)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("folder", help="output folder of build_graph.py")
    ap.add_argument("--wipe", action="store_true", help="delete all nodes in the database first")
    args = ap.parse_args()

    try:
        from neo4j import GraphDatabase
    except ImportError:
        sys.exit("The neo4j package is missing. Run:  pip install neo4j")

    folder = Path(args.folder)
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    db = os.environ.get("NEO4J_DATABASE", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD") or getpass.getpass(f"Password for {user} at {uri}: ")

    driver = GraphDatabase.driver(uri, auth=(user, password))
    driver.verify_connectivity()
    print(f"connected to {uri}, database '{db}'")

    with driver.session(database=db) as s:
        if args.wipe:
            while True:   # small batches, works on every Neo4j version
                left = s.run("MATCH (n) WITH n LIMIT 5000 DETACH DELETE n RETURN count(*) AS c").single()["c"]
                if left == 0:
                    break
            print("database emptied")
        for q in SCHEMA:
            s.run(q).consume()
        s.run("CALL db.awaitIndexes(300)").consume()

        for name, query in STEPS:
            rows = read_rows(folder / name)
            t = time.time()
            for i in range(0, len(rows), BATCH):
                s.execute_write(lambda tx, b=rows[i:i + BATCH]: tx.run(query, rows=b).consume())
            print(f"  {name:28s} {len(rows):7d} rows  {time.time() - t:5.1f}s")
        for q in FINISH:
            s.run(q).consume()

        print("\nin the database now:")
        for rec in s.run("MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) AS n ORDER BY n DESC"):
            print(f"  :{rec['l']:15s} {rec['n']}")
        for rec in s.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS n ORDER BY n DESC"):
            print(f"  -[:{rec['t']}]-> {rec['n']}")
    driver.close()


if __name__ == "__main__":
    main()
