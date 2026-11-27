"""Fuzzy trust in AI-generated mathematics, propagated along DEPENDS_ON with OWA.

Question: before the October 7 corrections, could the dependency graph alone have
pointed reviewers to the manuscripts that later needed proof repairs?

Model (all values in [0, 1]):
    base trust     b(m) = B_LEAN if a Lean proof checks m, else B_PLAIN
    trust          t(m) = b(m) * OWA_alpha( t(p) for p that m uses )
                   (a manuscript using nothing has t(m) = b(m))
    OWA_alpha      Yager's OWA with quantifier Q(r) = r**alpha:
                   alpha -> infinity: min ("all prerequisites must hold", orness 0)
                   alpha = 1: mean (orness 0.5);   alpha -> 0: max (orness 1)
    risk           1 - t(m)
Cycles (manuscripts that use each other) are collapsed: inside a cycle each member
only takes trust from prerequisites outside the cycle.

Usage:
    pip install networkx numpy matplotlib
    python trust_owa.py out results
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np

B_LEAN, B_PLAIN = 0.95, 0.80
SNAPSHOT_DAY = "2026-10-06"          # the release as it stood before the October 7 fixes

# The October 7 notice: "We have revised 14 other manuscripts with proof repairs ..."
# (history.md). Identified by family, plus the Birch-Swinnerton-Dyer paper.
REPAIRED_FAMILIES = {"232", "233", "034", "036", "056", "341", "342", "376"}
REPAIRED_EXTRA = ("Exact-Birch-Swinnerton-Dyer-Formula-from-Low-Selmer-Corank",)


# ---------- OWA ----------

def owa_weights(n: int, alpha: float) -> np.ndarray:
    r = np.arange(n + 1) / n
    q = r ** alpha
    return np.diff(q)                    # w_j = Q(j/n) - Q((j-1)/n), j = 1..n


def owa(values, alpha: float) -> float:
    a = np.sort(np.asarray(values, float))[::-1]        # largest first
    return float(owa_weights(len(a), alpha) @ a)


def orness(alpha: float, n: int = 10) -> float:
    w = owa_weights(n, alpha)
    return float(sum(w[j] * (n - 1 - j) for j in range(n)) / (n - 1))


# ---------- trust propagation ----------

def propagate(G: nx.DiGraph, base: dict, alpha: float) -> dict:
    """Trust for every node. Edges point from a manuscript to what it uses."""
    C = nx.condensation(G)
    member = C.graph["mapping"]
    t = {}
    for c in reversed(list(nx.topological_sort(C))):        # prerequisites first
        for m in C.nodes[c]["members"]:
            outside = [p for p in G.successors(m) if member[p] != c]
            t[m] = base[m] * (owa([t[p] for p in outside], alpha) if outside else 1.0)
    return t


def auc(scores: dict, positive: set, negative: set) -> float:
    """Probability that a random positive scores higher than a random negative."""
    pos = np.array([scores[m] for m in positive])
    neg = np.array([scores[m] for m in negative])
    greater = (pos[:, None] > neg[None, :]).mean()
    ties = (pos[:, None] == neg[None, :]).mean()
    return float(greater + 0.5 * ties)


def precision_at(scores: dict, positive: set, pool: set, k: int) -> float:
    top = sorted(pool, key=lambda m: -scores[m])[:k]
    return sum(m in positive for m in top) / k


# ---------- data ----------

def read(folder: Path, name: str) -> list[dict]:
    with open(folder / name, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def build_graph(ms: dict, edges: list[dict], keep: set) -> nx.DiGraph:
    G = nx.DiGraph()
    G.add_nodes_from(keep)
    for e in edges:
        if e["kind"] == "uses" and e["src"] in keep and e["dst"] in keep:
            G.add_edge(e["src"], e["dst"], n=int(e["n_cites"]))
    return G


def main(src: str, dst: str) -> None:
    src, out = Path(src), Path(dst)
    out.mkdir(parents=True, exist_ok=True)
    ms = {r["mid"]: r for r in read(src, "manuscripts.csv")}
    edges = read(src, "rel_depends_on.csv")
    prev = read(src, "rel_previous_version.csv")
    new_of = {r["old"]: r["new"] for r in prev}
    new_eds = set(new_of.values())
    lean = {m: r["lean_formalized"] == "True" for m, r in ms.items()}
    for old, new in new_of.items():                      # an edition shares its Lean status
        lean[old] = lean[old] or lean[new]
    base = {m: B_LEAN if lean[m] else B_PLAIN for m in ms}

    # ---------- 1. the release as of October 6 ----------
    snap = {m for m, r in ms.items()
            if r["status"] != "withdrawn" and m not in new_eds
            and r["date"] and r["date"] <= SNAPSHOT_DAY}
    G = build_graph(ms, edges, snap)
    repaired_new = {n for n in new_eds
                    if ms[n]["family"] in REPAIRED_FAMILIES or n.startswith(REPAIRED_EXTRA)}
    positive = {o for o, n in new_of.items() if n in repaired_new and o in snap}
    citation_only = {o for o, n in new_of.items() if n not in repaired_new}
    negative = snap - positive - citation_only       # citation-only updates left out
    pool = positive | negative

    unchecked_below = {m: sum(not lean[p] for p in nx.descendants(G, m)) for m in snap}
    C = nx.condensation(G)
    cdepth = {}
    for c in reversed(list(nx.topological_sort(C))):
        cdepth[c] = 1 + max((cdepth[d] for d in C.successors(c)), default=-1)
    depth = {m: cdepth[C.graph["mapping"][m]] for m in snap}

    rng = np.random.default_rng(0)
    baselines = {
        "not Lean-checked": {m: float(not lean[m]) for m in snap},
        "uses (AI prerequisites)": {m: G.out_degree(m) for m in snap},
        "used by": {m: G.in_degree(m) for m in snap},
        "depth of reasoning chain": depth,
        "unchecked prerequisites (all levels)": unchecked_below,
        "number of references": {m: int(ms[m]["n_references"]) for m in snap},
        "length of the LaTeX": {m: int(ms[m]["tex_chars"]) for m in snap},
        "random": {m: rng.random() for m in snap},
    }

    alphas = [0.25, 0.5, 1, 2, 4, 8, 32, 1000]
    sweep = []
    for a in alphas:
        risk = {m: 1 - v for m, v in propagate(G, base, a).items()}
        sweep.append({"alpha": a, "orness": round(orness(a), 3),
                      "auc": round(auc(risk, positive, negative), 3),
                      "p@20": precision_at(risk, positive, pool, 20),
                      "p@50": precision_at(risk, positive, pool, 50)})
    base_rows = [{"score": k, "auc": round(auc(s, positive, negative), 3),
                  "p@20": precision_at(s, positive, pool, 20),
                  "p@50": precision_at(s, positive, pool, 50)} for k, s in baselines.items()]

    # ---------- 2. today's release: what to review first ----------
    cur = {m for m, r in ms.items() if r["status"] == "current"}
    H = build_graph(ms, edges, cur)
    ALPHA = 2.0                                           # "most" prerequisites must hold
    t = propagate(H, base, ALPHA)
    review = []
    for m in cur:
        if lean[m] or H.in_degree(m) == 0:
            continue
        b2 = dict(base); b2[m] = 0.0                      # suppose m is wrong
        t2 = propagate(H, b2, ALPHA)
        loss = sum(t[x] - t2[x] for x in nx.ancestors(H, m))
        review.append({"mid": m, "title": ms[m]["title"], "family": ms[m]["family"],
                       "used_by_all_levels": len(nx.ancestors(H, m)),
                       "trust_lost_if_wrong": round(loss, 2), "own_trust": round(t[m], 3)})
    review.sort(key=lambda r: -r["trust_lost_if_wrong"])

    # ---------- write ----------
    with open(out / "trust.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["mid", "trust", "base", "lean", "uses", "used_by"])
        for m in sorted(cur):
            w.writerow([m, round(t[m], 4), base[m], lean[m], H.out_degree(m), H.in_degree(m)])
    with open(out / "review_priority.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(review[0]))
        w.writeheader(); w.writerows(review)
    summary = {"snapshot_manuscripts": len(snap), "snapshot_uses_edges": G.number_of_edges(),
               "positives_repaired": len(positive), "left_out_citation_only": len(citation_only),
               "negatives": len(negative), "base_rate": round(len(positive) / len(pool), 3),
               "owa_sweep": sweep, "baselines": base_rows,
               "repaired_ranks_at_alpha_2": sorted(
                   1 + sorted(pool, key=lambda m: -(1 - propagate(G, base, 2.0)[m])).index(p)
                   for p in positive),
               "review_top10": review[:10]}
    json.dump(summary, open(out / "summary.json", "w"), indent=2)

    # ---------- figure ----------
    fig, ax = plt.subplots(figsize=(7, 4))
    x = [r["orness"] for r in sweep]
    ax.plot(x, [r["auc"] for r in sweep], marker="o", lw=2, color="#1F4E79",
            label="fuzzy trust, OWA aggregation")
    for row, ls in [(base_rows[4], "--"), (base_rows[0], ":"), (base_rows[7], "-.")]:
        ax.axhline(row["auc"], ls=ls, color="0.45", lw=1.2)
        ax.text(1.0, row["auc"] + 0.01, row["score"], ha="right", fontsize=8, color="0.3")
    ax.set_xlabel("orness of the OWA (0 = all prerequisites must hold, 1 = any one is enough)")
    ax.set_ylabel("AUC: repaired vs other manuscripts")
    ax.set_ylim(0.4, 1.0)
    ax.set_title("Flagging the manuscripts repaired on Oct 7, from the Oct 6 graph", fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, fontsize=8, loc="lower left")
    fig.tight_layout()
    fig.savefig(out / "auc_vs_orness.png", dpi=200)
    print(json.dumps({k: v for k, v in summary.items() if k != "review_top10"}, indent=1))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python trust_owa.py <output folder of build_graph.py> <results folder>")
    main(sys.argv[1], sys.argv[2])
