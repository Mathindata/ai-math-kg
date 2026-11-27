"""Figures for fuzzy_rules.py: the fuzzy sets themselves, and how much each choice moves the results.
Usage:  python fuzzy_figures.py out results   (after fuzzy_rules.py)"""
import csv, sys
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import fuzzy_rules as F

src, dst = sys.argv[1], Path(sys.argv[2]) / "fuzzy"
ms = F.read(src, "manuscripts.csv")
ref = np.array([int(r["proof_chars"]) for r in ms if r["status"] == "current"])
BLUE, GREY = "#1F4E79", "0.55"

# 1. the words as fuzzy sets
fig, ax = plt.subplots(1, 3, figsize=(12, 3.4))
x = np.linspace(0, 300_000, 400)
for name, ls in [("p50-p90 linear", "-"), ("p40-p80 linear (lenient)", "--"),
                 ("p60-p95 linear (strict)", ":"), ("sigmoid around p75", "-.")]:
    ax[0].plot(x / 1000, F.make_long(name, x, ref), ls, color=BLUE if name.startswith("p50") else GREY,
               lw=2.2 if name.startswith("p50") else 1.4, label=name.replace(" linear", ""))
ax[0].set_title("long(m): total length of the proofs", fontsize=10)
ax[0].set_xlabel("thousand characters inside proofs"); ax[0].legend(frameon=False, fontsize=7)
n = np.arange(0, 11)
ax[1].step(n, np.minimum(1, n / 5), where="mid", color=BLUE, lw=2.2, label="n/5 (base)")
ax[1].step(n, (n > 0).astype(float), where="mid", color=GREY, ls="--", label="crisp")
ax[1].set_title("heavily(m, p): citations inside proofs", fontsize=10)
ax[1].set_xlabel("times p is cited in m's proofs"); ax[1].legend(frameon=False, fontsize=7)
r = np.linspace(0, 1, 200)
for name, Q, ls, c in [("Zadeh ramp 0.3-0.8 (base)", F.q_zadeh(0.3, 0.8), "-", BLUE),
                       ("ramp 0.5-0.9 (strict)", F.q_zadeh(0.5, 0.9), "--", GREY),
                       ("ramp 0.2-0.6 (lenient)", F.q_zadeh(0.2, 0.6), ":", GREY),
                       ("Q(r)=r^2", F.q_power(2), "-.", GREY), ("Q(r)=r (average)", F.q_power(1), (0, (1, 1)), GREY)]:
    ax[2].plot(r, [Q(v) for v in r], ls=ls, color=c, lw=2.2 if c == BLUE else 1.4, label=name)
ax[2].set_title("most: truth given the share r", fontsize=10)
ax[2].set_xlabel("share of the relied-on results that are risky"); ax[2].legend(frameon=False, fontsize=7)
for a in ax:
    a.set_ylim(-0.03, 1.05); a.spines[["top", "right"]].set_visible(False)
ax[0].set_ylabel("membership")
fig.tight_layout(); fig.savefig(dst / "fuzzy_sets.png", dpi=200)

# 2. sensitivity
rows = list(csv.DictReader(open(dst / "sensitivity.csv", encoding="utf-8")))[1:]
labels = [f"{r['choice']}: {r['variant']}" for r in rows]
y = np.arange(len(rows))[::-1]
fig, ax = plt.subplots(1, 2, figsize=(12, 5.2), sharey=True)
top = [float(r["same_risky_set"]) for r in rows]
cols = {"measure": BLUE, "long": BLUE, "most": "#4F7FAF", "heavily": GREY, "logic": GREY, "many": GREY}
ax[0].barh(y, top, color=[cols[r["choice"]] for r in rows])
ax[0].axvline(1, color="0.3", lw=1, ls=":")
ax[0].set_xlabel("overlap with the base set of risky results (risky >= 0.5, Jaccard)"); ax[0].set_xlim(0, 1.02)
ax[0].set_yticks(y, labels, fontsize=8)
truth = [float(r["T(most repaired were risky)"]) for r in rows]
ax[1].barh(y, truth, color=[cols[r["choice"]] for r in rows])
ax[1].axvline(float(list(csv.DictReader(open(dst / "sensitivity.csv", encoding="utf-8")))[0]["T(most repaired were risky)"]), color="0.3", lw=1, ls=":")
ax[1].set_xlabel("truth of 'Most results repaired on Oct 7 were risky'"); ax[1].set_xlim(0, 1.02)
for a in ax:
    a.spines[["top", "right"]].set_visible(False)
fig.suptitle("Which modelling choice moves the results?  (dotted line = base setting)", fontsize=11)
fig.tight_layout(); fig.savefig(dst / "sensitivity.png", dpi=200)
print("saved fuzzy_sets.png and sensitivity.png")
