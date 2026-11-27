"""Build a knowledge graph of the OpenAI math release (github.com/openai/math).

Reads a local copy of the repository and writes CSV files for Neo4j:

  nodes:  manuscripts, families, disciplines, papers (cited human work), authors
  edges:  IN_FAMILY, IN_DISCIPLINE, CITES (manuscript -> human paper, weighted),
          DEPENDS_ON / MENTIONS (manuscript -> manuscript, weighted), PREVIOUS_VERSION,
          SUBSUMED_BY, RELATED_FAMILY, AUTHORED

Usage:
    python build_graph.py C:\\om\\openai-math out

Only the Python standard library is needed. Long Windows paths are handled.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path


# ---------- files ----------

def long_path(p: str) -> str:
    """Windows refuses paths over 260 characters unless they carry the \\\\?\\ prefix."""
    p = os.path.abspath(p)
    if os.name == "nt" and not p.startswith("\\\\?\\"):
        p = "\\\\?\\" + p
    return p


def read(p: str) -> str:
    with open(long_path(p), encoding="utf-8", errors="ignore") as f:
        return f.read()


def files_under(d: str):
    for root, _, names in os.walk(long_path(d)):
        for n in names:
            yield os.path.join(root, n)


# ---------- small text helpers ----------

def norm(s: str) -> str:
    """Lowercase ASCII letters and digits only: for matching titles and names."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\\[a-zA-Z]+", " ", s)          # drop TeX commands
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def clean_tex(s: str) -> str:
    s = re.sub(r"\\href\s*\{[^{}]*\}\s*\{((?:[^{}]|\{[^{}]*\})*)\}", r"\1", s)
    s = re.sub(r"\\url\s*\{[^{}]*\}", " ", s)
    s = re.sub(r"\\[Hvcuk]\s*\{?\s*([a-zA-Z])\s*\}?", r"\1", s)   # \H{o}, \v{s}, \c{c}
    s = re.sub(r"\\(?:newblock|textbf|textrm|textsc|bf|it|em|sc)\b", " ", s)
    s = re.sub(r"\\(?:emph|textit|textbf|mathrm|mathbb|mathcal|operatorname)\{([^{}]*)\}", r"\1", s)
    s = s.replace("{", "").replace("}", "").replace("~", " ").replace("--", "-")
    s = re.sub(r"\\['`^\"~=.]\s*([a-zA-Z])", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def short_id(prefix: str, s: str) -> str:
    return prefix + hashlib.sha1(s.encode()).hexdigest()[:12]


ARXIV_NEW = re.compile(r"(?<![\d.])(\d{4}\.\d{4,5})(?:v\d+)?(?![\d])")
ARXIV_OLD = re.compile(r"\b([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?\b")
DOI = re.compile(r"\b(10\.\d{4,9}/[^\s,;{}\"<>]+)", re.I)
PREPRINT_LINK = re.compile(r"preprints/([^/\s{}\]]+?)/")
OAI_KEY = re.compile(r"OAI:([^}\s,]+)")
FOLDER_DATE = re.compile(r"-(January|February|March|April|May|June|July|August|September|October|"
                         r"November|December)-(\d{1,2})-(\d{4})$")
MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June",
                                      "July", "August", "September", "October", "November",
                                      "December"], 1)}


def folder_date(folder: str) -> str:
    m = FOLDER_DATE.search(folder)
    if not m:
        return ""
    return f"{m.group(3)}-{MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}"


# ---------- bibliography parsing ----------

def parse_bib(text: str) -> list[dict]:
    """A forgiving BibTeX reader: entry type, key and string fields (braces balanced)."""
    out = []
    i = 0
    while True:
        m = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,").search(text, i)
        if not m:
            break
        depth, j = 1, m.end()
        while j < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            j += 1
        body = text[m.end():j - 1]
        fields = {}
        for fm in re.finditer(r"(\w+)\s*=\s*", body):
            k = fm.group(1).lower()
            p = fm.end()
            if p >= len(body):
                continue
            if body[p] == "{":
                d, q = 1, p + 1
                while q < len(body) and d:
                    d += {"{": 1, "}": -1}.get(body[q], 0)
                    q += 1
                val = body[p + 1:q - 1]
            elif body[p] == '"':
                q = body.find('"', p + 1)
                val = body[p + 1:q if q > 0 else len(body)]
            else:
                val = re.match(r"[^,\n]*", body[p:]).group(0)
            fields.setdefault(k, val.strip())
        out.append({"type": m.group(1).lower(), "key": m.group(2), "fields": fields, "raw": body})
        i = j
    return out


