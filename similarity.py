"""OWA concept similarity of manuscripts in the knowledge graph.

Follows the structure of Wang, Yager & Reformat, "Similarity of Concepts in Weighted
Knowledge Graphs" (IPMU 2024):
  1. A concept (manuscript) is described by its weighted relations: (CITES, work, w).
     w is the degree to which the work matters to the manuscript, from how often
     it is cited in the text:  w = 1 - exp(-(n + 1) / 2)   (n = 0 -> 0.39, n = 3 -> 0.86).
  2. A shared feature counts through a T-norm of the two weights (min or product).
  3. Levels: when A and B cite DIFFERENT works through the same relation, the two
     works are compared one level down (shared authors, same arXiv area), and the
     match is discounted by lambda.
  4. The per-feature matches are aggregated with an OWA operator. Its orness moves
     the meaning from "all of A's references are found in B" (orness 0) through
     "on average" (0.5) to "some reference is found in B" (orness 1).

    coverage of feature f of A by B:  c_f = max_g  sigma(f, g) * T(w_f, w_g) / w_f
    inclusion(A, B) = OWA(c_f over f in A);   S(A, B) = (inclusion(A,B) + inclusion(B,A)) / 2

Ground truth: OpenAI grouped the manuscripts into families (one research result each).
A good similarity puts members of the same family near each other. The OWA settings
are chosen on half of the families and tested on the other half.

Usage:
    pip install numpy scipy matplotlib
    python similarity.py out results
"""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import sparse

ALPHAS = [0.1, 0.25, 0.5, 1, 2, 4, 10, 1000]      # quantifier Q(r) = r**alpha
LAMBDAS = [0.0, 0.25, 0.5, 1.0]                   # weight of the second level
TNORM = "min"                                     # product gives almost the same numbers
TOP_K = 5                                         # neighbours written to the graph


def read(folder: Path, name: str) -> list[dict]:
    with open(folder / name, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def owa_weights(n: int, alpha: float) -> np.ndarray:
    return np.diff((np.arange(n + 1) / n) ** alpha)


def orness(alpha: float, n: int = 20) -> float:
    w = owa_weights(n, alpha)
    return float(sum(w[j] * (n - 1 - j) for j in range(n)) / (n - 1))


# ---------- data ----------

def load(src: Path):
    ms = {r["mid"]: r for r in read(src, "manuscripts.csv")}
    cur = sorted(m for m, r in ms.items() if r["status"] == "current")
    fam = {r["fid"]: r for r in read(src, "families.csv")}
    papers = [r["pid"] for r in read(src, "papers.csv")]
    pidx = {p: i for i, p in enumerate(papers)}
    cat = {r["pid"]: r["arxiv_class"] for r in read(src, "papers.csv")}
    refs = defaultdict(dict)
    for r in read(src, "rel_cites.csv"):
        if r["mid"] in ms and ms[r["mid"]]["status"] == "current":
            n = int(r["n_cites"])
            refs[r["mid"]][pidx[r["pid"]]] = 1 - math.exp(-(n + 1) / 2)
    authors = defaultdict(set)
    for r in read(src, "rel_authored.csv"):
        authors[pidx[r["pid"]]].add(r["aid"])
    return ms, cur, fam, papers, pidx, cat, refs, authors


def level_two(papers, pidx, cat, authors) -> sparse.csr_matrix:
    """Similarity of two DIFFERENT cited works: Jaccard of their authors, or 0.3 if they
    only share an arXiv area. Identity on the diagonal."""
    n = len(papers)
    aids = sorted({a for s in authors.values() for a in s})
    aidx = {a: i for i, a in enumerate(aids)}
    rows, cols = [], []
    for p, s in authors.items():
        for a in s:
            rows.append(p); cols.append(aidx[a])
    PA = sparse.csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(n, len(aids)))
    shared = (PA @ PA.T).tocoo()
    size = np.asarray(PA.sum(1)).ravel()
    jac = shared.data / (size[shared.row] + size[shared.col] - shared.data)
    J = sparse.csr_matrix((jac, (shared.row, shared.col)), shape=(n, n))
    cats = sorted({c for c in cat.values() if c})
    cidx = {c: i for i, c in enumerate(cats)}
    r2 = [pidx[p] for p, c in cat.items() if c]
    c2 = [cidx[c] for p, c in cat.items() if c]
    PC = sparse.csr_matrix((np.ones(len(r2)), (r2, c2)), shape=(n, len(cats)))
    C = (PC @ PC.T) * 0.3
    S = J.maximum(C).tolil()
    S.setdiag(0)
    return S.tocsr()


