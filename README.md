# Fuzzy reasoning over a knowledge graph of AI-generated mathematics

A knowledge graph of OpenAI's public release of AI-generated mathematics ([openai/math](https://github.com/openai/math)), and three questions answered on it with fuzzy methods:

1. **Trust.** Which results are risky, and which unchecked results do many others rest on?
2. **Relatedness.** Does OWA concept similarity ([Wang, Yager and Reformat, IPMU 2024](#reference)) group related results?
3. **Reviewers.** Who could review a given manuscript?

The aim is an understandable picture of a complex structure, not a model that beats others. Every score can be traced to a rule in words, and every modelling choice is varied to show how much it moves the results.

## The graph

| Element | Count | Notes |
| --- | --- | --- |
| Manuscript | 761 | 730 current, 28 older editions, 3 withdrawn; 186 checked by Lean |
| Family / Discipline | 375 / 17 | OpenAI's own grouping, used as ground truth |
| Paper (cited human work) | 11,321 | merged by DOI, then arXiv id, then title and year |
| Author | 8,492 | authors of the cited works |
| CITES | 18,999 | weighted by how often the work is cited in the text |
| DEPENDS_ON | 452 | one AI manuscript uses a result of another inside a proof |
| MENTIONS | 233 | the other manuscript is cited only in the introduction |

Separating *uses* from *mentions* matters. Counted together, 121 pairs of manuscripts cite each other. After the split, 21 pairs truly use each other's results.

![Schema of the graph](results/graphs/1_schema.svg)

The AI results that build on other AI results form many small groups and one large cluster in algebraic geometry:

![Dependency web](results/graphs/2_dependency_web.svg)

## From words to numbers: the Zadeh and Yager steps

Each step turns part of a sentence a reviewer would say into a number in [0, 1]:

| Step | What it does | Meaning here |
| --- | --- | --- |
| **Fuzzy set** (Zadeh, 1965) | Membership is a degree in [0, 1], not yes or no. | A manuscript can be partly long, partly risky. |
| **Linguistic variable** (Zadeh, 1975) | A word becomes a fuzzy set over a measured quantity. | "Long" is a ramp over the total length of the proofs; "relies heavily" over how often a result is cited inside proofs; "many" over how many results rest on it. |
| **And / or** (T-norm and T-conorm) | "And" and "or" are computed with min/max or other operator pairs. | "No Lean proof **and** (long **or** most prerequisites risky)". |
| **Fuzzy quantifier** (Zadeh, 1983) | "Most" is a fuzzy set on proportions: Q(share). | "**Most** of the results it relies on are risky", from the share of risky prerequisites. |
| **OWA operator** (Yager, 1988) | Sort the values, then take a weighted sum whose weights come from Q; orness slides from "all" to "any". | The same "most", read as an ordered aggregation, so a few sound prerequisites pull the score down. |
| **Importance-weighted OWA** (Yager, 1996) | Each value carries an importance that shapes the OWA weights. | A prerequisite cited ten times in proofs counts more than one cited once. |
| **Linguistic summary** (Yager, 1982) | The truth of "Q of the R items are S" is Q(Σ min(R, S) / Σ R). | "**Most** results repaired on October 7 **were risky**" has truth 0.97. |

## Main findings

**Fuzzy rules in words.** Three plain-language rules give every result a risk score with its reason:

- **R1.** A result is *risky* if it has no Lean proof, and either it is *long*, or *most* of the results it *relies on heavily* are risky.
- **R2.** A result is a *critical foundation* if *many* results rest on it and it is risky.
- **R3.** Two results *support each other suspiciously* if each relies heavily on the other and neither has a Lean proof.

The words are fuzzy sets with breakpoints taken from the data. Each choice (what "long" is measured on, its breakpoints, eight versions of "most", three pairs of and/or operators, two meanings of "many") was varied one at a time, 19 settings in all.

![The fuzzy sets](results/fuzzy/fuzzy_sets.png)

![Which choice moves the results](results/fuzzy/sensitivity.png)

- Quantifiers and T-norms barely move the results. The meaning of "long" and "many" moves them most: the model is sensitive where a human judgement enters.
- Three unchecked results are critical foundations in 18 of 19 settings: *Log abundance in characteristic zero*, *Minimal models and Mori fibre spaces for generalized log canonical Q-pairs* and *The spacetime Penrose inequality and enclosing area*.
- The most suspicious circular pair (R3), two Kähler-geometry manuscripts, was already circular on October 5. OpenAI repaired both on October 7.
- "Most results repaired on October 7 were risky" has truth 0.97 on the October 6 graph. "Few of the other results were risky" has truth only 0.28, so the rule is a filter for review, not a detector of errors.
- The repaired manuscripts had many proofs, not unusually long single proofs.

![Two critical foundations](results/graphs/3_critical_foundations.svg)

![The withdrawal and the repaired circular pair](results/graphs/4_withdrawal_and_circular_pair.svg)

**OWA concept similarity: one weight, opposite effects.** The second level of the similarity compares different cited works through their authors and arXiv area. It helps a coarse task and hurts a fine one, so its weight should follow the task.

![Second-level trade-off](results/second_level_tradeoff.png)

| Method | Discipline from other families (accuracy) | Hidden experts found (top 20) |
| --- | --- | --- |
| Jaccard on shared works | 0.58 | 43.5% |
| OWA similarity, one level | 0.58 | 42.9% |
| OWA similarity, two levels | 0.70 | 37.6% |
| Flat bag: works + their authors + areas | 0.72 | |

The two-level similarity gains 12 points over Jaccard on the coarse task (95% interval 7 to 17). It does not beat a flat bag of the same information, so the gain comes from the second level's information, not from the OWA operator itself.

**Reviewers.** Hiding half of each bibliography (2,093 cases), the authors cited by the most similar manuscripts recover 43% of the hidden experts. For *The Exponential Time Hypothesis is False*, the three reviewers OpenAI named rank 1st, 2nd and 10th.

**Trust propagation with OWA.** On the October 6 graph, trust passed along dependencies ranks the 14 manuscripts repaired on October 7 above chance (AUC 0.72), but no better than simple counts. The OWA setting barely matters, because 64% of manuscripts use no other AI result. (In `auc_vs_orness.png` the "random" line is one random draw; its expected value is 0.5.)

## Key papers in the graph

**Human works the AI manuscripts rely on most**

| Work | Year | AI manuscripts citing it | Disciplines |
| --- | --- | --- | --- |
| The Stacks Project (online reference for algebraic geometry) | ongoing | 72 | 8 |
| Hatcher, *Algebraic Topology* | 2002 | 31 | 11 |
| Birkar, Cascini, Hacon and McKernan, *Existence of minimal models for varieties of log general type* | 2010 | 26 | 1 |
| Demailly, *Complex Analytic and Differential Geometry* | 2012 | 24 | 3 |
| Hoeffding, *Probability inequalities for sums of bounded random variables* | 1963 | 23 | 9 |
| Fortuin, Kasteleyn and Ginibre, *Correlation inequalities on some partially ordered sets* | 1971 | 23 | 1 |
| Kollár and Mori, *Birational Geometry of Algebraic Varieties* | 1998 | 21 | 1 |

Years are as given in the manuscripts' bibliographies. Hatcher's textbook and Hoeffding's inequality are the widest bridges: they are cited from 11 and 9 of the 17 disciplines.

![Human bridge works](results/graphs/5_human_bridge_works.svg)

The most cited human authors (by AI manuscripts citing their work) include the Stacks Project authors, James S. Milne, Elliott H. Lieb, Jean-Pierre Serre, Jean-Pierre Demailly, János Kollár, Paul Erdős and Terence Tao. Some names appear under two spellings (for example "J. S. Milne"), so these counts are lower bounds.

**AI results worth a closer look**

| Result | Why it matters here |
| --- | --- |
| *Minimal models and Mori fibre spaces for generalized log canonical Q-pairs* | 21 results rest on it, no Lean proof; a critical foundation in 18 of 19 settings |
| *Log abundance in characteristic zero* | 19 results rest on it, no Lean proof, risky 0.97; critical in 18 of 19 settings |
| *The spacetime Penrose inequality and enclosing area* | 8 results rest on it, no Lean proof; the main foundation outside algebraic geometry |
| *Algebraicity of Weil classes on split abelian eightfolds* | Withdrawn as flawed on October 6, with the two K3 manuscripts built on it |
| *Finite ordinary minimal model programs on compact Kähler fourfolds* and *Termination of generalized log canonical flips on compact Kähler fourfolds* | Rely on each other (22 and 5 citations in proofs); both repaired on October 7 |
| *Hilbert's tenth problem over the rational numbers* | Short proofs, but fully risky because it rests on risky number-theory results |
| *A counterexample to Hadwiger's conjecture* | Has a Lean proof, but relies on the unchecked *Colin de Verdière* counterexample |
| *The Exponential Time Hypothesis is False* | Lean-checked, 7 results rest on it; the graph ranks its named reviewers 1st, 2nd and 10th |
| *The low-temperature Sherrington–Kirkpatrick fluctuation scale* and related spin-glass papers | The most similar pairs across disciplines (mathematical physics and probability) |

## Run it

Requires Python 3.10+ and a local copy of [openai/math](https://github.com/openai/math). The Lean folder is not needed:

```bash
git clone --filter=blob:none --no-checkout --depth 1 https://github.com/openai/math openai-math
cd openai-math
git sparse-checkout set --no-cone "/*" "!/lean/*" "/lean/formalization.yaml"
git checkout main
cd ..
```

On Windows, run `git config --global core.longpaths true` first.

```bash
pip install -r requirements.txt
python build_graph.py openai-math out      # graph as CSV files       (~10 s)
python trust_owa.py out results            # trust and review list    (~1 min)
python similarity.py out results           # OWA concept similarity   (~30 s)
python reviewer_test.py out results        # reviewer test            (~3 min)
python fuzzy_rules.py out results          # fuzzy rules in words     (~3 s)
python fuzzy_figures.py out results
python draw_graphs.py out results          # pictures of the graph    (needs Graphviz)
```

`draw_graphs.py` needs the Graphviz programs: `winget install graphviz` on Windows, `brew install graphviz` on macOS, `apt install graphviz` on Linux.

To explore the graph in Neo4j (Desktop, Community or Aura):

```bash
python load_neo4j.py out                   # asks for the password; --wipe to reload
python add_results_to_neo4j.py results     # adds trust scores and SIMILAR_TO links
```

Then paste the blocks from `queries.cypher` and `queries_analysis.cypher` into Neo4j Browser. Settings: `NEO4J_URI` (default `bolt://localhost:7687`), `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`.

## Files

| File | What it does |
| --- | --- |
| `build_graph.py` | Reads the repository and writes nodes and edges as CSV (standard library only) |
| `load_neo4j.py`, `add_results_to_neo4j.py` | Load the graph and the results into Neo4j |
| `fuzzy_rules.py`, `fuzzy_figures.py` | Rules R1 to R3, fuzzy sets, quantifiers, sensitivity, linguistic summaries |
| `similarity.py` | OWA concept similarity, two tasks, baselines |
| `reviewer_test.py` | Reviewer suggestion test and the second-level trade-off |
| `trust_owa.py` | Trust propagation and the review list |
| `draw_graphs.py` | Pictures of the graph (Graphviz), as SVG and PNG |
| `queries.cypher`, `queries_analysis.cypher` | Starter questions for Neo4j |
| `results/` | Figures and result tables from the run above |

`results/fuzzy/scores.csv` gives each manuscript its scores and the reason in words.

## Limits

- Only 14 repairs, in about 5 clusters, so no difference between trust scores is significant.
- "Author of a hidden reference" stands in for "good reviewer"; only one manuscript has named reviewers.
- OWA weights come from quantifiers Q(r), not the InHyp family of the IPMU paper.
- Lean status is taken from `lean/formalization.yaml` as of the download; Lean proofs may assume the results they rely on.

## Reference

Y. Wang, R. R. Yager and M. Z. Reformat, "Similarity of Concepts in Weighted Knowledge Graphs", in *Information Processing and Management of Uncertainty in Knowledge-Based Systems (IPMU 2024)*, Springer. [doi:10.1007/978-3-031-74003-9_15](https://doi.org/10.1007/978-3-031-74003-9_15)

Fuzzy methods (titles and years only; full details to be checked):

- L. A. Zadeh, "Fuzzy sets", 1965.
- L. A. Zadeh, "The concept of a linguistic variable and its application to approximate reasoning", 1975.
- L. A. Zadeh, "A computational approach to fuzzy quantifiers in natural languages", 1983.
- R. R. Yager, "A new approach to the summarization of data", 1982.
- R. R. Yager, "On ordered weighted averaging aggregation operators in multicriteria decisionmaking", 1988.
- R. R. Yager, "Quantifier guided aggregation using OWA operators", 1996.

## Data and license

The graph is derived from [openai/math](https://github.com/openai/math), released under the Apache License 2.0. The code here is released under the MIT License.
