"""Build the ONON quarterly financials CSV (items as rows, Q2 2026 -> Q1 2021 as columns)."""
import csv, glob, json, os, re, sys

RAW_JSON, IR_DIR, OUT_CSV, CHECK_TXT = sys.argv[1:5]
ACCESSED = "2026-11-05"
d = json.load(open(RAW_JSON))

periods = [f"{y}Q{q}" for y in range(2026, 2020, -1) for q in (4, 3, 2, 1)]
periods = [p for p in periods if "2021Q1" <= p <= "2026Q2"]
label = lambda p: f"Q{p[5]} {p[:4]}"

# ---------- IR documents per release quarter ----------
IR_BASE = "https://s21.q4cdn.com/241001792/files/doc_financials"
ir_txt = {}
for f in glob.glob(os.path.join(IR_DIR, "*.txt")):
    m = re.match(r"(\d{4})-Q(\d)_(.+)\.txt$", os.path.basename(f))
    ir_txt.setdefault(f"{m.group(1)}Q{m.group(2)}", []).append(f)


def ir_url(txt_path):
    y, q, name = re.match(r"(\d{4})-Q(\d)_(.+)\.txt$", os.path.basename(txt_path)).groups()
    return f"{IR_BASE}/{y}/q{q}/{name}.pdf"


def is_press(path):
    return re.search(r"(?i)presents|reports|announces|-PR_", os.path.basename(path)) is not None


def release_period(doc):
    date = re.search(r"_(\d{4}-\d{2}-\d{2})_", doc).group(1)
    y, m = int(date[:4]), int(date[5:7])
    q = {3: 4, 5: 1, 8: 2, 11: 3}[m]
    return f"{y - 1 if q == 4 else y}Q{q}"


ir_text_cache = {}


def ir_text(period):
    if period not in ir_text_cache:
        ir_text_cache[period] = " ".join(open(f, errors="ignore").read() for f in ir_txt.get(period, []))
    return ir_text_cache[period]


checks = []


def verify(entry):
    """Confirm the reported figure appears in the IR PDF text of the release that carried it."""
    raw = entry["raw"].strip("()").replace("—", "").strip()
    rp = release_period(entry["doc"])
    if raw in ("", "-"):
        ok = True
    else:
        ok = re.search(r"(?<![\d.,])" + re.escape(raw) + r"(?![\d])", ir_text(rp)) is not None
    checks.append((ok, entry["doc"], rp, entry["raw"]))
    return ok


def get(p, key):
    e = d.get(p, {}).get(key)
    if e is None:
        return None
    verify(e)
    return e["v"]


# ---------- cash flow: YTD -> quarter ----------
YTD = {1: "3M", 2: "6M", 3: "9M", 4: "FY"}


def ytd(p, item):
    q = int(p[5])
    return get(p, item) if q == 1 else get(p, f"{item}@{YTD[q]}")


def quarter_cf(p, item):
    q = int(p[5])
    cur = ytd(p, item)
    if q == 1 or cur is None:
        return cur
    prev = ytd(f"{p[:4]}Q{q - 1}", item)
    return None if prev is None else cur - prev


def r(x, n=3):
    return None if x is None else round(x, n)


rows = []  # (section, code, name, notes, {period: value})


def add(code, name, notes, fn):
    rows.append((code, name, notes, {p: fn(p) for p in periods}))


def ratio(a, b):
    return None if a is None or b in (None, 0) else round(a / b * 100, 1)


add("1.1", "Total revenue (Net sales)", "รายงานตรง", lambda p: get(p, "net_sales"))
add("1.2", "Cost of sales", "รายงานตรง ค่าติดลบ = ค่าใช้จ่าย", lambda p: get(p, "cost_of_sales"))
add("1.3", "Gross profit", "รายงานตรง", lambda p: get(p, "gross_profit"))
add("1.4", "%GPM", "คำนวณ: 1.3 / 1.1 x 100", lambda p: ratio(get(p, "gross_profit"), get(p, "net_sales")))
add("1.5", "SG&A", "รายงานตรง (Selling, general and administrative expenses)", lambda p: get(p, "sga"))
def op_result(p):
    if p == "2021Q4":
        # Q4-2021 press release prints (354,901) which does not reconcile with GP + SG&A;
        # the FY2022 press release restates the Q4 2021 comparative as (177.5)
        return -177.5
    return get(p, "operating_result")


add("1.6", "Operating profit (Operating result)",
    "รายงานตรง · Q4 2021 ใช้ตัวเลขเปรียบเทียบ (177.5) จากข่าวผลประกอบการ Q4 2022 เพราะข่าว Q4 2021 พิมพ์ (354,901) ซึ่งไม่ตรงกับ GP + SG&A",
    op_result)
