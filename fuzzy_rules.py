"""Fuzzy rules in words for the OpenAI math release, and how much each modelling choice matters.

THE RULES (each sentence is computed exactly as written)

  R1  A result is RISKY if it has no Lean proof, and either it is LONG,
      or MOST of the results it RELIES ON HEAVILY are risky.
  R2  A result is a CRITICAL FOUNDATION if MANY results rest on it and it is risky.
  R3  Two results SUPPORT EACH OTHER SUSPICIOUSLY if each relies heavily on the other
      and neither has a Lean proof.

THE WORDS (fuzzy sets; breakpoints come from the data, so they can be defended)

  long(m)        total length of the proofs (text inside proof environments),
                 rising from the 50th to the 90th percentile of the release
  heavily(m, p)  how strongly m relies on p: min(1, n / 5), n = citations of p inside proofs
  many(m)        results resting on m (all levels), rising from 3 to 6: the 75th and 95th
                 percentiles among results that anything rests on
  most           a quantifier: a fuzzy set on proportions (Zadeh) or OWA weights (Yager)
  and / or       a T-norm and its dual T-conorm

Every choice above has variants. The script scores all manuscripts with a base setting,
changes ONE choice at a time, and reports how much the results move.

Usage:  python fuzzy_rules.py out results
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import networkx as nx
import numpy as np

SNAPSHOT_DAY = "2026-10-06"
REPAIRED_FAMILIES = {"232", "233", "034", "036", "056", "341", "342", "376"}
REPAIRED_EXTRA = ("Exact-Birch-Swinnerton-Dyer-Formula-from-Low-Selmer-Corank",)


# ---------------- building blocks ----------------

def ramp(x, a, b):
    """0 below a, 1 above b, linear between (b < a gives a falling ramp)."""
    x = np.asarray(x, float)
    if b > a:
        return np.clip((x - a) / (b - a), 0, 1)
    return np.clip((a - x) / (a - b), 0, 1)


def sigmoid(x, centre, width):
    return 1 / (1 + np.exp(-(np.asarray(x, float) - centre) / width))


TNORMS = {   # "and" with its dual "or"
    "min/max":            (lambda a, b: min(a, b),             lambda a, b: max(a, b)),
    "product/prob-sum":   (lambda a, b: a * b,                 lambda a, b: a + b - a * b),
    "Lukasiewicz":        (lambda a, b: max(0.0, a + b - 1),   lambda a, b: min(1.0, a + b)),
}


def q_zadeh(lo, hi):
    """'most' as a fuzzy set on the proportion r: 0 below lo, 1 above hi."""
    return lambda r: float(ramp(r, lo, hi))


def q_power(alpha):
    return lambda r: float(r) ** alpha


def most_zadeh(values, importance, Q):
    """Zadeh: truth of 'most of the items are X' = Q(weighted share of X)."""
    s = np.sum(importance)
    return Q(np.sum(np.minimum(values, 1) * importance) / s) if s > 0 else 0.0


def most_yager(values, importance, Q):
    """Yager: importance-weighted OWA. Sort by value, weight j = Q(S_j) - Q(S_{j-1}),
    where S_j is the share of importance held by the j largest values."""
    s = np.sum(importance)
    if s <= 0:
        return 0.0
    order = np.argsort(-np.asarray(values))
    v, u = np.asarray(values)[order], np.asarray(importance)[order] / s
    cum = np.concatenate([[0.0], np.cumsum(u)])
    w = np.array([Q(cum[j + 1]) - Q(cum[j]) for j in range(len(v))])
    return float(w @ v)


# ---------------- the settings ----------------

BASE = {"measure": "proof text only", "long": "p50-p90 linear", "heavily": "n/5",
        "most": "Zadeh, ramp 0.3-0.8", "logic": "min/max", "many": "3-6"}

VARIANTS = {   # one choice changed at a time
    "measure": ["whole LaTeX", "average proof length"],
    "long":    ["p40-p80 linear (lenient)", "p60-p95 linear (strict)", "sigmoid around p75"],
    "heavily": ["crisp (any use counts fully)"],
    "most":    ["Zadeh, ramp 0.5-0.9 (strict)", "Zadeh, ramp 0.2-0.6 (lenient)",
                "Yager OWA, same ramp 0.3-0.8", "Yager OWA, Q(r)=r^2", "Yager OWA, Q(r)=r^3",
                "average (Q(r)=r)", "all (minimum)", "some (maximum)"],
    "logic":   ["product/prob-sum", "Lukasiewicz"],
    "many":    ["2-10 (wider)", "4-20 (strict)"],
}


def make_long(setting, lengths, ref):
    p = lambda q: np.percentile(ref, q)
    if setting.startswith("p50"):
        return ramp(lengths, p(50), p(90))
    if setting.startswith("p40"):
        return ramp(lengths, p(40), p(80))
    if setting.startswith("p60"):
        return ramp(lengths, p(60), p(95))
    return sigmoid(lengths, p(75), (p(90) - p(60)) / 4)


def make_most(setting):
    if setting.startswith("Zadeh"):
        lo, hi = (0.3, 0.8) if "0.3" in setting else (0.5, 0.9) if "0.5" in setting else (0.2, 0.6)
        Q = q_zadeh(lo, hi)
        return lambda v, u: most_zadeh(v, u, Q)
    if setting.startswith("Yager OWA, same"):
        Q = q_zadeh(0.3, 0.8)
        return lambda v, u: most_yager(v, u, Q)
    if "r^2" in setting:
        return lambda v, u: most_yager(v, u, q_power(2))
    if "r^3" in setting:
        return lambda v, u: most_yager(v, u, q_power(3))
    if setting.startswith("average"):
        return lambda v, u: most_yager(v, u, q_power(1))
    if setting.startswith("all"):
        return lambda v, u: float(np.min(v)) if len(v) else 0.0
    return lambda v, u: float(np.max(v)) if len(v) else 0.0


# ---------------- scoring ----------------

def size_of(info_m, measure):
    """What 'long' is measured on."""
    if measure == "whole LaTeX":
        return info_m["chars"]
    if measure == "proof text only":
        return info_m["proof_chars"]
    return info_m["proof_chars"] / max(info_m["n_proofs"], 1)


def score(G, info, setting, ref_info):
    """Apply R1 and R2 over the dependency graph G (edge m -> p: m relies on p)."""
    nodes = list(G.nodes)
    lengths = np.array([size_of(info[m], setting["measure"]) for m in nodes])
    ref_lengths = np.array([size_of(i, setting["measure"]) for i in ref_info])
    long_ = dict(zip(nodes, make_long(setting["long"], lengths, ref_lengths)))
    if setting["heavily"] == "n/5":
        heavy = {(a, b): min(1.0, d["n"] / 5) for a, b, d in G.edges(data=True)}
    else:
        heavy = {(a, b): 1.0 for a, b in G.edges}
    AND, OR = TNORMS[setting["logic"]]
    most = make_most(setting["most"])

    risky, inherited = {}, {}
    C = nx.condensation(G)
    for c in reversed(list(nx.topological_sort(C))):           # what is relied on comes first
        members = C.nodes[c]["members"]
        for m in members:
            risky[m] = 0.0 if info[m]["lean"] else float(long_[m])
        for _ in range(30 if len(members) > 1 else 1):         # a cycle: iterate to a fixed point
            for m in members:
                ps = list(G.successors(m))
                inh = most(np.array([risky[p] for p in ps]),
                           np.array([heavy[(m, p)] for p in ps])) if ps else 0.0
                inherited[m] = inh
                risky[m] = 0.0 if info[m]["lean"] else OR(float(long_[m]), inh)

    lo, hi = {"3-6": (3, 6), "2-10 (wider)": (2, 10), "4-20 (strict)": (4, 20)}[setting["many"]]
    rests = {m: len(nx.ancestors(G, m)) for m in nodes}
    many = {m: float(ramp(rests[m], lo, hi)) for m in nodes}
    critical = {m: AND(many[m], risky[m]) for m in nodes}
    return {"long": long_, "inherited": inherited, "risky": risky, "many": many,
            "critical": critical, "rests": rests, "heavy": heavy}


def suspicious_pairs(G, info, heavy, AND):
    out = []
    for a, b in G.edges:
        if a < b and G.has_edge(b, a) and not info[a]["lean"] and not info[b]["lean"]:
            out.append((AND(heavy[(a, b)], heavy[(b, a)]), a, b))
    return sorted(out, reverse=True)


# ---------------- linguistic summaries (Yager) ----------------

Q_MOST, Q_FEW = q_zadeh(0.3, 0.8), q_zadeh(0.3, 0.1)       # few: 1 below 10%, 0 above 30%


def summary(Q, mu_S, mu_R=None):
    """Truth of 'Q (R) items are S' = Q( sum min(R, S) / sum R )."""
    mu_S = np.asarray(mu_S, float)
    mu_R = np.ones_like(mu_S) if mu_R is None else np.asarray(mu_R, float)
    return Q(np.minimum(mu_R, mu_S).sum() / mu_R.sum()) if mu_R.sum() else 0.0


def spearman(a, b):
    """Rank correlation with tied values sharing their average rank."""
    from scipy.stats import spearmanr
    return float(spearmanr(a, b).correlation)


def auc(scores, pos, neg):
    p, n = np.array([scores[m] for m in pos]), np.array([scores[m] for m in neg])
    return float((p[:, None] > n[None, :]).mean() + 0.5 * (p[:, None] == n[None, :]).mean())


# ---------------- data ----------------

def read(folder, name):
    with open(Path(folder) / name, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def graph(edges, keep):
    G = nx.DiGraph()
    G.add_nodes_from(keep)
    for e in edges:
        if e["kind"] == "uses" and e["src"] in keep and e["dst"] in keep:
            body = int(e["n_cites"]) - int(e["n_cites_intro"])
            G.add_edge(e["src"], e["dst"], n=max(body, 1))
    return G


def main(src, dst):
    out = Path(dst) / "fuzzy"
    out.mkdir(parents=True, exist_ok=True)
    ms = {r["mid"]: r for r in read(src, "manuscripts.csv")}
    fam = {r["fid"]: r for r in read(src, "families.csv")}
    edges = read(src, "rel_depends_on.csv")
    prev = read(src, "rel_previous_version.csv")
    new_of = {r["old"]: r["new"] for r in prev}
    new_eds = set(new_of.values())
    info = {m: {"chars": int(r["tex_chars"]), "lean": r["lean_formalized"] == "True",
                "proof_chars": int(r.get("proof_chars") or 0), "n_proofs": int(r.get("n_proofs") or 0),
                "title": r["title"]} for m, r in ms.items()}
    for old, new in new_of.items():
        info[old]["lean"] = info[old]["lean"] or info[new]["lean"]

    cur = sorted(m for m, r in ms.items() if r["status"] == "current")
    ref = np.array([info[m]["chars"] for m in cur])
    ref_info = [info[m] for m in cur]                   # 'long' is judged against today's release
    G = graph(edges, cur)
    snap = {m for m, r in ms.items() if r["status"] != "withdrawn" and m not in new_eds
            and r["date"] and r["date"] <= SNAPSHOT_DAY}
    Gs = graph(edges, snap)
    repaired_new = {n for n in new_eds if ms[n]["family"] in REPAIRED_FAMILIES
                    or n.startswith(REPAIRED_EXTRA)}
    pos = {o for o, n in new_of.items() if n in repaired_new}
    neg = snap - pos - {o for o, n in new_of.items() if n not in repaired_new}
    relies = [m for m in cur if G.out_degree(m) > 0]

    settings = [("base", "", dict(BASE))]
    for k, vals in VARIANTS.items():
        for v in vals:
            s = dict(BASE); s[k] = v
            settings.append((k, v, s))

    base = score(G, info, BASE, ref_info)
    order = sorted(cur)
    rows = []
    for k, v, s in settings:
        r = score(G, info, s, ref_info)
        rs = score(Gs, info, s, ref_info)
        set_b = {m for m in cur if base["risky"][m] >= 0.5}     # 112 results tie at 1.0, so a
        set_v = {m for m in cur if r["risky"][m] >= 0.5}        # top-20 list would be arbitrary
        crit_b = {m for m in cur if base["critical"][m] >= 0.25}  # ties again: compare sets,
        crit_v = {m for m in cur if r["critical"][m] >= 0.25}     # not a top-10 list
        rows.append({
            "choice": k, "variant": v or "base setting",
            "rank_agreement_all": round(spearman([base["risky"][m] for m in order],
                                                 [r["risky"][m] for m in order]), 3),
            "rank_agreement_reliers": round(spearman([base["risky"][m] for m in relies],
                                                     [r["risky"][m] for m in relies]), 3),
            "same_risky_set": round(len(set_b & set_v) / len(set_b | set_v), 3),
            "same_critical_set": round(len(crit_b & crit_v) / len(crit_b | crit_v), 3),
            "n_risky_over_half": sum(r["risky"][m] >= 0.5 for m in cur),
            "n_critical_over_half": sum(r["critical"][m] >= 0.5 for m in cur),
            "T(most repaired were risky)": round(summary(Q_MOST, [rs["risky"][m] for m in pos]), 2),
            "T(few others were risky)": round(summary(Q_FEW, [rs["risky"][m] for m in neg]), 2),
            "auc_repairs": round(auc(rs["risky"], pos, neg), 3),
        })
    with open(out / "sensitivity.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

    # ---- per-manuscript scores with the reason in words (base setting) ----
    AND = TNORMS[BASE["logic"]][0]
    disc = {m: fam[ms[m]["family"]]["discipline"] if ms[m]["family"] in fam else "" for m in ms}
    pref = np.array([info[m]["proof_chars"] for m in cur])
    pct = {m: round(100 * (pref < info[m]["proof_chars"]).mean()) for m in cur}

    def reason(m):
        if info[m]["lean"]:
            return "Lean proof, so not risky by R1"
        parts = [f"no Lean proof; long {base['long'][m]:.2f} "
                 f"({info[m]['proof_chars'] // 1000}k characters of proofs, more than {pct[m]}% of manuscripts)"]
        ps = sorted(G.successors(m), key=lambda p: -base["risky"][p])
        if ps:
            worst = ps[0]
            parts.append(f"relies on {len(ps)} result(s); 'most are risky' = {base['inherited'][m]:.2f}"
                         f" (riskiest: {info[worst]['title'][:60]}, {base['risky'][worst]:.2f})")
        if base["rests"][m]:
            parts.append(f"{base['rests'][m]} results rest on it (many = {base['many'][m]:.2f})")
        return "; ".join(parts)

    with open(out / "scores.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["mid", "title", "discipline", "lean", "long", "most_relied_on_risky",
                    "risky", "rests_on_it", "many", "critical", "reason"])
        for m in sorted(cur, key=lambda m: -base["risky"][m]):
            w.writerow([m, info[m]["title"], disc[m], info[m]["lean"], round(float(base["long"][m]), 3),
                        round(base["inherited"].get(m, 0), 3), round(base["risky"][m], 3),
                        base["rests"][m], round(base["many"][m], 3), round(base["critical"][m], 3),
                        reason(m)])

    pairs = suspicious_pairs(G, info, base["heavy"], AND)
    with open(out / "suspicious_pairs.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["degree", "a", "b", "a_uses_b_cites", "b_uses_a_cites"])
        for d, a, b in pairs:
            w.writerow([round(d, 2), info[a]["title"], info[b]["title"],
                        G[a][b]["n"], G[b][a]["n"]])

    # ---- descriptive linguistic summaries on today's release ----
    risky = np.array([base["risky"][m] for m in cur]); crit = np.array([base["critical"][m] for m in cur])
    lean = np.array([float(info[m]["lean"]) for m in cur])
    longv = np.array([base["long"][m] for m in cur])
    rel = np.array([float(G.out_degree(m) > 0) for m in cur])
    inh = np.array([base["inherited"].get(m, 0) for m in cur])
    lean_relying_on_risky = [m for m in cur if info[m]["lean"] and base["inherited"].get(m, 0) >= 0.5]
    summaries = {
        "Most results are risky": summary(Q_MOST, risky),
        "Few results are critical foundations": summary(Q_FEW, crit),
        "Most long results have no Lean proof": summary(Q_MOST, 1 - lean, longv),
        "Most results that rely on others rely mostly on risky ones": summary(Q_MOST, inh, rel),
        "Most repaired results (Oct 7) were long": summary(Q_MOST, [float(make_long(BASE['long'], [info[m]['proof_chars']], np.array([i['proof_chars'] for i in ref_info]))[0]) for m in pos]),
        "Lean-checked results that rely mostly on risky ones (count)": len(lean_relying_on_risky),
    }
    json.dump({"summaries": {k: round(float(v), 2) for k, v in summaries.items()},
               "lean_checked_relying_on_risky": [info[m]["title"] for m in lean_relying_on_risky],
               "n_current": len(cur), "n_relying_on_others": len(relies),
               "repaired_in_snapshot": len(pos), "others_in_snapshot": len(neg)},
              open(out / "summaries.json", "w"), indent=2)

    print(f"{len(cur)} results, {len(relies)} rely on others; snapshot: {len(pos)} repaired vs {len(neg)}")
    for r in rows:
        print("  " + " | ".join(str(x) for x in r.values()))
    print(json.dumps({k: round(float(v), 2) for k, v in summaries.items()}, indent=1))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python fuzzy_rules.py <output folder of build_graph.py> <results folder>")
    main(sys.argv[1], sys.argv[2])