# ---------- similarity ----------

def coverage_vectors(cur, refs, L2, lam, tnorm):
    """For every ordered pair (A, B): the sorted coverage of A's features by B."""
    idx = {m: (np.array(list(refs[m])), np.array(list(refs[m].values()))) for m in cur}
    out = {}
    for a in cur:
        ra, wa = idx[a]
        if not len(ra):
            continue
        rowsA = L2[ra].toarray() * lam if lam > 0 else None
        for b in cur:
            if a == b:
                continue
            rb, wb = idx[b]
            if not len(rb):
                continue
            sig = (ra[:, None] == rb[None, :]).astype(float)
            if rowsA is not None:
                sig = np.maximum(sig, rowsA[:, rb])
            if not sig.any():
                out[(a, b)] = None                          # nothing in common at all
                continue
            t = np.minimum(wa[:, None], wb[None, :]) if tnorm == "min" else wa[:, None] * wb[None, :]
            c = (sig * t).max(1) / wa
            out[(a, b)] = np.sort(c)[::-1]
    return out


def similarity_matrix(cur, cov, alpha):
    n = len(cur)
    pos = {m: i for i, m in enumerate(cur)}
    inc = np.zeros((n, n))
    wcache = {}
    for (a, b), c in cov.items():
        if c is None:
            continue
        k = len(c)
        if k not in wcache:
            wcache[k] = owa_weights(k, alpha)
        inc[pos[a], pos[b]] = wcache[k] @ c
    return (inc + inc.T) / 2


def flat_bag(cur, refs, papers, cat, authors):
    """Same information as the two levels, but as one flat bag of features:
    cited works, their authors and their arXiv areas (TF-IDF cosine)."""
    fid, rows, cols, vals = {}, [], [], []
    for i, m in enumerate(cur):
        bag = defaultdict(float)
        for p, w in refs[m].items():
            bag["p" + str(p)] += w
            for a in authors.get(p, ()):
                bag["a" + a] += w
            if cat[papers[p]]:
                bag["c" + cat[papers[p]]] += w
        for k, v in bag.items():
            rows.append(i); cols.append(fid.setdefault(k, len(fid))); vals.append(v)
    X = sparse.csr_matrix((vals, (rows, cols)), shape=(len(cur), len(fid)))
    df = np.asarray((X > 0).sum(0)).ravel()
    T = X.multiply(np.log((1 + len(cur)) / (1 + df)) + 1).tocsr()
    nrm = np.sqrt(np.asarray(T.multiply(T).sum(1)).ravel()) + 1e-12
    S = (T @ T.T).toarray() / nrm[:, None] / nrm[None, :]
    np.fill_diagonal(S, 0)
    return S


def baselines(cur, refs, n_papers):
    pos = {m: i for i, m in enumerate(cur)}
    rows, cols, vals = [], [], []
    for m in cur:
        for p, w in refs[m].items():
            rows.append(pos[m]); cols.append(p); vals.append(w)
    W = sparse.csr_matrix((vals, (rows, cols)), shape=(len(cur), n_papers))
    X = (W > 0).astype(float)
    inter = (X @ X.T).toarray()
    size = np.asarray(X.sum(1)).ravel()
    jac = inter / np.maximum(size[:, None] + size[None, :] - inter, 1)
    df = np.asarray(X.sum(0)).ravel()
    idf = np.log((1 + len(cur)) / (1 + df)) + 1
    T = X.multiply(idf).tocsr()
    nrm = np.sqrt(np.asarray(T.multiply(T).sum(1)).ravel()) + 1e-12
    cos = (T @ T.T).toarray() / nrm[:, None] / nrm[None, :]
    aa_w = 1 / np.log(np.maximum(df, 2))                        # Adamic-Adar
    aa = (X.multiply(aa_w) @ X.T).toarray()
    for M in (jac, cos, aa):
        np.fill_diagonal(M, 0)
    return {"Jaccard (shared references)": jac, "TF-IDF cosine": cos, "Adamic-Adar": aa}