add("1.7", "%Operating margin", "คำนวณ: 1.6 / 1.1 x 100", lambda p: ratio(op_result(p), get(p, "net_sales")))
add("1.8", "Interest and investment income", "รายงานตรง (Financial income)", lambda p: get(p, "financial_income"))
add("1.9", "Interest expense", "รายงานตรง (Financial expenses)", lambda p: get(p, "financial_expenses"))
add("1.10", "Non-operating income", "รายงานตรง (Foreign exchange result) กำไร/ขาดทุนจากอัตราแลกเปลี่ยน", lambda p: get(p, "fx_result"))


def nonop(p):
    parts = [get(p, k) for k in ("financial_income", "financial_expenses", "fx_result")]
    return None if None in parts else r(sum(parts))


add("1.11", "Total non-operating income", "คำนวณ: 1.8 + 1.9 + 1.10", nonop)
add("1.12", "Income before provision for income taxes", "รายงานตรง (Income before taxes)", lambda p: get(p, "pretax"))
add("1.13", "Provision for income taxes", "รายงานตรง ค่าติดลบ = ภาษีจ่าย", lambda p: get(p, "tax"))
add("1.14", "Consolidated net income", "รายงานตรง", lambda p: get(p, "net_income"))


def ebitda(p):
    op, da = op_result(p), get(p, "da")
    return None if None in (op, da) else r(op + da)


add("1.15", "EBITDA", "คำนวณ: 1.6 + ค่าเสื่อมราคาและค่าตัดจำหน่าย (ไม่ใช่ Adjusted EBITDA ของบริษัท)", ebitda)


def etr(p):
    t, pt = get(p, "tax"), get(p, "pretax")
    return None if None in (t, pt) or pt <= 0 else round(-t / pt * 100, 1)


add("1.16", "Effective tax rate (%)", "คำนวณ: -1.13 / 1.12 x 100 · เว้นว่างเมื่อกำไรก่อนภาษีติดลบ", etr)
add("1.x", "Depreciation and amortization", "รายงานตรง ใช้คำนวณ 1.15", lambda p: get(p, "da"))

add("2.1", "Assets", "รายงานตรง ณ วันสิ้นไตรมาส", lambda p: get(p, "assets"))


def liab(p):
    a, b = get(p, "current_liabilities"), get(p, "noncurrent_liabilities")
    return None if None in (a, b) else r(a + b)


add("2.2", "Liabilities", "คำนวณ: Current liabilities + Non-current liabilities", liab)
add("2.3", "Equity", "รายงานตรง ณ วันสิ้นไตรมาส", lambda p: get(p, "equity"))

add("3.1", "CFO", "Q1 รายงานตรง · Q2-Q4 = ยอดสะสมงวดนี้ - ยอดสะสมงวดก่อน", lambda p: r(quarter_cf(p, "cfo")))
add("3.2", "CFI", "วิธีเดียวกับ 3.1", lambda p: r(quarter_cf(p, "cfi")))
add("3.3", "CFF", "วิธีเดียวกับ 3.1", lambda p: r(quarter_cf(p, "cff")))


def capex(p):
    a, b = quarter_cf(p, "capex_ppe"), quarter_cf(p, "capex_intangible")
    return None if None in (a, b) else r(a + b)


def fcf(p):
    c, x = quarter_cf(p, "cfo"), capex(p)
    return None if None in (c, x) else r(c + x)


add("3.x", "Capex (PP&E + intangible assets)", "วิธีเดียวกับ 3.1 ใช้คำนวณ 3.4", capex)
add("3.4", "FCF", "คำนวณ: 3.1 + Capex (Capex เป็นค่าติดลบ) · ยังไม่หักค่าเช่าตาม IFRS 16", fcf)

add("4", "Ratio", "ยังไม่ทำตามที่ผู้ใช้สั่ง", lambda p: None)

add("5.1.1", "Americas", "รายงานตรง · ก่อน 2023 บริษัทใช้ชื่อ North America", lambda p: get(p, "geo_americas"))
add("5.1.2", "Europe, Middle East and Africa", "รายงานตรง · ก่อน 2023 บริษัทรายงานเป็น Europe เท่านั้น", lambda p: get(p, "geo_emea"))
add("5.1.3", "Asia-Pacific", "รายงานตรง", lambda p: get(p, "geo_apac"))
add("5.1.4", "Others", "รายงานตรง (Rest of World) มีถึงปี 2022 · ตั้งแต่ 2023 บริษัทรวมเข้าภูมิภาคอื่น", lambda p: get(p, "geo_other"))
add("5.1.5", "Total revenue", "รายงานตรง (ยอดรวมในตารางภูมิภาค)", lambda p: get(p, "geo_total"))
add("5.2.1", "Shoes", "รายงานตรง", lambda p: get(p, "prod_shoes"))
add("5.2.2", "Apparel", "รายงานตรง", lambda p: get(p, "prod_apparel"))
add("5.2.3", "Accessories", "รายงานตรง", lambda p: get(p, "prod_accessories"))


