"""Extract ONON quarterly statements from earnings press releases (table HTML), keyed by period.

Output: JSON {period: {item: (value_chf_m, source_doc, raw_string)}} with direct 3M values,
plus YTD cash-flow values for differencing.
"""
import glob, html, json, os, re, sys

EXH = sys.argv[1]
OUT = sys.argv[2]

ITEMS = {
    "net_sales": r"^net sales$",
    "cost_of_sales": r"^cost of sales$",
    "gross_profit": r"^gross profit$",
    "sga": r"^selling, general and administrative expenses$",
    "operating_result": r"^operating result$",
    "financial_income": r"^financial income$",
    "financial_expenses": r"^financial expenses$",
    "fx_result": r"^foreign exchange (result|gain / \(loss\))( \(1\))?$",
    "pretax": r"^(income|result|income / \(loss\)) before taxes$",
    "tax": r"^income tax(es| expense| benefit / \(expense\))$",
    "net_income": r"^net (income|income / \(loss\)|loss)$",
    "assets": r"^(total )?assets$",
    "current_liabilities": r"^current liabilities$",
    "noncurrent_liabilities": r"^non-current liabilities$",
    "equity": r"^(total )?equity$",
    "da": r"^depreciation and amortization$",
    "cfo": r"from operating activities$",
    "cfi": r"from investing activities$",
    "cff": r"from financing activities$",
    "capex_ppe": r"^purchase of (property, plant and equipment|tangible assets)$",
    "capex_intangible": r"^purchase of intangible assets$",
    "geo_emea": r"^(europe, middle east (and|&) africa|europe|emea)$",
    "geo_americas": r"^(americas|north america)$",
    "geo_apac": r"^asia-pacific$",
    "geo_other": r"^(rest of (the )?world|others?)$",
    "geo_total": r"^net sales$",
    "prod_shoes": r"^shoes$",
    "prod_apparel": r"^apparel$",
    "prod_accessories": r"^accessories$",
}


def num(s):
    s = s.replace(",", "").replace("%", "").strip()
    if s in ("—", "-", "–", ""):
        return 0.0
    neg = s.startswith("(")
    v = float(s.strip("()"))
    return -v if neg else v


def tables(path):
    src = open(path, errors="ignore").read()
    for tb in re.findall(r"(?is)<table.*?</table>", src):
        out = []
        for tr in re.findall(r"(?is)<tr.*?</tr>", tb):
            cells = [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", c)).replace("​", " ").split())
                     for c in re.findall(r"(?is)<t[dh].*?</t[dh]>", tr)]
            cells = [c for c in cells if c and c not in ("(", ")", "%", "CHF", "$")]
            if cells:
                out.append(cells)
        if out:
            yield out


def parse_table(rows):
    """Return (groups, ncols_per_group, scale, body_rows). groups like ['3M','6M']."""
    head = " ".join(" ".join(r) for r in rows[:3])
    groups = []
    for m in re.finditer(r"(?i)(three|six|nine)-month|year ended|fiscal year", head):
        g = m.group(0).lower()
        groups.append({"three-month": "3M", "six-month": "6M", "nine-month": "9M"}.get(g, "FY"))
    unit_row = next((r for r in rows if r[0].startswith("(CHF in")), None)
    if unit_row is None:
        return None
    scale = 1000.0 if "thousand" in unit_row[0] else 1.0
    ncols = len(unit_row) - 1
    if not groups:
        has_assets = any(re.match(r"^(total )?assets$", r[0].lower()) for r in rows)
        groups = ["BS"] if has_assets or re.search(r"\d+/\d+/\d{4}", " ".join(unit_row)) else ["YTD"]
    per = ncols // len(groups) if len(groups) > 1 else ncols
    body = rows[rows.index(unit_row) + 1:]
    return groups, per, scale, body, unit_row


def quarter_of(date):
    y, m = int(date[:4]), int(date[5:7])
    q = {3: 4, 5: 1, 8: 2, 11: 3}[m]
    return (y - 1 if q == 4 else y), q


res = {}  # period -> item -> dict


def put(period, item, val, doc, raw, how):
    d = res.setdefault(period, {})
    if item not in d:  # first occurrence (statement tables come before reconciliations)
        d[item] = {"v": val, "doc": doc, "raw": raw, "how": how}


def kind(f):
    b = os.path.basename(f)
    if b.startswith("20-F"):
        return 2
    return 0 if "press" in b else 1


files = [f for f in glob.glob(os.path.join(EXH, "*")) if re.search(r"press|mda|a992management", f)]
files += glob.glob(os.path.join(EXH, "..", "20-F_*.htm"))
files = [f for f in files if not re.search(r"2023-05-02|2026-01-28", f)]
files.sort(key=lambda f: (re.search(r"_(\d{4}-\d{2}-\d{2})_", f).group(1), kind(f)))
for f in files:
    date = re.search(r"_(\d{4}-\d{2}-\d{2})_", f).group(1)
    y, q = quarter_of(date)
    doc = os.path.basename(f)
    segment_only = kind(f) > 0
    seen_geo = seen_prod = False
    for rows in tables(f):
        p = parse_table(rows)
        if not p:
            continue
        groups, per, scale, body, unit_row = p
        if segment_only and groups in (["BS"], ["YTD"]):
            continue  # MD&A / 20-F segment tables always state their period
        labels = [r[0].lower() for r in body]
        is_geo = any(re.match(ITEMS["geo_apac"], l) for l in labels)
        is_prod = any(re.match(ITEMS["prod_shoes"], l) for l in labels)
        for r in body:
            lab = r[0].lower()
            vals = r[1:]
            for item, rx in ITEMS.items():
                if segment_only and not item.startswith(("geo_", "prod_")):
                    continue
                if item.startswith("geo_") and not is_geo:
                    continue
                if item.startswith("prod_") and not is_prod:
                    continue
                if item == "geo_total" and not is_geo:
                    continue
                if item == "net_sales" and (is_geo or is_prod):
                    continue
                if not re.search(rx, lab):
                    continue
                for gi, g in enumerate(groups):
                    for which, offset in (("cur", 0), ("prior", 1)):
                        idx = gi * per + offset
                        if idx >= len(vals):
                            continue
                        try:
                            v = num(vals[idx]) / scale
                        except ValueError:
                            continue
                        yy = y if which == "cur" else y - 1
                        if g == "BS":
                            if which == "prior":
                                continue  # prior column is last year-end, not same quarter
                            key = f"{yy}Q{q}"
                            how = "BS"
                        elif g == "3M":
                            key, how = f"{yy}Q{q}", "3M"
                        elif g in ("6M", "9M", "FY", "YTD"):
                            if g == "YTD":
                                g = {1: "3M", 2: "6M", 3: "9M", 4: "FY"}[q]
                            key, how = f"{yy}Q{q}", g
                        else:
                            continue
                        tag = item if g in ("3M", "BS") else f"{item}@{g}"
                        if g in ("3M", "BS") and q == 4 and g == "BS":
                            pass
                        # Q1 tables and single-group CF tables in Q1 are 3M by definition
                        put(key, tag, round(v, 3), doc, vals[idx], f"{how}:{which}")
        if is_geo:
            seen_geo = True
        if is_prod:
            seen_prod = True

json.dump(res, open(OUT, "w"), indent=1, ensure_ascii=False)
for k in sorted(res):
    print(k, len(res[k]), sorted(i for i in res[k] if "@" not in i)[:0])