def parse_bibitems(text: str) -> list[dict]:
    """\\bibitem[label]{key} free text ... (references written inside the LaTeX)."""
    out = []
    body = text.split("\\begin{thebibliography}", 1)[-1]
    parts = re.split(r"\\bibitem\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", body)
    for k in range(1, len(parts) - 1, 2):
        raw = parts[k + 1].split("\\end{thebibliography}")[0]
        out.append({"type": "bibitem", "key": parts[k].strip(), "fields": {}, "raw": raw.strip()})
    return out


def bib_authors(field: str) -> list[str]:
    names = []
    for a in re.split(r"\s+and\s+", field):
        a = clean_tex(a)
        if not a or a.lower() in ("others", "et al."):
            continue
        if "," in a:
            last, first = [x.strip() for x in a.split(",", 1)]
            a = f"{first} {last}".strip()
        names.append(a)
    return names


VENUE = re.compile(r"^(Journal\b|Annals of\b|Proceedings\b|Transactions\b|Communications in\b|"
                   r"Physical Review|Inventiones|Duke Math|Acta Math|Publications Math|IEEE |SIAM J|"
                   r"[A-Z][a-z]{0,6}\.\s)")   # or an abbreviation such as "J. ", "Ann. ", "Proc. "
NAME = r"(?:[A-Z][a-zA-Z]?\.[\s\-~]*)+(?:(?:de|van|von|der|di|da|du|le|la)\s+)*[A-Z][\w'\-]+(?:,?\s+(?:Jr\.|[IVX]+))?"
AUTHORS = re.compile(rf"^(?:and\s+)?{NAME}(?:(?:,\s*|\s+)(?:and\s+)?{NAME})*$")
AUTHORS_THEN_TITLE = re.compile(rf"^{NAME}(?:(?:,\s*|\s+)(?:and\s+)?{NAME})*\.\s+(.+)$")


def looks_like_venue(t: str) -> bool:
    return bool(VENUE.match(t)) or bool(re.search(r"\bJournal\b", t)) or \
        (t.count(".") >= 2 and len(t) < 50)


def bibitem_title(raw: str) -> str:
    """Title of a free-text reference: an italic phrase that is not a journal name,
    else the block after the authors (\\newblock style), else the first
    comma-separated piece that is not an author."""
    for m in re.finditer(r"\\(?:emph|textit|textsl)\s*\{((?:[^{}]|\{[^{}]*\})*)\}", raw):
        t = clean_tex(m.group(1)).strip(" .,")
        if len(t) >= 8 and not looks_like_venue(t):
            return t
    if "\\newblock" in raw:
        blocks = [clean_tex(b).strip(" .,") for b in raw.split("\\newblock")]
        if len(blocks) > 1 and blocks[1]:
            return blocks[1]
    text = clean_tex(raw)
    m = AUTHORS_THEN_TITLE.match(text)            # "A. Name and B. Name. Title, Journal ..."
    if m:
        return re.split(r"[.,]\s", m.group(1))[0].strip(" .")
    for seg in text.split(", "):
        seg = seg.strip(" .")
        if seg and not AUTHORS.match(seg) and len(seg) > 6:
            return seg
    return clean_tex(raw)[:160]


def entry_has_title(e: dict) -> bool:
    t = clean_tex(e["fields"].get("title", ""))
    return bool(t) and not looks_like_venue(t)


def paper_identity(entry: dict) -> tuple[str, dict]:
    """A stable id for a cited human work, plus its descriptive fields."""
    f, raw = entry["fields"], entry["raw"]
    doi = (f.get("doi") or "").strip().lower()
    if not doi:
        m = DOI.search(raw)
        doi = m.group(1).rstrip(".").lower() if m else ""
    arxiv = ""
    if f.get("eprint"):
        m = ARXIV_NEW.search(f["eprint"]) or ARXIV_OLD.search(f["eprint"])
        arxiv = m.group(1) if m else f["eprint"].strip()
    if not arxiv:
        m = re.search(r"arxiv[^0-9a-z]{0,12}(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})", raw, re.I)
        arxiv = m.group(1) if m else ""
    title = clean_tex(f.get("title", ""))
    year = re.search(r"(1[89]\d\d|20\d\d)", f.get("year", "") or raw)
    year = year.group(1) if year else ""
    if entry["type"] == "bibitem" and not title:
        title = bibitem_title(raw)
    if doi:
        pid = "doi:" + doi
    elif arxiv:
        pid = "arxiv:" + re.sub(r"v\d+$", "", arxiv)
    elif title:
        pid = short_id("t:", norm(title) + "|" + year)
    else:
        pid = short_id("r:", norm(raw)[:200])
    cls = (f.get("primaryclass") or "").strip()
    if not cls and "/" in arxiv:
        cls = arxiv.split("/")[0]
    info = {
        "pid": pid, "title": title, "year": year, "doi": doi, "arxiv": arxiv,
        "arxiv_class": cls, "venue": clean_tex(f.get("journal") or f.get("booktitle") or
                                              f.get("publisher") or ""),
        "authors": bib_authors(f["author"]) if f.get("author") else [],
        "citation": clean_tex(re.sub(r"\\[a-zA-Z]+\*?", " ", raw))[:300],
    }
    return pid, info


