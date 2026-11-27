"""Draw readable pictures of the knowledge graph with Graphviz, straight from the CSV files.

    python draw_graphs.py out results        (after build_graph.py and fuzzy_rules.py)

Needs the Graphviz programs (dot, sfdp) on the PATH:
    Windows: winget install graphviz     macOS: brew install graphviz     Linux: apt install graphviz
Writes SVG (sharp at any size) and PNG files to results/graphs/.
"""
from __future__ import annotations

import csv
import subprocess
import sys
import textwrap
from collections import Counter, defaultdict
from pathlib import Path

FONT = "Helvetica"
OKABE_ITO = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#F0E442", "#000000"]
GREY = "#B8B8B8"


def read(folder, name):
    with open(Path(folder) / name, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


def wrap(title, width=24, lines=3):
    parts = textwrap.wrap(title, width)
    if len(parts) > lines:
        parts = parts[:lines]
        parts[-1] = parts[-1][: width - 1] + "…"
    return "\\n".join(esc(p) for p in parts)


def find_graphviz(engine):
    """The Graphviz program, from the PATH or from its usual Windows install folders."""
    import os
    import shutil
    hit = shutil.which(engine)
    if hit:
        return hit
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        cand = Path(base) / "Graphviz" / "bin" / (engine + ".exe")
        if cand.exists():
            return str(cand)
    sys.exit(f"Graphviz program '{engine}' not found. Install Graphviz "
             "(Windows: winget install graphviz), then open a new PowerShell window.")


def render(dot_text, out_base: Path, engine="dot"):
    out_base.parent.mkdir(parents=True, exist_ok=True)
    src = out_base.with_suffix(".dot")
    src.write_text(dot_text, encoding="utf-8")
    for fmt, extra in (("svg", []), ("png", ["-Gdpi=170"])):
        subprocess.run([find_graphviz(engine), f"-T{fmt}", *extra, str(src), "-o", str(out_base.with_suffix("." + fmt))],
                       check=True)
    print("  wrote", out_base.with_suffix(".svg").name)


def risk_fill(r):
    """White (0) to deep orange (1): one hue, so the order reads at a glance."""
    lo, hi = (255, 255, 255), (204, 85, 0)
    c = [round(a + (b - a) * r) for a, b in zip(lo, hi)]
    return "#%02X%02X%02X" % tuple(c)


def legend_risk():
    return f"""
  subgraph cluster_legend {{ label="Legend"; fontname="{FONT}"; fontsize=11; color="#DDDDDD"; style=rounded;
    l1 [label="risky 0", fillcolor="{risk_fill(0)}"]; l2 [label="risky 0.5", fillcolor="{risk_fill(0.5)}"];
    l3 [label="risky 1", fillcolor="{risk_fill(1)}", fontcolor="white"];
    l4 [label="Lean proof", fillcolor="white", color="#009E73", penwidth=3];
    l5 [label="repaired Oct 7", fillcolor="white", style="filled,dashed", penwidth=2.5];
    {{rank=same; l1; l2; l3; l4; l5}} }}"""


def main(src, res):
    src, res = Path(src), Path(res)
    out = res / "graphs"
    ms = {r["mid"]: r for r in read(src, "manuscripts.csv")}
    fam = {r["fid"]: r for r in read(src, "families.csv")}
    disc = {m: fam[r["family"]]["discipline"] if r["family"] in fam else "" for m, r in ms.items()}
    edges = read(src, "rel_depends_on.csv")
    uses = [e for e in edges if e["kind"] == "uses"]
    scores = {r["mid"]: r for r in read(res / "fuzzy", "scores.csv")}
    risky = {m: float(r["risky"]) for m, r in scores.items()}
    prev = read(src, "rel_previous_version.csv")
    repaired_fams = {"232", "233", "034", "036", "056", "341", "342", "376"}
    repaired = {r["new"] for r in prev if ms[r["new"]]["family"] in repaired_fams
                or r["new"].startswith("Exact-Birch-Swinnerton-Dyer")}
    lean = {m for m, r in ms.items() if r["lean_formalized"] == "True"}

    def node(m, extra=""):
        rk = risky.get(m, 0.0)
        style = "filled,dashed" if m in repaired else "filled"
        border = 'color="#009E73", penwidth=3' if m in lean else ('penwidth=2.5' if m in repaired else 'color="#555555"')
        font = 'fontcolor="white"' if rk > 0.7 else ""
        return (f'  "{m}" [label="{wrap(ms[m]["title"])}", fillcolor="{risk_fill(rk)}", '
                f'style="{style}", {border} {font} {extra}];')

    head = (f'  graph [fontname="{FONT}", bgcolor="white", pad=0.3];\n'
            f'  node [shape=box, style="rounded,filled", fontname="{FONT}", fontsize=10, margin="0.12,0.06"];\n'
            f'  edge [color="#777777", arrowsize=0.7, fontname="{FONT}", fontsize=9, fontcolor="#555555"];')

    # 1. schema with counts
    s = read(src, "papers.csv")
    counts = {"Manuscript": len(ms), "Family": len(fam), "Discipline": len(read(src, "disciplines.csv")),
              "Paper": len(s), "Author": len(read(src, "authors.csv")),
              "ArxivCategory": len({r["arxiv_class"] for r in s if r["arxiv_class"]})}
    e = lambda f: len(read(src, f))
    ek = Counter(x["kind"] for x in edges)
    render(f"""digraph G {{ rankdir=LR; {head}
  label="The knowledge graph: node types and links (counts)"; labelloc=t; fontsize=15;
  Manuscript [label="Manuscript\\n{counts['Manuscript']}\\n(186 Lean-checked, 3 withdrawn)", fillcolor="#DCEBF7"];
  Family [label="Family\\n{counts['Family']}", fillcolor="#E8F4EA"]; Discipline [label="Discipline\\n{counts['Discipline']}", fillcolor="#E8F4EA"];
  Paper [label="Paper (human work)\\n{counts['Paper']:,}", fillcolor="#FBE9DA"]; Author [label="Author\\n{counts['Author']:,}", fillcolor="#FBE9DA"];
  ArxivCategory [label="arXiv area\\n{counts['ArxivCategory']}", fillcolor="#FBE9DA"];
  Manuscript -> Family [label="IN_FAMILY"]; Family -> Discipline [label="IN_DISCIPLINE"];
  Manuscript -> Paper [label="CITES  {e('rel_cites.csv'):,}\\n(weighted by citations)"];
  Author -> Paper [label="AUTHORED  {e('rel_authored.csv'):,}"]; Paper -> ArxivCategory [label="IN_CATEGORY"];
  Manuscript -> Manuscript [label="DEPENDS_ON {ek['uses']} (used in a proof)\\lMENTIONS {ek['mentions']} (introduction only)\\lPREVIOUS_VERSION {e('rel_previous_version.csv')} (older edition)\\l"];
}}""", out / "1_schema")

    # 2. the whole web of AI results using AI results, coloured by discipline
    in_web = {x for e_ in uses for x in (e_["src"], e_["dst"]) if ms[x]["status"] == "current"}
    top_disc = [d for d, _ in Counter(disc[m] for m in in_web).most_common(7)]
    col = {d: OKABE_ITO[i] for i, d in enumerate(top_disc)}
    indeg = Counter(e_["dst"] for e_ in uses)
    lines = []
    for m in in_web:
        c = col.get(disc[m], GREY)
        xl = wrap(ms[m]["title"], 22, 2) if indeg[m] >= 8 else ""
        size = 0.12 + 0.025 * min(indeg[m], 20)
        lines.append(f'  "{m}" [label="", xlabel="{xl}", shape=circle, width={size:.2f}, fixedsize=true, '
                     f'style=filled, fillcolor="{c}", color="white", fontsize=11];')
    for e_ in uses:
        if e_["src"] in in_web and e_["dst"] in in_web:
            lines.append(f'  "{e_["src"]}" -> "{e_["dst"]}" [color="#00000040", arrowsize=0.4];')
    leg = "".join(f'<TD BGCOLOR="{col[d]}" WIDTH="14"> </TD><TD ALIGN="LEFT">{esc(d)}  </TD>' for d in top_disc)
    title = (f'<<TABLE BORDER="0" CELLSPACING="4"><TR><TD COLSPAN="{2 * len(top_disc) + 2}"><FONT POINT-SIZE="16">'
             f'AI results that use other AI results ({len(in_web)} manuscripts). Dot size = how many use it.'
             f'</FONT></TD></TR><TR>{leg}<TD BGCOLOR="{GREY}" WIDTH="14"> </TD><TD>other</TD></TR></TABLE>>')
    render(f"""digraph G {{ layout=sfdp; overlap=prism; overlap_scaling=2; splines=false; outputorder=edgesfirst; {head}
  label={title}; labelloc=t; forcelabels=true;
{chr(10).join(lines)}
}}""", out / "2_dependency_web", engine="sfdp")

    # 3. the biggest foundations and what rests on them, coloured by fuzzy risk
    by = lambda t: next(m for m in ms if ms[m]["title"].startswith(t) and ms[m]["status"] == "current")
    roots = [by("Log abundance in characteristic zero"), by("Minimal models and Mori fibre spaces")]
    users = defaultdict(set)
    for e_ in uses:
        users[e_["dst"]].add(e_["src"])
    keep = set(roots)
    for r in roots:
        for u in users[r]:
            keep.add(u)
    sub = [e_ for e_ in uses if e_["src"] in keep and e_["dst"] in keep]
    body = "\n".join(node(m, 'penwidth=4' if m in roots and m not in lean else "") for m in keep)
    eds = "\n".join(f'  "{e_["src"]}" -> "{e_["dst"]}";' for e_ in sub)
    render(f"""digraph G {{ rankdir=BT; nodesep=0.25; ranksep=0.6; {head}
  label="Two critical foundations (top) and the results that use them. Arrows point to what is used. Fill = fuzzy risk (rule R1)."; labelloc=t; fontsize=14;
{body}
{eds}
{legend_risk()}
}}""", out / "3_critical_foundations")

    # 4. the withdrawal and the repaired circular pair
    w = [m for m in ms if ms[m]["status"] == "withdrawn"]
    we = [e_ for e_ in edges if e_["src"] in w and e_["dst"] in w]
    fin = by("Finite ordinary minimal model programs on compact K")
    fin = max((m for m in ms if ms[m]["title"] == ms[fin]["title"] and ms[m]["status"] == "current"),
              key=lambda m: ms[m]["date"])
    ter = by("Termination of generalized log canonical flips on compact")
    pair = [e_ for e_ in uses if {e_["src"], e_["dst"]} == {fin, ter}]
    wnodes = "\n".join(f'    "{m}" [label="{wrap(ms[m]["title"])}\\n(withdrawn {esc(ms[m]["withdrawn_on"])})", fillcolor="#F2F2F2", style="filled,rounded", color="#AA0000", penwidth=2];' for m in w)
    wed = "\n".join(f'    "{e_["src"]}" -> "{e_["dst"]}" [label=" relies on"];' for e_ in we)
    ped = "\n".join(f'    "{e_["src"]}" -> "{e_["dst"]}" [label=" {int(e_["n_cites"]) - int(e_["n_cites_intro"])} citations\\n in proofs", penwidth=2];' for e_ in pair)
    render(f"""digraph G {{ rankdir=TB; {head}
  label="Two events in the release"; labelloc=t; fontsize=15;
  subgraph cluster_w {{ label="October 6: one flawed result, two withdrawn with it"; fontsize=12; color="#DDDDDD"; style=rounded;
{wnodes}
{wed} }}
  subgraph cluster_p {{ label="Rule R3: results that rely on each other (both repaired October 7)"; fontsize=12; color="#DDDDDD"; style=rounded;
{node(fin)}
{node(ter)}
{ped} }}
}}""", out / "4_withdrawal_and_circular_pair")

    # 5. human works that feed many disciplines
    papers = {r["pid"]: r for r in s}
    cites = defaultdict(set)
    for r in read(src, "rel_cites.csv"):
        cites[r["pid"]].add(r["mid"])
    spread = lambda p: Counter(disc[m] for m in cites[p] if disc[m])
    hubs = sorted(papers, key=lambda p: -len(spread(p)))[:3]
    names = {hubs[0]: "Hatcher, Algebraic Topology (2002)"}
    lines, discs_used = [], set()
    for p in hubs:
        t = names.get(p) or f'{papers[p]["title"]} ({papers[p]["year"]})'
        if papers[p]["title"] == "The Stacks Project":
            t = "The Stacks Project (online)"
        if papers[p]["title"].startswith("Probability Inequalities"):
            t = "Hoeffding, Probability inequalities for sums of bounded random variables (1963)"
        lines.append(f'  "{p}" [label="{wrap(t, 30, 3)}\\ncited by {len(cites[p])} manuscripts", fillcolor="#FBE9DA", shape=box];')
        for d, n in sorted(spread(p).items(), key=lambda kv: -kv[1]):
            lines.append(f'  "{p}|{esc(d)}" [label="{esc(d)}  ({n})", fillcolor="#E8F4EA", shape=box, fontsize=9];')
            lines.append(f'  "{p}" -> "{p}|{esc(d)}" [penwidth={0.6 + 0.35 * n:.1f}, dir=none];')
    render(f"""digraph G {{ rankdir=LR; ranksep=0.9; nodesep=0.06; {head}
  label="Human works cited across the most disciplines (number = manuscripts in that discipline citing it)"; labelloc=t; fontsize=14;
{chr(10).join(lines)}
}}""", out / "5_human_bridge_works")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python draw_graphs.py <output folder of build_graph.py> <results folder>")
    main(sys.argv[1], sys.argv[2])