# ---------- evaluation ----------

def evaluate(S, cur, family, queries):
    """Mean average precision and hit@1 for finding same-family manuscripts."""
    pos = {m: i for i, m in enumerate(cur)}
    fam_arr = np.array([family[m] for m in cur])
    aps, hits = [], []
    rng = np.random.default_rng(0)
    for q in queries:
        i = pos[q]
        s = S[i].copy()
        s[i] = -np.inf
        s = s + rng.random(len(s)) * 1e-9                          # break ties fairly
        order = np.argsort(-s)
        rel = (fam_arr[order] == family[q]) & (order != i)
        k = np.flatnonzero(rel)
        if not len(k):
            continue
        aps.append(np.mean((np.arange(len(k)) + 1) / (k + 1)))
        hits.append(float(rel[0]))
    return float(np.mean(aps)), float(np.mean(hits)), np.array(aps)


def discipline_from_other_families(S, cur, family, disc, idx, k=5):
    """For each manuscript, hide its own family, take the k most similar manuscripts
    and vote for a discipline. 1 if the vote is right, else 0."""
    F = np.array([family[m] for m in cur]); D = np.array([disc[m] for m in cur])
    out = []
    for i in idx:
        s = S[i].copy(); s[F == F[i]] = -np.inf
        nn = [x for x in np.argsort(-s)[:k] if s[x] > 0]
        if not nn:
            out.append(0.0); continue
        v, c = np.unique(D[nn], return_counts=True)
        out.append(float(v[np.argmax(c)] == D[i]))
    return np.array(out)


def ci(diff, rng, n=2000):
    b = [rng.choice(diff, len(diff)).mean() for _ in range(n)]
    return [round(float(np.percentile(b, 2.5)), 3), round(float(np.percentile(b, 97.5)), 3)]