# ---------- citation counting inside the LaTeX ----------

CITE = re.compile(r"\\[cC]ite[a-zA-Z]*\*?\s*(?:\[[^\]]*\]\s*){0,2}\{([^}]*)\}")
SECTION = re.compile(r"\\section\*?\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")


def cite_counts(tex_files: list[str]) -> tuple[Counter, Counter, dict]:
    """How often each key is cited, and how often inside an Introduction section."""
    total, intro = Counter(), Counter()
    stats = {"theorems": 0, "chars": 0, "proofs": 0, "proof_chars": 0, "lemmas": 0}
    for path in sorted(tex_files):
        text = read(path)
        text = re.sub(r"(?<!\\)%.*", "", text)                     # comments
        stats["chars"] += len(text)
        for pm in re.finditer(r"\\begin\{proof\}(.*?)\\end\{proof\}", text, re.S):
            stats["proofs"] += 1
            stats["proof_chars"] += len(pm.group(1))
        stats["lemmas"] += len(re.findall(r"\\begin\{(lemma|lem|claim|sublemma)\*?\}", text))
        stats["theorems"] += len(re.findall(
            r"\\begin\{(theorem|lemma|proposition|corollary|thm|lem|prop|cor)\*?\}", text))
        name_is_intro = "intro" in os.path.basename(path).lower()
        pieces = SECTION.split(text)
        # pieces = [before, title1, body1, title2, body2, ...]
        chunks = [(name_is_intro, pieces[0])] + [
            (name_is_intro or "intro" in pieces[k].lower(), pieces[k + 1])
            for k in range(1, len(pieces) - 1, 2)]
        for is_intro, chunk in chunks:
            for m in CITE.finditer(chunk):
                for key in m.group(1).split(","):
                    key = key.strip()
                    if key:
                        total[key] += 1
                        if is_intro:
                            intro[key] += 1
    return total, intro, stats


# ---------- catalogue files ----------

def parse_overview(text: str) -> tuple[dict, dict]:
    """Family id -> (title, description, discipline) from overview.tex."""
    families, disciplines = {}, {}
    current = ""
    for line in text.splitlines():
        m = re.match(r"\\cataloguesection\{([^}]*)\}\{(\d+)\}", line.strip())
        if m:
            current = m.group(1)
            disciplines[current] = int(m.group(2))
            continue
        m = re.match(r"\\resultentry\{(\d+)\}\{", line.strip())
        if m:
            args, i, s = [], line.index("{"), line
            while len(args) < 3 and i < len(s):
                depth, j = 1, i + 1
                while j < len(s) and depth:
                    depth += {"{": 1, "}": -1}.get(s[j], 0)
                    j += 1
                args.append(s[i + 1:j - 1])
                i = s.find("{", j)
                if i < 0:
                    break
            fid = args[0]
            families[fid] = {"title": clean_tex(args[1]) if len(args) > 1 else "",
                             "description": clean_tex(args[2]) if len(args) > 2 else "",
                             "discipline": current}
    return families, disciplines


