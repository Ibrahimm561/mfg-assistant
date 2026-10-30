"""Clean + anonymise the raw production CSV  ->  data/production.csv

Usage:  python scripts/prepare_data.py              (anonymised, safe to deploy)
        ANONYMISE=0 python scripts/prepare_data.py  (keeps real names: LOCAL USE ONLY)

Cleaning decisions (each one is documented in the notebook):
  * file is latin-1 encoded; non-breaking spaces removed
  * the two unnamed trailing columns are stray overflow cells -> dropped
  * '?' in QUANTITY / Production Time           -> NULL (not recorded)
  * '?' in the five *_LOSS columns              -> 0   (no stoppage logged)
  * Excel-corrupted loss values like '1-Jan-26' -> NULL
  * comma thousands separators removed
"""
import os, re
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.getenv("RAW_CSV", os.path.join(ROOT, "private", "raw_CVS_PROD_EFFICIENCY-2018.csv"))  # raw file stays OUT of git/Vercel
OUT = os.path.join(ROOT, "data", "production.csv")
ANON = os.getenv("ANONYMISE", "1") != "0"

df = pd.read_csv(RAW, encoding="latin-1").iloc[:, :24]
stats = {"raw_rows": len(df)}

def clean_text(s):
    return re.sub(r"\s+", " ", str(s).replace("\xa0", " ")).strip()

for c in df.columns:
    if df[c].dtype == object or str(df[c].dtype).startswith("str"):
        df[c] = df[c].map(lambda x: clean_text(x) if isinstance(x, str) else x)

def num(s):
    return pd.to_numeric(s.astype(str).str.replace(",", "", regex=False), errors="coerce")

cases = num(df["QUANTITY"]); run_hours = num(df["Production Time"])
stats["cases_missing"] = int(cases.isna().sum())
stats["run_hours_missing"] = int(run_hours.isna().sum())

LOSS = {"PLN_LOSS": "planned_loss_hrs", "ELC_LOSS": "electrical_loss_hrs",
        "MEC_LOSS": "mechanical_loss_hrs", "ELE_LOSS": "utilities_loss_hrs",
        "OPT_LOSS": "operational_loss_hrs"}
out = pd.DataFrame()
out["record_id"] = range(1, len(df) + 1)
dt = pd.to_datetime(df["Posting Date"].astype(str), format="%Y%m%d")
out["date"] = dt.dt.strftime("%Y-%m-%d")
out["year"] = dt.dt.year
out["month"] = dt.dt.month
out["month_name"] = dt.dt.strftime("%b")
out["shift"] = df["SHIFT"]
out["plant"] = df["Plant"]

def line_id(plant, desc, line_no):
    d = desc.upper()
    if "CO2" in d: return f"P{plant}-CO2"
    if "RETAIL" in d: return f"P{plant}-RETAIL"
    if "BULK" in d: return f"P{plant}-BULK"
    if "6 LITR" in d: return f"P{plant}-6L"
    return f"P{plant}-L{line_no}"
out["line"] = [line_id(p, d, n) for p, d, n in zip(df["Plant"], df["Plant Desc"], df["LINE_NO"])]
out["line_no"] = df["LINE_NO"]

BRANDS = [  # order matters (diet before plain); trailing digits may touch the name in fault text
    (r"\bD/PEPSI|\bDIET PEPSI", "BRAND-B DIET"), (r"\bPEPSI", "BRAND-B"),
    (r"\b7[-/ ]?UP", "BRAND-A"), (r"\bM/DEW|\bMOUNTAIN DEW", "BRAND-C"),
    (r"\bMIRR?INDA", "BRAND-D"), (r"\bSTING(?!S)", "BRAND-E"),
    (r"\bSLICE", "BRAND-F"), (r"\bAQUAFINA", "BRAND-G")]
def anon(text):
    if not ANON: return text
    t = text
    for pat, rep in BRANDS: t = re.sub(pat, rep, t, flags=re.I)
    t = re.sub(r"\bWAPDA\b", "UTILITY", t, flags=re.I)
    t = re.sub(r"\bPBL\b|\bYASSIR\b", "SITE", t, flags=re.I)
    return clean_text(t)
def brand_of(product):
    p = product.upper()
    for pat, rep in BRANDS:
        if re.search(pat, p): return rep if ANON else re.search(pat, p).group(0)
    return "OTHER"
out["product"] = df["Product"].map(anon)
out["brand"] = df["Product"].map(brand_of)
def container(p):
    p = p.upper()
    if "CAN" in p: return "CAN"
    if "PET" in p or "WATER" in p: return "PET"
    return "REGULAR"
out["container"] = df["Product"].map(container)
def size_ml(p):
    p = p.upper()
    if re.search(r"\b5G\b", p): return 18900.0          # 5-gallon water bottle
    m = re.search(r"(\d+(?:\.\d+)?)\s*(ML|LTR|LITR|L)\b", p)
    if m:
        v = float(m.group(1)); return v if m.group(2) == "ML" else v * 1000
    return None
out["size_ml"] = df["Product"].map(size_ml)
out["units_per_case"] = df["PSIZE"].str.extract(r"X\s*(\d+)")[0].astype(int)
out["cases"] = cases
out["run_hours"] = run_hours

fault = df["FAULT_DETAIL"].where(df["FAULT_DETAIL"] != "?")
for raw_col, new in LOSS.items():
    raw = df[raw_col].astype(str)
    v = num(raw).where(raw != "?", 0.0)          # '?' -> no stoppage logged
    stats[f"{new}_corrupt_nulled"] = int(v.isna().sum())
    out[new] = v
out["total_loss_hrs"] = out[list(LOSS.values())].sum(axis=1, skipna=True).round(2)
out["fault_detail"] = fault.map(lambda s: anon(s) if isinstance(s, str) else None)

if ANON:
    leak = re.compile(r"\bPEPSI|MIRR?INDA|\b7[-/ ]?UP|M/DEW|MOUNTAIN DEW|\bSTING(?!S)|\bSLICE|AQUAFINA|YASSIR|\bPBL\b|KARACHI|QUETTA|HYDERABAD|\bKHI\b|\bHYD\b|WAPDA", re.I)
    bad = [c for c in ["product", "brand", "line", "fault_detail"] if any(leak.search(x) for x in out[c].dropna().astype(str))]
    if bad:
        import collections
        print("LEAK", collections.Counter(m.group(0) for c in bad for s in out[c].dropna().astype(str) for m in leak.finditer(s)))
    assert not bad, f"anonymisation leak in columns: {bad}"

out.to_csv(OUT, index=False)
closed = fault.fillna("").str.contains("CLOSE", case=False) & cases.isna()
stats["cases_missing_with_line_closed_text"] = int(closed.sum())
stats["written_rows"] = len(out); stats["anonymised"] = ANON
for k, v in stats.items(): print(f"{k:45s} {v}")
print("->", OUT)
