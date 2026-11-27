"""Write the analysis results back into the Neo4j graph.

    python add_results_to_neo4j.py results

Adds:
  Manuscript.trust                 fuzzy trust (trust_owa.py)
  Manuscript.trust_lost_if_wrong   how much trust the release loses if this result is wrong
  (Manuscript)-[:SIMILAR_TO {score, same_family}]->(Manuscript)   (similarity.py)

Same connection settings as load_neo4j.py (NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD).
"""
from __future__ import annotations

import csv
import getpass
import os
import sys
from pathlib import Path

STEPS = [
    ("trust.csv", """
        UNWIND $rows AS r MATCH (m:Manuscript {mid: r.mid})
        SET m.trust = toFloat(r.trust)"""),
    ("review_priority.csv", """
        UNWIND $rows AS r MATCH (m:Manuscript {mid: r.mid})
        SET m.trust_lost_if_wrong = toFloat(r.trust_lost_if_wrong),
            m.used_by_all_levels = toInteger(r.used_by_all_levels)"""),
    ("rel_similar_to.csv", """
        UNWIND $rows AS r
        MATCH (a:Manuscript {mid: r.a}) MATCH (b:Manuscript {mid: r.b})
        MERGE (a)-[s:SIMILAR_TO]->(b)
        SET s.score = toFloat(r.score), s.same_family = (r.same_family = 'True')"""),
]


def main(folder: str) -> None:
    from neo4j import GraphDatabase
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    db = os.environ.get("NEO4J_DATABASE", "neo4j")
    pw = os.environ.get("NEO4J_PASSWORD") or getpass.getpass(f"Password for {user} at {uri}: ")
    driver = GraphDatabase.driver(uri, auth=(user, pw))
    driver.verify_connectivity()
    with driver.session(database=db) as s:
        s.run("MATCH ()-[r:SIMILAR_TO]->() DELETE r").consume()       # replace old results
        for name, q in STEPS:
            path = Path(folder) / name
            if not path.exists():
                print(f"  {name:22s} not found, skipped")
                continue
            rows = list(csv.DictReader(open(path, encoding="utf-8", newline="")))
            for i in range(0, len(rows), 2000):
                s.execute_write(lambda tx, b=rows[i:i + 2000]: tx.run(q, rows=b).consume())
            print(f"  {name:22s} {len(rows):6d} rows")
    driver.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python add_results_to_neo4j.py <results folder>")
    main(sys.argv[1])