def parse_contents(text: str) -> tuple[dict, dict, dict]:
    """Family headers, manuscript -> family, manuscript -> (title, abstract) from CONTENTS.md."""
    fam_head, ms_family, ms_info = {}, {}, {}
    current, last_ms = None, None
    for line in text.splitlines():
        m = re.search(r"\*\*(\d{3})\.\s+(.+?)\*\*\s*(.*)", line)
        if m and not line.lstrip().startswith("&emsp;"):
            current = m.group(1)
            fam_head[current] = {"title": m.group(2).rstrip("."), "summary": m.group(3),
                                 "lean_doc": "lean/docs/" in line}
            last_ms = None
            continue
        m = re.match(r"\s*&emsp;\[(.+?)\]\(preprints/(.+?)/[^/]+\.pdf\)", line)
        if m and current:
            folder = m.group(2)
            ms_family[folder] = current
            ms_info[folder] = {"title": m.group(1), "abstract": ""}
            last_ms = folder
            continue
        if last_ms and line.strip() and not line.strip().startswith("<") and \
                not ms_info[last_ms]["abstract"]:
            ms_info[last_ms]["abstract"] = re.sub(r"<[^>]+>", "", line.strip())
    return fam_head, ms_family, ms_info


def readme_facts(text: str) -> dict:
    """Status and links to other manuscripts from a manuscript's README."""
    facts = {"title": "", "withdrawn": "", "previous": [], "subsumed_by": [], "relies_on": []}
    m = re.search(r"^#\s*\[(.+?)\]", text, re.M)
    if m:
        facts["title"] = re.sub(r"^Withdrawal notice:\s*", "", m.group(1))
    m = re.search(r"Withdrawn on ([A-Z][a-z]+ \d{1,2}, \d{4})", text)
    if m:
        facts["withdrawn"] = m.group(1)
    for para in re.split(r"\n\s*\n", text):
        links = [x for x in re.findall(r"\(\.\./(.+?)/[^/()]+\.pdf\)", para)] + \
                [x for x in PREPRINT_LINK.findall(para) if "Citation" not in para]
        links = list(dict.fromkeys(links))
        low = para.lower()
        if "previous version" in low or "previous edition" in low or "version note" in low:
            facts["previous"] += links
        elif "subsume" in low:
            facts["subsumed_by"] += links
        elif facts["withdrawn"] and ("relies on" in low or "flawed" in low or "depends on" in low):
            facts["relies_on"] += links
    return facts


def merge_duplicates(papers: dict, cites: list, authored: set):
    rank = lambda pid: (0 if pid.startswith("doi:") else 1 if pid.startswith("arxiv:") else 2, pid)
    groups = defaultdict(list)
    for pid, p in papers.items():
        t = norm(p["title"])
        if len(t) >= 12:
            groups[t].append(pid)
    canon = {}
    for t, pids in groups.items():
        pids.sort(key=rank)
        clusters = []                                  # [(year, [pids])]
        for pid in pids:
            y = int(papers[pid]["year"]) if papers[pid]["year"] else None
            tol = 3 if len(t) >= 30 else 0
            for c in clusters:
                if y is None or c[0] is None or abs(y - c[0]) <= tol:
                    c[1].append(pid)
                    break
            else:
                clusters.append([y, [pid]])
        for _, members in clusters:
            for pid in members[1:]:
                canon[pid] = members[0]
    for old, new in canon.items():                      # keep the best fields on the survivor
        for k in ("doi", "arxiv", "arxiv_class", "venue", "citation", "year"):
            if not papers[new][k] and papers[old][k]:
                papers[new][k] = papers[old][k]
    merged = {}
    for c in cites:
        pid = canon.get(c["pid"], c["pid"])
        key = (c["mid"], pid)
        if key in merged:
            merged[key]["n_cites"] += c["n_cites"]
            merged[key]["n_cites_intro"] += c["n_cites_intro"]
        else:
            merged[key] = {**c, "pid": pid}
    cites = list(merged.values())
    count = Counter(c["pid"] for c in cites)
    papers = {pid: {**p, "n_manuscripts": count[pid]} for pid, p in papers.items() if pid not in canon}
    authored = {(aid, name, canon.get(pid, pid)) for aid, name, pid in authored}
    return papers, cites, authored


# ---------- main build ----------