def main(src: str, dst: str) -> None:
    src, out = Path(src), Path(dst)
    out.mkdir(parents=True, exist_ok=True)
    ms, cur, fam, papers, pidx, cat, refs, authors = load(src)
    family = {m: ms[m]["family"] for m in cur}
    disc = {m: fam[family[m]]["discipline"] for m in cur}
    size = defaultdict(int)
    for m in cur:
        size[family[m]] += 1
    rng = np.random.default_rng(42)
    fams = sorted(set(family.values()))
    train_f = set(rng.choice(fams, len(fams) // 2, replace=False))   # settings chosen here
    with_refs = [i for i, m in enumerate(cur) if refs[m]]
    tr = [i for i in with_refs if family[cur[i]] in train_f]
    te = [i for i in with_refs if family[cur[i]] not in train_f]
    q_tr = [cur[i] for i in tr if size[family[cur[i]]] >= 2]
    q_te = [cur[i] for i in te if size[family[cur[i]]] >= 2]
    print(f"{len(cur)} manuscripts in {len(fams)} families; test: {len(te)} manuscripts, "
          f"{len(q_te)} of them with family members")

    L2 = level_two(papers, pidx, cat, authors)
    grid, mats = [], {}
    for lam in LAMBDAS:
        cov = coverage_vectors(cur, refs, L2, lam, TNORM)
        for a in ALPHAS:
            S = similarity_matrix(cur, cov, a)
            row = {"lambda": lam, "alpha": a, "orness": round(orness(a), 3),
                   "family_map_train": round(evaluate(S, cur, family, q_tr)[0], 4),
                   "family_map_test": round(evaluate(S, cur, family, q_te)[0], 4),
                   "discipline_acc_train": round(discipline_from_other_families(S, cur, family, disc, tr).mean(), 4),
                   "discipline_acc_test": round(discipline_from_other_families(S, cur, family, disc, te).mean(), 4)}
            grid.append(row); mats[(lam, a)] = S
            print("  " + "  ".join(f"{k}={v}" for k, v in row.items()))
        del cov

    base = baselines(cur, refs, len(papers))
    base["Flat bag: works + their authors + areas"] = flat_bag(cur, refs, papers, cat, authors)
    best_f = max(grid, key=lambda g: g["family_map_train"])
    best_d = max(grid, key=lambda g: g["discipline_acc_train"])
    S_f, S_d = mats[(best_f["lambda"], best_f["alpha"])], mats[(best_d["lambda"], best_d["alpha"])]
    ap_owa = evaluate(S_f, cur, family, q_te)[2]
    acc_owa = discipline_from_other_families(S_d, cur, family, disc, te)
    comparison = {}
    for name, S in base.items():
        ap = evaluate(S, cur, family, q_te)[2]
        acc = discipline_from_other_families(S, cur, family, disc, te)
        comparison[name] = {"family_map_test": round(ap.mean(), 4),
                            "owa_family_gain": round(float((ap_owa - ap).mean()), 4),
                            "owa_family_gain_95ci": ci(ap_owa - ap, rng),
                            "discipline_acc_test": round(acc.mean(), 4),
                            "owa_discipline_gain": round(float((acc_owa - acc).mean()), 4),
                            "owa_discipline_gain_95ci": ci(acc_owa - acc, rng)}
    D = [disc[m] for m in cur]
    summary = {"manuscripts": len(cur), "families": len(fams),
               "task_1_family_members": {"chosen_on_train": best_f,
                                         "owa_test_map": round(ap_owa.mean(), 4)},
               "task_2_discipline_from_other_families": {
                   "chosen_on_train": best_d, "owa_test_acc": round(acc_owa.mean(), 4),
                   "majority_class_acc": round(max(D.count(d) for d in set(D)) / len(D), 4)},
               "baselines": comparison, "grid": grid}
    json.dump(summary, open(out / "similarity_summary.json", "w"), indent=2)

    # neighbours for Neo4j: the 5 closest overall and the 5 closest outside the family
    F = np.array([family[m] for m in cur])
    with open(out / "rel_similar_to.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["a", "b", "score", "same_family"])
        for i, m in enumerate(cur):
            s = S_d[i].copy(); s[i] = -1
            pick = list(np.argsort(-s)[:TOP_K])
            s2 = s.copy(); s2[F == F[i]] = -1
            pick += [j for j in np.argsort(-s2)[:TOP_K] if j not in pick]
            for j in pick:
                if s[j] > 0:
                    w.writerow([m, cur[j], round(float(s[j]), 4), bool(F[i] == F[j])])

    # figure: both tasks against orness
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), sharex=True)
    shades = {0.0: "#C3D5E6", 0.25: "#86A9CB", 0.5: "#4F7FAF", 1.0: "#1F4E79"}
    for ax, key, title, bkey in [
            (axes[0], "family_map_test", "Task 1: find the other members of its family",
             "family_map_test"),
            (axes[1], "discipline_acc_test", "Task 2: discipline from the 5 nearest OTHER families",
             "discipline_acc_test")]:
        for lam in LAMBDAS:
            g = [x for x in grid if x["lambda"] == lam and x["alpha"] < 1000]
            ax.plot([x["orness"] for x in g], [x[key] for x in g], marker="o", ms=3, lw=1.8,
                    color=shades[lam], label=f"second-level weight {lam}")
        for name, ls in [("Jaccard (shared references)", ":"),
                         ("Flat bag: works + their authors + areas", "--")]:
            v = comparison[name][bkey]
            ax.axhline(v, color="0.4", ls=ls, lw=1.1)
            off = 0.004 if ls == ":" else -0.010
            ax.text(0.01, v + off, name, ha="left", fontsize=7, color="0.3",
                    transform=ax.get_yaxis_transform())
        ax.set_title(title, fontsize=9.5)
        ax.set_xlabel("orness (0 = all references must match, 1 = any one is enough)", fontsize=8)
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].set_ylabel("mean average precision (test)")
    axes[1].set_ylabel("accuracy (test)")
    axes[1].legend(frameon=False, fontsize=7, loc="lower center")
    fig.tight_layout()
    fig.savefig(out / "similarity_vs_orness.png", dpi=200)
    print(json.dumps({k: v for k, v in summary.items() if k != "grid"}, indent=1))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python similarity.py <output folder of build_graph.py> <results folder>")
    main(sys.argv[1], sys.argv[2])