# ---------- sources per column ----------
def src(p, press):
    files = sorted(ir_txt.get(p, []))
    pick = [f for f in files if is_press(f) == press]
    return " ; ".join(ir_url(f) for f in pick)


src_rows = [
    ("src1", "Source: press release (IR)", "", {p: src(p, True) for p in periods}),
    ("src2", "Source: financial statements (IR)", "", {p: src(p, False) for p in periods}),
    ("src3", "Accessed", "", {p: ACCESSED for p in periods}),
]
for p in ("2021Q1", "2021Q2"):
    src_rows[0][3][p] = src("2022Q" + p[5], True) + " (ตัวเลขเปรียบเทียบปีก่อน)"
    src_rows[1][3][p] = src("2022Q" + p[5], False) + " (ตัวเลขเปรียบเทียบปีก่อน)"

with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.writer(fh)
    w.writerow(["code", "item", "unit", "notes"] + [label(p) for p in periods])
    for code, name, notes, vals in rows + src_rows:
        unit = "" if code.startswith("src") else ("%" if "%" in name or code in ("1.4", "1.7", "1.16") else "CHF m")
        if code == "4":
            unit = ""
        w.writerow([code, name, unit, notes] + ["" if vals[p] is None else vals[p] for p in periods])

# ---------- consistency checks ----------
out = []
def chk(name, a, b, tol=0.15):
    if a is None or b is None:
        return
    if abs(a - b) > tol:
        out.append(f"MISMATCH {name}: {a} vs {b}")

for p in periods:
    g = lambda k: get(p, k)
    if None not in (g("net_sales"), g("cost_of_sales"), g("gross_profit")):
        chk(f"{p} GP", g("net_sales") + g("cost_of_sales"), g("gross_profit"))
    if None not in (g("gross_profit"), g("sga"), g("operating_result")):
        chk(f"{p} OP", g("gross_profit") + g("sga"), g("operating_result"))
    n = nonop(p)
    if None not in (n, g("operating_result"), g("pretax")):
        chk(f"{p} pretax", g("operating_result") + n, g("pretax"))
    if None not in (g("pretax"), g("tax"), g("net_income")):
        chk(f"{p} NI", g("pretax") + g("tax"), g("net_income"))
    if None not in (g("assets"), liab(p), g("equity")):
        chk(f"{p} BS", liab(p) + g("equity"), g("assets"), 0.5)
    parts = [g(k) for k in ("geo_americas", "geo_emea", "geo_apac")]
    if None not in parts and g("geo_total") is not None:
        chk(f"{p} geo", sum(parts) + (g("geo_other") or 0), g("geo_total"), 0.3)
        chk(f"{p} geo vs IS", g("geo_total"), g("net_sales"))
    parts = [g(k) for k in ("prod_shoes", "prod_apparel", "prod_accessories")]
    if None not in parts:
        chk(f"{p} product", sum(parts), g("net_sales"), 0.3)

for y in range(2021, 2026):
    for item in ("cfo", "cfi", "cff"):
        qs = [quarter_cf(f"{y}Q{q}", item) for q in (1, 2, 3, 4)]
        fy = get(f"{y}Q4", f"{item}@FY")
        if None not in qs and fy is not None:
            chk(f"{y} {item} sum of quarters", sum(qs), fy)
    qs = [get(f"{y}Q{q}", "net_sales") for q in (1, 2, 3, 4)]
    fy = get(f"{y}Q4", "net_sales@FY")
    if None not in qs and fy is not None:
        chk(f"{y} sales sum of quarters", sum(qs), fy, 0.3)

bad = [c for c in checks if not c[0]]
uniq = {(c[1], c[3]) for c in checks}
with open(CHECK_TXT, "w") as fh:
    fh.write(f"ตัวเลขที่ตรวจกับ PDF หน้า IR: {len(uniq)} ค่า · ไม่พบใน PDF: {len({(c[1], c[3]) for c in bad})}\n")
    for c in sorted({(c[1], c[2], c[3]) for c in bad}):
        fh.write(f"NOT FOUND IN IR PDF: {c}\n")
    fh.write("\n".join(out) + "\n")
print(open(CHECK_TXT).read()[:4000])