def build(repo: str, out: str) -> dict:
    repo = os.path.abspath(repo)
    outp = Path(out)
    outp.mkdir(parents=True, exist_ok=True)
    pre = os.path.join(repo, "preprints")

    families, disciplines = parse_overview(read(os.path.join(repo, "overview.tex")))
    fam_head, ms_family, ms_info = parse_contents(read(os.path.join(repo, "CONTENTS.md")))
    for fid, h in fam_head.items():                       # families only in CONTENTS
        families.setdefault(fid, {"title": h["title"], "description": h["summary"],
                                  "discipline": ""})
        families[fid]["lean_doc"] = h["lean_doc"]
    lean_ids = set(PREPRINT_LINK.findall(read(os.path.join(repo, "lean", "formalization.yaml"))))

    folders = sorted(os.listdir(long_path(pre)))
    folder_set = set(folders)
    manuscripts, cites, depends, previous, subsumed, authored = {}, [], [], [], [], set()
    papers: dict[str, dict] = {}
    n_bib_entries = 0
    for folder in folders:
        d = os.path.join(pre, folder)
        if not os.path.isdir(long_path(d)):
            continue
        files = list(files_under(d))
        readme = next((f for f in files if os.path.basename(f) == "README.md"), None)
        facts = readme_facts(read(readme)) if readme else readme_facts("")
        tex = [f for f in files if f.endswith(".tex")]
        bibs = [f for f in files if f.endswith(".bib")]
        total, intro, stats = cite_counts(tex)

        entries = []
        for b in bibs:
            entries += parse_bib(read(b))
        if not bibs:
            for t in tex:
                body = read(t)
                if "\\bibitem" in body:
                    entries += parse_bibitems(body)
        n_bib_entries += len(entries)

        status = "current" if folder in ms_family else "superseded"
        if facts["withdrawn"]:
            status = "withdrawn"
        manuscripts[folder] = {
            "mid": folder, "title": ms_info.get(folder, {}).get("title") or facts["title"] or folder,
            "date": folder_date(folder), "status": status,
            "family": ms_family.get(folder, ""),
            "lean_formalized": folder in lean_ids,
            "withdrawn_on": facts["withdrawn"],
            "n_theorems": stats["theorems"], "tex_chars": stats["chars"],
            "n_proofs": stats["proofs"], "proof_chars": stats["proof_chars"], "n_lemmas": stats["lemmas"],
            "n_references": len(entries),
            "abstract": ms_info.get(folder, {}).get("abstract", ""),
        }

        seen_targets = set()
        for e in entries:
            key = e["key"]
            n, ni = total.get(key, 0), intro.get(key, 0)
            target = None
            for txt in (key, e["raw"]):
                m = OAI_KEY.search(txt) or PREPRINT_LINK.search(txt)
                if m and m.group(1) in folder_set:    # other sites also have /preprints/ URLs
                    target = m.group(1)
                    break
            if target:
                if target != folder and target not in seen_targets:
                    seen_targets.add(target)
                    depends.append({"src": folder, "dst": target, "n_cites": n,
                                    "n_cites_intro": ni, "source": "bibliography"})
                continue
            pid, info = paper_identity(e)
            p = papers.setdefault(pid, {**{k: v for k, v in info.items() if k != "authors"},
                                        "n_manuscripts": 0, "_seen": set()})
            score = 2 if entry_has_title(e) else 1
            p.setdefault("_titles", Counter())[(score, info["title"])] += 1
            for k in ("year", "doi", "arxiv", "arxiv_class", "venue", "citation"):
                if not p[k] and info[k]:
                    p[k] = info[k]
            if folder not in p["_seen"]:
                p["_seen"].add(folder)
                p["n_manuscripts"] += 1
                cites.append({"mid": folder, "pid": pid, "key": key, "n_cites": n,
                              "n_cites_intro": ni})
            for a in info["authors"]:
                authored.add((short_id("a:", norm(a)), a, pid))

        for t in facts["relies_on"]:
            if t != folder:
                depends.append({"src": folder, "dst": t, "n_cites": 0, "n_cites_intro": 0,
                                "source": "withdrawal_notice"})
        for t in facts["previous"]:
            if t != folder:
                previous.append({"new": folder, "old": t})
        for t in facts["subsumed_by"]:
            if t != folder:
                subsumed.append({"old": folder, "new": t})

    for p in papers.values():                   # best title: from a title field, then most frequent
        if p.get("_titles"):
            (_, t), _ = max(p["_titles"].items(), key=lambda kv: (kv[0][0], kv[1], len(kv[0][1])))
            p["title"] = t

    # the same work often appears under different ids (with and without a DOI):
    # merge on normalized title when the years agree (within 3 years for long titles)
    papers, cites, authored = merge_duplicates(papers, cites, authored)

    # superseded editions inherit the family of their newest edition
    newer = {p["old"]: p["new"] for p in previous}
    newer.update({s_["old"]: s_["new"] for s_ in subsumed})
    for m in manuscripts.values():
        if not m["family"]:
            f, guard = m["mid"], 0
            while f in newer and guard < 10:
                f, guard = newer[f], guard + 1
            m["family"] = manuscripts.get(f, {}).get("family", "")
    for fid in {m["family"] for m in manuscripts.values() if m["family"]}:
        families.setdefault(fid, {"title": "", "description": "", "discipline": ""})

    # keep only dependency edges whose target exists in the repository
    missing_targets = Counter(d["dst"] for d in depends if d["dst"] not in manuscripts)
    depends = [d for d in depends if d["dst"] in manuscripts]
    # A citation only in the Introduction is context ("see also"); a citation in the
    # body is a result being used. Withdrawal notices state a real dependency.
    for d in depends:
        used = d["source"] == "withdrawal_notice" or d["n_cites"] - d["n_cites_intro"] > 0
        d["kind"] = "uses" if used else "mentions"

    # families that mention each other ("together with result 032")
    related = set()
    for fid, f in families.items():
        text = f["description"] + " " + fam_head.get(fid, {}).get("summary", "")
        mentioned = [n for w in re.findall(r"\b[Rr]esults?\s+((?:\d{3}(?:\s*(?:,|and|or)\s*)?)+)", text)
                     for n in re.findall(r"\d{3}", w)]
        for other in mentioned:
            if other != fid and other in families:
                related.add(tuple(sorted((fid, other))))

    # ---------- write ----------
    def write(name, rows, cols):
        with open(outp / name, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)

    fam_count = Counter(m["family"] for m in manuscripts.values() if m["status"] == "current")
    write("manuscripts.csv", manuscripts.values(),
          ["mid", "title", "date", "status", "family", "lean_formalized", "withdrawn_on",
           "n_theorems", "tex_chars", "n_proofs", "proof_chars", "n_lemmas", "n_references", "abstract"])
    write("families.csv", [{"fid": k, **v, "n_manuscripts": fam_count.get(k, 0),
                            "lean_doc": v.get("lean_doc", False)}
                           for k, v in sorted(families.items())],
          ["fid", "title", "discipline", "n_manuscripts", "lean_doc", "description"])
    write("disciplines.csv", [{"name": k, "order": v} for k, v in disciplines.items()],
          ["name", "order"])
    write("papers.csv", [{k: v for k, v in p.items() if not k.startswith("_")} for p in papers.values()],
          ["pid", "title", "year", "doi", "arxiv", "arxiv_class", "venue", "n_manuscripts",
           "citation"])
    authors = {}
    for aid, name, _ in authored:
        authors.setdefault(aid, name)
    write("authors.csv", [{"aid": k, "name": v} for k, v in authors.items()], ["aid", "name"])
    write("rel_cites.csv", cites, ["mid", "pid", "key", "n_cites", "n_cites_intro"])
    write("rel_depends_on.csv", depends,
          ["src", "dst", "kind", "n_cites", "n_cites_intro", "source"])
    write("rel_previous_version.csv", previous, ["new", "old"])
    write("rel_subsumed_by.csv", subsumed, ["old", "new"])
    write("rel_related_family.csv", [{"a": a, "b": b} for a, b in sorted(related)], ["a", "b"])
    write("rel_authored.csv", [{"aid": a, "pid": p} for a, _, p in sorted(authored)],
          ["aid", "pid"])

    status = Counter(m["status"] for m in manuscripts.values())
    summary = {
        "manuscript_folders": len(manuscripts), "status": dict(status),
        "families": len(families), "disciplines": len(disciplines),
        "lean_formalized": sum(m["lean_formalized"] for m in manuscripts.values()),
        "reference_entries": n_bib_entries, "cited_human_works": len(papers),
        "cites_edges": len(cites), "authors": len(authors),
        "depends_on_edges": len(depends),
        "depends_on_by_source": dict(Counter(d["source"] for d in depends)),
        "manuscript_links_by_kind": dict(Counter(d["kind"] for d in depends)),
        "depends_on_missing_targets": len(missing_targets),
        "previous_version_edges": len(previous), "subsumed_by_edges": len(subsumed),
        "related_family_pairs": len(related),
        "manuscripts_without_family": sum(1 for m in manuscripts.values() if not m["family"]),
        "papers_with_doi": sum(1 for p in papers.values() if p["doi"]),
        "papers_with_arxiv": sum(1 for p in papers.values() if p["arxiv"]),
    }
    json.dump(summary, open(outp / "summary.json", "w"), indent=2)
    return summary


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python build_graph.py <path to openai-math repo> <output folder>")
    s = build(sys.argv[1], sys.argv[2])
    print(json.dumps(s, indent=2))
