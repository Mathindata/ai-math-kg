"""Who should review an AI-generated manuscript?  A test with hundreds of cases.

Idea: a good reviewer knows the manuscript's whole background, not only the part we
looked at. For every manuscript with 6+ cited works we hide half of its bibliography,
suggest reviewers from the visible half only, and check whether the suggestions are
authors of the HIDDEN works. We score only "new" people: authors who wrote none of the
visible works (finding those is the hard and useful part).

Methods compared
  co-authors     people who wrote papers with the authors of the visible works
  neighbours     authors cited by the 10 most similar manuscripts, similarity from
                 (a) Jaccard on shared works or (b) the OWA two-level similarity
  OWA coverage   score of author x = OWA over the manuscript's visible works of how well
                 x's own papers cover each work (same work = 1, otherwise the second
                 level: shared co-authors or same arXiv area). Orness 0 asks "does x know
                 ALL of the background", orness 1 asks "does x know ANY of it".

Usage:  python reviewer_test.py out results
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

import similarity as SM

K_NEIGH = 10
TOP = 20                    # length of the suggested reviewer list
ALPHAS = [0.25, 0.5, 1, 2, 4]
LAM, ALPHA_SIM = 1.0, 1.0   # similarity setting chosen on train families in similarity.py


def run(src: str, lam: float, alphas, seeds=(1, 2, 3)) -> dict:
    LAM = lam
    ms, cur, fam, papers, pidx, cat, refs, authors = SM.load(Path(src))
    L2 = SM.level_two(papers, pidx, cat, authors).tocsr()
    n_pap = len(papers)

    # author -> papers, as one sorted column list for fast "max over an author's papers"
    aids = sorted({a for s in authors.values() for a in s})
    apos = {a: i for i, a in enumerate(aids)}
    by_author = defaultdict(list)
    for p, s in authors.items():
        for a in s:
            by_author[apos[a]].append(p)
    order = sorted(by_author)
    cols, starts = [], []
    for a in order:
        starts.append(len(cols)); cols += by_author[a]
    cols, starts, order = np.array(cols), np.array(starts), np.array(order)
    paper_authors = {p: np.array([apos[a] for a in s]) for p, s in authors.items()}

    full = {m: (np.array(list(refs[m])), np.array(list(refs[m].values()))) for m in cur}
    cur_idx = {m: i for i, m in enumerate(cur)}

    def owa_cov(sig, wa, wb):
        """Coverage of A's works (rows) by B's works (columns), then OWA."""
        if not sig.any():
            return 0.0
        c = np.sort((sig * np.minimum(wa[:, None], wb[None, :])).max(1) / wa)[::-1]
        return float(SM.owa_weights(len(c), ALPHA_SIM) @ c)

    # co-authorship: paper x author incidence
    from scipy import sparse
    pr, ac = zip(*[(p, apos[a]) for p, s_ in authors.items() for a in s_])
    PA = sparse.csr_matrix((np.ones(len(pr)), (pr, ac)), shape=(n_pap, len(aids)))

    results = defaultdict(list)
    n_cases = 0
    for seed in seeds:
        rng = np.random.default_rng(seed)
        for m in cur:
            r, w = full[m]
            if len(r) < 6:
                continue
            perm = rng.permutation(len(r))
            vis, hid = perm[: len(r) // 2], perm[len(r) // 2:]
            rv, wv = r[vis], w[vis]
            known = set()
            for p in rv:
                known.update(paper_authors.get(p, ()))
            target = set()
            for p in r[hid]:
                target.update(paper_authors.get(p, ()))
            target -= known
            if not target:
                continue
            n_cases += 1

            def score_list(score: np.ndarray, name: str):
                score = score.copy()
                score[list(known)] = -np.inf
                top = np.argsort(-score)[:TOP]
                top = [t for t in top if score[t] > 0]
                hits = sum(t in target for t in top)
                results[name].append((hits / min(len(target), TOP), float(hits > 0)))

            # neighbours by Jaccard and by OWA similarity (computed from the visible half)
            # co-authors of the people whose work is visible (two steps in the graph)
            v = np.zeros(n_pap); v[rv] = wv
            known_vec = PA.T @ v
            score_list(PA.T @ (PA @ known_vec), "co-authors of cited authors")

            sv = set(rv.tolist())
            Dv = L2[rv].toarray() * LAM                       # visible works x all papers
            jac, owa_sim = np.zeros(len(cur)), np.zeros(len(cur))
            for b in cur:
                if b == m:
                    continue
                rb, wb = full[b]
                if not len(rb):
                    continue
                inter = len(sv & set(rb.tolist()))
                jac[cur_idx[b]] = inter / (len(sv) + len(rb) - inter)
                sig = np.maximum((rv[:, None] == rb[None, :]).astype(float), Dv[:, rb])
                owa_sim[cur_idx[b]] = (owa_cov(sig, wv, wb) + owa_cov(sig.T, wb, wv)) / 2
            for name, S in (("neighbours: Jaccard", jac), ("neighbours: OWA similarity", owa_sim)):
                score = np.zeros(len(aids))
                for j in np.argsort(-S)[:K_NEIGH]:
                    if S[j] <= 0:
                        continue
                    rb, wb = full[cur[j]]
                    for p, wp in zip(rb, wb):
                        if p in paper_authors:
                            score[paper_authors[p]] += S[j] * wp
                score_list(score, name)

            # OWA coverage of the visible works by each author's own papers
            sig = Dv.copy()                                    # works x papers
            sig[np.arange(len(rv)), rv] = 1.0
            G = np.maximum.reduceat(sig[:, cols], starts, axis=1)   # works x authors
            cov = np.zeros((len(rv), len(aids)))
            cov[:, order] = G * wv[:, None] / wv.max()
            srt = -np.sort(-cov, axis=0)                       # each column largest first
            for a in alphas:
                wts = SM.owa_weights(len(rv), a)
                score_list(wts @ srt, f"OWA coverage, orness {SM.orness(a):.2f}")
    return {"cases": n_cases, "list_length": TOP, "second_level_weight": lam,
            "methods": {k: {"recall_of_hidden_experts": round(float(np.mean([x[0] for x in v])), 4),
                            "found_at_least_one": round(float(np.mean([x[1] for x in v])), 4)}
                        for k, v in results.items()}}


def main(src: str, dst: str) -> None:
    """Run the test for several second-level weights and draw the trade-off with
    the discipline task from similarity.py (which must have been run first)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    out = Path(dst); out.mkdir(parents=True, exist_ok=True)
    runs = []
    for lam in (0.0, 0.25, 0.5, 1.0):
        r = run(src, lam, ALPHAS if lam == 1.0 else [])
        runs.append(r)
        print(f"second-level weight {lam}: " + ", ".join(
            f"{k} {v['recall_of_hidden_experts']:.3f}" for k, v in r["methods"].items()
            if k.startswith("neighbours")))
    json.dump(runs, open(out / "reviewer_test.json", "w"), indent=2)

    sim = json.load(open(out / "similarity_summary.json"))
    lams = [r["second_level_weight"] for r in runs]
    rec_owa = [r["methods"]["neighbours: OWA similarity"]["recall_of_hidden_experts"] for r in runs]
    rec_jac = runs[0]["methods"]["neighbours: Jaccard"]["recall_of_hidden_experts"]
    disc = [next(g["discipline_acc_test"] for g in sim["grid"]
                 if g["lambda"] == l and g["alpha"] == ALPHA_SIM) for l in lams]
    disc_jac = sim["baselines"]["Jaccard (shared references)"]["discipline_acc_test"]
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8), sharex=True)
    for ax, y, base, title, lab in [
            (axes[0], rec_owa, rec_jac, "Fine task: find the experts behind hidden references",
             "share of hidden experts found (top 20)"),
            (axes[1], disc, disc_jac, "Coarse task: discipline from other families",
             "accuracy (test families)")]:
        ax.plot(lams, y, marker="o", lw=2, color="#1F4E79", label="OWA similarity (orness 0.5)")
        ax.axhline(base, color="0.45", ls=":", lw=1.2, label="Jaccard on shared references")
        ax.set_title(title, fontsize=9.5)
        ax.set_ylabel(lab, fontsize=8.5)
        ax.set_xlabel("weight of the second level (0 = only identical works count)", fontsize=8.5)
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(out / "second_level_tradeoff.png", dpi=200)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python reviewer_test.py <output folder of build_graph.py> <results folder>")
    main(sys.argv[1], sys.argv[2])
