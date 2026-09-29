#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
distribux_local_audit.py
========================
Read-only forensic audit of the DistribuX CSV folder. Runs entirely on your PC.

USAGE (Windows, from any terminal):
    python distribux_local_audit.py
    python distribux_local_audit.py "C:\\some\\other\\folder"          # optional folder
    python distribux_local_audit.py "C:\\data" --out "C:\\somewhere"     # optional output dir

REQUIREMENTS: Python 3.8+ ; pip install pandas numpy   (pandas 1.3 - 3.x tested)

SAFETY: source CSVs are only ever opened for reading. SHA-256 hashes are taken
before and after the run and compared, so "source files unchanged" is proven,
not assumed. All outputs go to ./audit_output (next to this script) unless --out.

OUTPUT: audit_output/audit_report.md, audit_results.json,
        metric_reference_comparison.csv

IF AUTO-DETECTION PICKS A WRONG TABLE/COLUMN: edit TABLE_OVERRIDES /
COLUMN_OVERRIDES below (the report prints exactly what was detected).
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import itertools
import json
import math
import os
import platform
import re
import sys
import time
import traceback
import warnings
from collections import OrderedDict
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.api.types import is_bool_dtype, is_datetime64_any_dtype, is_numeric_dtype

warnings.filterwarnings("ignore")

# =============================================================================
# CONFIG
# =============================================================================
DATA_DIR = r"C:\Users\adars\OneDrive\Desktop\DistribuX"
OUTPUT_DIRNAME = "audit_output"

# Optional manual overrides. Keys for tables are roles:
#   orders, items, payments, returns, refunds, customers, products,
#   employees, promotions, inventory, shipments
# Example: TABLE_OVERRIDES = {"items": "order_lines.csv"}
TABLE_OVERRIDES = {}
# Keys are "<role>.<field>", values are exact column names.
# Example: COLUMN_OVERRIDES = {"items.qty": "Quantity", "payments.amount": "AmountPaid"}
COLUMN_OVERRIDES = {}

# Reference values from the earlier analysis (never forced onto the data).
# (key, label, value, approximate?, format)
REFERENCES = [
    ("gross_sales", "Gross sales", 3827746136, False, "money"),
    ("units", "Units", 1500518, False, "int"),
    ("payments", "Payments", 3018080810, False, "money"),
    ("refunds", "Refunds", 75962300, False, "money"),
    ("orders_with_lines", "Orders with order lines", 259233, False, "int"),
    ("returned_lines", "Returned distinct lines", 29251, False, "int"),
    ("orders_with_returns", "Orders with returns", 27792, False, "int"),
    ("orders_2024", "2024 orders", 195, False, "int"),
    ("total_orders", "Total orders (reference is approximate)", 300000, True, "int"),
    ("lineless_orders", "Line-less orders", 40767, False, "int"),
]
# Classification rules (documented in the report):
#   exact refs : MATCH if |diff| <= 0.5 (integer rounding) ; CLOSE if <= 1% ; else DIFFERENT
#   approx refs: MATCH if <= 0.5% ; CLOSE if <= 5% ; else DIFFERENT
#   no computed value -> NOT TESTABLE

SENTINELS = {"n/a", "na", "null", "none", "nan", "-", "--", "?", "unknown", "tbd",
             "missing", "undefined", "#n/a", "nil", "not available"}
ID_RE = re.compile(r"(id|key|code|sku|ref|no|num|number)$", re.I)
DATE_RE = re.compile(
    r"^\s*(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}([ T]\d{1,2}:\d{2}.*)?"
    r"|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}([ T]\d{1,2}:\d{2}.*)?"
    r"|\d{1,2}[ -][A-Za-z]{3,9}[ -,]+\d{2,4}.*)\s*$")
ENCODINGS = ["utf-8-sig", "cp1252", "latin-1"]
TODAY = dt.date.today()
NUM_KINDS = ("int", "float", "numeric_text")


# =============================================================================
# SMALL HELPERS
# =============================================================================
def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def is_texty(s):
    return not (is_numeric_dtype(s) or is_bool_dtype(s) or is_datetime64_any_dtype(s))


def fnum(x, dec=0):
    if x is None:
        return "n/a"
    try:
        if isinstance(x, (float, np.floating)) and (math.isnan(x) or math.isinf(x)):
            return "n/a"
        return f"{x:,.{dec}f}"
    except Exception:
        return str(x)


def fmt_val(v, kind):
    if v is None:
        return "n/a"
    if kind == "pct":
        return f"{v * 100:.4f}%"
    if kind == "money":
        return fnum(v, 2)
    if kind == "int":
        return fnum(v, 0)
    return str(v)


def cell(x, maxlen=160):
    s = str(x).replace("|", "/").replace("\n", " ")
    return s if len(s) <= maxlen else s[: maxlen - 3] + "..."


def fsum(series):
    """Independent, precision-safe sum (math.fsum) - used as a devil's-advocate check."""
    arr = np.asarray(series, dtype="float64")
    arr = arr[~np.isnan(arr)]
    return math.fsum(arr.tolist()) if arr.size else 0.0


def sha256_file(p, chunk=1 << 20):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def count_lines(p, chunk=1 << 22):
    n = 0
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            n += b.count(b"\n")
    return n


def clean_json(o):
    if isinstance(o, dict):
        return {str(k): clean_json(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean_json(v) for v in o]
    if isinstance(o, (set, frozenset)):
        return sorted(str(v) for v in o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, dt.datetime, dt.date)):
        return str(o)
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, np.ndarray):
        return clean_json(o.tolist())
    if o is pd.NaT:
        return None
    return o


# =============================================================================
# TYPE COERCION (used on demand; the raw dataframe is never altered)
# =============================================================================
def to_num(s):
    """Robust numeric coercion. Returns (float Series, info)."""
    na = s.isna()
    if is_bool_dtype(s):
        return s.astype(float), {"cleaned": False, "fail": 0, "fail_naive": 0}
    if is_numeric_dtype(s):
        return s.astype(float), {"cleaned": False, "fail": 0, "fail_naive": 0}
    naive = pd.to_numeric(s, errors="coerce")
    fail_naive = int(naive.isna().sum() - na.sum())
    if fail_naive == 0:
        return naive.astype(float), {"cleaned": False, "fail": 0, "fail_naive": 0}
    t = s.astype(str).str.strip()
    neg = t.str.match(r"^\(.*\)$")
    t = t.str.replace(r"^\((.*)\)$", r"\1", regex=True)
    t = t.str.replace(r"(?i)^(rs\.?|inr|usd|eur|gbp)\s*", "", regex=True)
    t = t.str.replace("[,\\s$\u20b9\u20ac\u00a3%]", "", regex=True)
    cleaned = pd.to_numeric(t, errors="coerce").astype(float)
    cleaned = cleaned.where(~neg, -cleaned)
    cleaned[na] = np.nan
    fail = int(cleaned.isna().sum() - na.sum())
    return cleaned, {"cleaned": fail < fail_naive, "fail": fail, "fail_naive": fail_naive}


def parse_dates(s):
    """Robust date parsing with dd/mm vs mm/dd detection. Returns (datetime Series, info)."""
    info = {"nonnull": 0, "failed": 0, "failed_default": 0, "method": "as-is", "dayfirst": False,
            "ambiguous_dayfirst": False, "inconsistent_slash_order": False,
            "corrected": False, "failed_examples": []}
    if is_datetime64_any_dtype(s):
        info["nonnull"] = int(s.notna().sum())
        return s, info
    na = s.isna()
    st = s.astype(str).str.strip()
    bad = na | st.eq("") | st.str.lower().isin(["nan", "nat", "none", "null"])
    st = st.mask(bad)
    nb = int(bad.sum())
    info["nonnull"] = int(len(st) - nb)
    if info["nonnull"] == 0:
        return pd.Series(pd.NaT, index=s.index), info
    samp = st.dropna()
    if len(samp) > 200000:
        samp = samp.sample(200000, random_state=0)
    m = samp.str.extract(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})")
    dayfirst = False
    if m[0].notna().mean() > 0.5:
        a = pd.to_numeric(m[0], errors="coerce")
        b = pd.to_numeric(m[1], errors="coerce")
        a12, b12 = bool((a > 12).any()), bool((b > 12).any())
        if a12 and not b12:
            dayfirst = True
        elif a12 and b12:
            info["inconsistent_slash_order"] = True
        elif not a12 and not b12:
            info["ambiguous_dayfirst"] = True
    info["dayfirst"] = dayfirst
    p = pd.to_datetime(st, errors="coerce", dayfirst=dayfirst)
    fail = int(p.isna().sum()) - nb
    info["failed_default"] = fail
    if fail > 0:
        try:
            p2 = pd.to_datetime(st, errors="coerce", dayfirst=dayfirst, format="mixed")
            fail2 = int(p2.isna().sum()) - nb
            if fail2 < fail:
                p, fail = p2, fail2
                info["method"] = "format=mixed"
                info["corrected"] = True
        except Exception:
            pass
    try:
        if not is_datetime64_any_dtype(p):
            p = pd.to_datetime(p, errors="coerce", utc=True)
        if getattr(p.dt, "tz", None) is not None:
            p = p.dt.tz_convert(None)
    except Exception:
        pass
    info["failed"] = fail
    try:
        info["failed_examples"] = st[p.isna() & st.notna()].head(5).tolist()
    except Exception:
        pass
    return p, info


# =============================================================================
# TABLE
# =============================================================================
class Table:
    def __init__(self, path):
        self.path = Path(path)
        self.file = self.path.name
        self.name = self.path.stem
        self.norm = norm(self.name)
        self.size = self.path.stat().st_size
        self.df = None
        self.encoding = None
        self.delim = ","
        self.error = None
        self.raw_lines = None
        self.load_note = ""
        self.kinds = {}
        self.pk = None
        self.pk_candidates = []
        self.pk_notes = []
        self.role = None
        self.corrections = []
        self._num, self._dt, self._key = {}, {}, {}

    # ---- loading ---------------------------------------------------------
    def load(self):
        try:
            with open(self.path, "rb") as f:
                head = f.read(65536)
            first = head.decode("utf-8-sig", errors="replace").splitlines()
            first = first[0] if first else ""
            counts = {d: first.count(d) for d in [",", ";", "\t", "|"]}
            self.delim = max(counts, key=counts.get) if max(counts.values()) > 0 else ","
            self.raw_lines = count_lines(self.path)
        except Exception as e:  # e.g. OneDrive cloud-only placeholder
            self.error = f"cannot read file: {e!r}"
            return
        for enc in ENCODINGS:
            try:
                hdr = pd.read_csv(self.path, sep=self.delim, encoding=enc, nrows=0, dtype=str)
                dtypes = {c: str for c in hdr.columns if ID_RE.search(str(c).strip())}
                kw = dict(sep=self.delim, encoding=enc, dtype=dtypes, keep_default_na=False,
                          na_values=[""], low_memory=False)
                try:
                    df = pd.read_csv(self.path, **kw)
                except pd.errors.ParserError:
                    df = pd.read_csv(self.path, on_bad_lines="skip", **kw)
                    self.load_note = "malformed lines were skipped by the parser"
                self.df, self.encoding = df, enc
                self.df.columns = [str(c).strip() for c in self.df.columns]
                self.infer_kinds()
                return
            except UnicodeDecodeError:
                continue
            except Exception as e:
                self.error = repr(e)
                return
        self.error = "could not decode with any supported encoding"

    def infer_kinds(self):
        for c in self.df.columns:
            s = self.df[c]
            if is_bool_dtype(s):
                self.kinds[c] = "bool"
                continue
            if is_datetime64_any_dtype(s):
                self.kinds[c] = "date"
                continue
            nn = s.dropna()
            if len(nn) == 0:
                self.kinds[c] = "empty"
                continue
            if is_numeric_dtype(s):
                if ID_RE.search(c):
                    self.kinds[c] = "id"
                else:
                    head = nn.iloc[:100000]
                    self.kinds[c] = "int" if bool((head == np.floor(head)).all()) else "float"
                continue
            # text-like
            if ID_RE.search(c):
                self.kinds[c] = "id"
                continue
            samp = nn if len(nn) <= 20000 else nn.sample(20000, random_state=0)
            st = samp.astype(str).str.strip()
            if st.str.match(DATE_RE).mean() >= 0.9:
                _, inf = parse_dates(samp)
                if inf["nonnull"] and (1 - inf["failed"] / inf["nonnull"]) >= 0.95:
                    self.kinds[c] = "date"
                    continue
            x, _ = to_num(samp)
            if x.notna().mean() >= 0.90:
                self.kinds[c] = "numeric_text"
                continue
            self.kinds[c] = "text"

    # ---- lazy typed accessors ---------------------------------------------
    def num(self, c):
        if c not in self._num:
            x, info = to_num(self.df[c])
            self._num[c] = (x, info)
            if info["cleaned"]:
                self.corrections.append({"table": self.name, "column": c, "type": "numeric_cleaning",
                                         "before": f"{info['fail_naive']} values unparsable by naive parse",
                                         "after": f"{info['fail']} unparsable after stripping commas/currency/%"})
        return self._num[c][0]

    def num_info(self, c):
        self.num(c)
        return self._num[c][1]

    def dates(self, c):
        if c not in self._dt:
            p, info = parse_dates(self.df[c])
            self._dt[c] = (p, info)
            if info["corrected"]:
                self.corrections.append({"table": self.name, "column": c, "type": "date_parse",
                                         "before": f"{info['failed_default']} unparsed with default parser",
                                         "after": f"{info['failed']} unparsed with format=mixed"})
        return self._dt[c][0]

    def date_info(self, c):
        self.dates(c)
        return self._dt[c][1]

    def key(self, c, mode="basic", cache=False):
        ck = (c, mode)
        if ck in self._key:
            return self._key[ck]
        s = self.df[c]
        na = s.isna()
        k = s.astype(str).str.strip().str.replace(r"\.0+$", "", regex=True)
        if mode == "aggressive":
            k = (k.str.upper().str.replace(r"[\s\-_/]", "", regex=True)
                 .str.replace(r"^0+(?=.)", "", regex=True))
        k = k.mask(na | k.eq("") | k.str.lower().eq("nan"))
        if cache:
            self._key[ck] = k
        return k


def comp_key(t, cols, mode="basic", cache=False):
    ks = [t.key(c, mode, cache) for c in cols]
    out = ks[0]
    for k in ks[1:]:
        out = out + "|" + k
    return out


# =============================================================================
# FINDING  (carries the evidence-gated loop log)
# =============================================================================
class Finding:
    def __init__(self, key, metric, grain, denominator, source, fmt="int"):
        self.key, self.metric, self.grain = key, metric, grain
        self.denominator, self.source, self.fmt = denominator, source, fmt
        self.result = None
        self.status = "UNRESOLVED"
        self.calc = ""
        self.limitations = []
        self.challenges = []      # {name, ok, detail, material}
        self.devils = []          # {name, alt, agrees, detail}
        self.corrections = []
        self.extra = {}
        self.gate = ""

    def chk(self, name, ok, detail="", material=True):
        self.challenges.append({"check": name, "passed": bool(ok), "detail": detail,
                                "material": bool(material and not ok)})

    def dev(self, name, alt, agrees, detail=""):
        self.devils.append({"check": name, "alt_value": alt, "agrees": bool(agrees), "detail": detail})

    def limit(self, text):
        if text and text not in self.limitations:
            self.limitations.append(text)

    def finish(self, unsupported_reason=None):
        if unsupported_reason:
            self.status = "UNSUPPORTED"
            self.limit(unsupported_reason)
        elif self.result is None:
            self.status = "UNSUPPORTED"
        elif any(c["material"] for c in self.challenges) or any(not d["agrees"] for d in self.devils):
            self.status = "UNRESOLVED"
        elif self.corrections:
            self.status = "CORRECTED"
        else:
            self.status = "VERIFIED"
        reproducible = self.result is not None and bool(self.calc)
        limited = bool(self.limitations) or self.status == "VERIFIED"
        self.gate = "PASS" if (reproducible and limited) or self.status == "UNSUPPORTED" else "REVIEW"
        return self

    def loop_summary(self):
        ch = [c for c in self.challenges if not c["passed"]]
        dv = [d for d in self.devils if not d["agrees"]]
        parts = []
        parts.append("challenge: " + ("; ".join(f"{c['check']} ({c['detail']})" for c in ch)
                                      if ch else f"{len(self.challenges)} risk check(s) clean"))
        parts.append("devil: " + ("; ".join(f"{d['check']} ({d['detail']})" for d in dv)
                                  if dv else (f"{len(self.devils)} independent check(s) agree" if self.devils else "no independent route available")))
        if self.corrections:
            parts.append("corrected: " + "; ".join(self.corrections))
        return " | ".join(parts)

    def to_dict(self):
        return {"key": self.key, "metric": self.metric, "result": self.result, "grain": self.grain,
                "denominator": self.denominator, "source": self.source, "status": self.status,
                "calculation": self.calc, "limitations": self.limitations,
                "challenges": self.challenges, "devils_advocate": self.devils,
                "corrections": self.corrections, "requirement_gate": self.gate, "extra": self.extra}


# =============================================================================
# COLUMN PATTERNS (matched against lower-cased names with punctuation removed)
# =============================================================================
FIELDS = {
    "orders.id": [r"^orderid$", r"^ordernumber$|^orderno$|^orderref", r"^id$"],
    "orders.date": [r"^orderdate$",
                    r"^(orderdatetime|ordertimestamp|orderedat|orderedon|placedat|placedon|createdat|createdon|createddate|orderdt)$",
                    r"order.*(date|time|dt)", r"^date$", r"date"],
    "orders.total": [r"^(ordertotal|totalamount|ordervalue|orderamount|grandtotal|total)$"],
    "orders.customer": [r"^customerid$", r"customer"],
    "items.id": [r"^(orderitemid|lineitemid|lineid|orderlineid|itemid|detailid|id)$"],
    "items.qty": [r"^(quantity|qty|units|orderqty|qtyordered|quantityordered|unitssold|quantitysold)$", r"quantity|qty"],
    "items.price": [r"^(unitprice|priceperunit|sellingprice|saleprice|listprice|price|rate)$", r"price"],
    "items.amount": [r"^(linetotal|lineamount|linevalue|linesales|lineamt|extendedprice|extendedamount|itemtotal|itemamount|grosssales|grossamount|grossrevenue|salesamount|netamount|netsales|subtotal|amount|total|sales|revenue|value)$"],
    "items.discount": [r"disc"],
    "payments.amount": [r"^(paymentamount|amountpaid|paidamount|amount|payment|value)$", r"amount"],
    "payments.status": [r"^(paymentstatus|status|state)$", r"status"],
    "payments.date": [r"^(paymentdate|paidat|paidon|paymentdatetime|transactiondate|date)$", r"date"],
    "returns.refund": [r"refund"],
    "returns.qty": [r"^(returnqty|returnedqty|quantityreturned|qtyreturned|returnquantity|quantity|qty|units)$"],
    "returns.date": [r"^(returndate|returnedat|returnedon|date)$", r"date"],
    "refunds.amount": [r"^(refundamount|amount|value|total|refund)$", r"amount|refund"],
    "customers.signup": [r"sign(ed)?up|regist|joined|joindate|enrol|onboard|acquisition|customersince",
                         r"^(createdat|createdon|createddate|created|since)$"],
}
PRICE_EXCL = r"disc|total|cost|tax|net"
AMT_EXCL = r"disc|tax"

ROLE_PATTERNS = OrderedDict([
    ("items", r"orderitem|orderline|orderdetail|lineitem|salesline|salesitem|orderproduct"),
    ("returns", r"return|rma"),
    ("refunds", r"refund|creditnote"),
    ("payments", r"payment|receipt|remittance"),
    ("shipments", r"shipment|delivery|deliveries|dispatch|logistic"),
    ("inventory", r"inventory|stock"),
    ("promotions", r"promo|campaign|coupon|discount"),
    ("employees", r"employee|staff|salesrep|salesperson|personnel|agent|associate"),
    ("customers", r"customer|client|buyer|retailer|dealer|outlet|distributor"),
    ("products", r"product|sku|catalog|material|article"),
    ("orders", r"order|sale"),
])


# =============================================================================
# AUDITOR
# =============================================================================
class Auditor:
    def __init__(self, data_dir, out_dir):
        self.data_dir, self.out_dir = Path(data_dir), Path(out_dir)
        self.T = OrderedDict()          # name -> Table
        self.roles = {}
        self.role_alts = {}
        self.mapping = OrderedDict()    # "role.field" -> column
        self.ambiguity = []
        self.inventory, self.quality, self.fks, self.weak_fks = [], [], [], []
        self.F = OrderedDict()          # key -> Finding
        self.fanout, self.discount, self.analytics = {}, {}, []
        self.recon, self.errors, self.notes = [], [], []
        self.integrity = {}
        self.docs = []
        self.V = {}                     # shared values between metrics
        self.t0 = time.time()

    def log(self, msg):
        print(f"[{time.time() - self.t0:6.1f}s] {msg}", flush=True)

    def guard(self, name, fn):
        try:
            fn()
        except Exception as e:
            self.errors.append({"step": name, "error": repr(e), "trace": traceback.format_exc()[-1500:]})
            self.log(f"!! step '{name}' failed: {e!r} (continuing)")

    # ------------------------------------------------------------------ run
    def run(self):
        self.discover()
        self.guard("inventory", self.build_inventory)
        self.guard("primary keys", self.find_pks)
        self.guard("data quality", self.data_quality)
        self.guard("relationships", self.find_fks)
        self.guard("roles", self.assign_roles)
        for name, fn in [("total_orders", self.m_total_orders), ("orders_with_lines", self.m_orders_with_lines),
                         ("units", self.m_units), ("gross_sales", self.m_gross_sales),
                         ("payments", self.m_payments), ("refunds", self.m_refunds),
                         ("returns", self.m_returns), ("orders_2024", self.m_orders_2024),
                         ("return_rates", self.m_rates)]:
            self.guard("metric:" + name, fn)
        self.guard("fan-out", self.fanout_analysis)
        self.guard("discount", self.discount_analysis)
        self.guard("reconciliation", self.reconcile)
        self.guard("unsupported analytics", self.unsupported_analytics)
        self.finalize_integrity()
        self.write_outputs()

    # ------------------------------------------------------------ discovery
    def discover(self):
        if not self.data_dir.exists():
            print(f"ERROR: folder not found: {self.data_dir}\nPass the folder as the first argument.")
            sys.exit(2)
        files = sorted([p for p in self.data_dir.iterdir() if p.is_file() and p.suffix.lower() == ".csv"],
                       key=lambda p: p.name.lower())
        if not files:
            print(f"ERROR: no .csv files directly inside {self.data_dir}")
            sys.exit(2)
        self.log(f"Found {len(files)} CSV file(s) in {self.data_dir}")
        sub = [p for p in self.data_dir.rglob("*.csv")
               if p.parent != self.data_dir and OUTPUT_DIRNAME not in p.parts]
        if sub:
            self.notes.append(f"{len(sub)} CSV(s) exist in sub-folders and were NOT audited (top-level only).")
        if len(files) != 12:
            self.notes.append(f"Expected 12 tables, found {len(files)} top-level CSVs.")
        self.integrity["before"] = {p.name: sha256_file(p) for p in files}
        for p in self.data_dir.iterdir():
            if p.is_file() and p.suffix.lower() in (".txt", ".md", ".json", ".yaml", ".yml", ".sql",
                                                     ".xlsx", ".xls", ".docx", ".pdf", ".rtf"):
                self.docs.append(p)
        for p in files:
            t = Table(p)
            self.log(f"loading {p.name} ({t.size / 1e6:.1f} MB)")
            t.load()
            if t.error:
                self.errors.append({"step": "load " + p.name, "error": t.error})
                self.log(f"!! {p.name}: {t.error}")
                if "cannot read" in t.error:
                    self.notes.append(f"{p.name}: unreadable - if in OneDrive, right-click the folder > "
                                      "'Always keep on this device', then re-run.")
            if t.df is not None:
                self.T[t.name] = t

    # ------------------------------------------------------------ inventory
    def build_inventory(self):
        for t in self.T.values():
            df = t.df
            note = t.load_note
            if t.raw_lines is not None and t.raw_lines - 1 != len(df):
                note = (note + "; " if note else "") + (
                    f"raw newline count-1 = {t.raw_lines - 1:,} vs parsed rows {len(df):,} "
                    "(embedded newlines in quoted text, blank lines, or skipped rows)")
            self.inventory.append({
                "file": t.file, "rows": len(df), "columns": df.shape[1], "size_bytes": t.size,
                "encoding": t.encoding, "delimiter": t.delim,
                "column_types": {c: f"{t.kinds[c]} ({df[c].dtype})" for c in df.columns}, "note": note})

    # ------------------------------------------------------------------ PKs
    @staticmethod
    def singular(base):
        base = re.sub(r"^(dim|fact|tbl|raw|stg)", "", base)
        if base.endswith("ies"):
            return base[:-3] + "y"
        if base.endswith("s") and not base.endswith("ss"):
            return base[:-1]
        return base

    def pk_score(self, t, c):
        nc, sg = norm(c), self.singular(t.norm)
        s = 0
        if nc in (sg + "id", t.norm + "id", "id", sg + "key", sg + "code", sg + "no", sg + "number"):
            s += 10
        if ID_RE.search(c):
            s += 3
        if list(t.df.columns).index(c) == 0:
            s += 1
        return s

    def find_pks(self):
        for t in self.T.values():
            n = len(t.df)
            if n == 0:
                continue
            cands = []
            for c in t.df.columns:
                if t.kinds[c] not in ("id", "int", "text", "numeric_text"):
                    continue
                s = t.df[c]
                if s.isna().any():
                    continue
                if s.nunique() != n:
                    continue
                if t.key(c, "basic").nunique() != n:
                    t.pk_notes.append(f"{c}: unique only before whitespace/format normalisation")
                    continue
                cands.append(c)
            cands.sort(key=lambda c: -self.pk_score(t, c))
            t.pk_candidates = cands
            if cands:
                t.pk = [cands[0]]
                continue
            idc = [c for c in t.df.columns if t.kinds[c] in ("id", "int") and ID_RE.search(c)][:6]
            for a, b in itertools.combinations(idc, 2):
                sub = t.df[[a, b]]
                if not sub.isna().any().any() and not sub.duplicated().any():
                    t.pk = [a, b]
                    t.pk_candidates = [f"{a}+{b}"]
                    break

    # -------------------------------------------------------- data quality
    def data_quality(self):
        for t in self.T.values():
            df, n = t.df, len(t.df)
            q = {"table": t.name, "rows": n, "duplicate_rows": int(df.duplicated().sum()) if n else 0,
                 "candidate_primary_keys": t.pk_candidates, "chosen_pk": t.pk, "pk_notes": t.pk_notes,
                 "columns": [], "date_ranges": [], "issues": []}
            if q["duplicate_rows"]:
                q["issues"].append(f"{q['duplicate_rows']:,} fully duplicated rows")
            if t.pk is None and n:
                q["issues"].append("no unique key found (single or 2-column)")
            for c in df.columns:
                s, k = df[c], t.kinds[c]
                info = {"name": c, "kind": k, "dtype": str(s.dtype), "nulls": int(s.isna().sum()),
                        "null_pct": round(100 * float(s.isna().mean()), 4) if n else 0,
                        "distinct": int(s.nunique())}
                if is_texty(s):
                    st = s.dropna().astype(str).str.strip()
                    info["blank_strings"] = int(st.eq("").sum())
                    info["sentinel_strings"] = int(st.str.lower().isin(SENTINELS).sum())
                    if k in ("numeric_text", "int", "float") and info["sentinel_strings"]:
                        q["issues"].append(f"{c}: {info['sentinel_strings']:,} sentinel strings in numeric column")
                    elif info["sentinel_strings"] and k not in ("text",):
                        q["issues"].append(f"{c}: {info['sentinel_strings']:,} sentinel strings")
                if k in NUM_KINDS:
                    x, ni = t.num(c), t.num_info(c)
                    v = x.dropna()
                    if len(v):
                        p1, p99 = float(v.quantile(0.01)), float(v.quantile(0.99))
                        info.update({"min": float(v.min()), "max": float(v.max()), "mean": float(v.mean()),
                                     "median": float(v.median()), "p1": p1, "p99": p99,
                                     "negatives": int((v < 0).sum()), "zeros": int((v == 0).sum()),
                                     "coercion_failures": ni["fail"], "needed_cleaning": ni["cleaned"]})
                        nc = norm(c)
                        if re.search(r"qty|quantity|units|price|stock|onhand|salary", nc) and info["negatives"]:
                            q["issues"].append(f"{c}: {info['negatives']:,} negative values (non-negative expected)")
                        if re.search(r"qty|quantity|units|price", nc) and info["zeros"]:
                            q["issues"].append(f"{c}: {info['zeros']:,} zero values")
                        if p99 > 0 and float(v.max()) > 20 * p99:
                            q["issues"].append(f"{c}: max {v.max():,.2f} > 20x p99 ({p99:,.2f}) - extreme outlier")
                    if ni["fail"]:
                        q["issues"].append(f"{c}: {ni['fail']:,} non-numeric values after cleaning")
                    if ni["cleaned"]:
                        q["issues"].append(f"{c}: stored as text; needed comma/currency stripping (CORRECTED on read)")
                if k == "date":
                    p, di = t.dates(c), t.date_info(c)
                    v = p.dropna()
                    dr = {"column": c, "min": str(v.min()) if len(v) else None, "max": str(v.max()) if len(v) else None,
                          "nonnull": di["nonnull"], "unparsed": di["failed"], "parse_method": di["method"],
                          "dayfirst": di["dayfirst"], "ambiguous_dayfirst": di["ambiguous_dayfirst"],
                          "future_dates": int((v.dt.date > TODAY).sum()) if len(v) else 0,
                          "before_1990": int((v < pd.Timestamp("1990-01-01")).sum()) if len(v) else 0,
                          "years": {int(y): int(cnt) for y, cnt in v.dt.year.value_counts().sort_index().items()} if len(v) else {}}
                    q["date_ranges"].append(dr)
                    if di["failed"]:
                        q["issues"].append(f"{c}: {di['failed']:,} unparseable dates e.g. {di['failed_examples'][:3]}")
                    if dr["future_dates"]:
                        q["issues"].append(f"{c}: {dr['future_dates']:,} dates after today")
                    if dr["before_1990"]:
                        q["issues"].append(f"{c}: {dr['before_1990']:,} dates before 1990")
                    if di["ambiguous_dayfirst"]:
                        q["issues"].append(f"{c}: dd/mm vs mm/dd ambiguous (all day/month parts <= 12); year-level metrics unaffected")
                    if di["inconsistent_slash_order"]:
                        q["issues"].append(f"{c}: mixed dd/mm and mm/dd formats detected")
                q["columns"].append(info)
            # date-order logic inside the table
            ranks = [(c, self.date_rank(c)) for c in df.columns if t.kinds[c] == "date"]
            ranks = [(c, r) for c, r in ranks if r is not None]
            for (ca, ra), (cb, rb) in itertools.combinations(ranks, 2):
                if ra == rb:
                    continue
                (ea, la), (eb, lb) = ((ca, ra), (cb, rb)) if ra < rb else ((cb, rb), (ca, ra))
                bad = int((t.dates(ea) > t.dates(eb)).sum())
                if bad:
                    q["issues"].append(f"date logic: {bad:,} rows where {ea} is later than {eb}")
            self.quality.append(q)

    @staticmethod
    def date_rank(c):
        n = norm(c)
        if re.search(r"expect|estimat|promis|planned|scheduled|due|eta", n):
            return None
        for r, pat in enumerate([r"order|placed|created|signup|regist|start", r"paid|payment",
                                 r"ship|dispatch", r"deliver|receiv|arriv", r"return|refund|close|end"]):
            if re.search(pat, n):
                return r
        return None

    # -------------------------------------------------------- relationships
    def name_match(self, c, child, parent, p):
        nc, npk, sg = norm(c), norm(p), self.singular(parent.norm)
        if nc == npk:
            return True
        if len(npk) >= 4 and nc.endswith(npk):
            return True
        if npk == "id" and (nc == sg + "id" or nc.endswith(sg + "id")):
            return True
        return False

    def probe_fk(self, C, c, P, p, evidence):
        ck = C.key(c, "basic")
        pk = P.key(p, "basic", cache=True)
        pvals = pk.dropna().values
        nn = ck.notna()
        inpar = ck[nn].isin(pvals)
        orphan_rows = int((~inpar).sum())
        mode, before = "basic", None
        ck_use, pk_use = ck, pk
        if orphan_rows > 0:
            ck2, pk2 = C.key(c, "aggressive"), P.key(p, "aggressive")
            if pk2.dropna().is_unique:
                nn2 = ck2.notna()
                inpar2 = ck2[nn2].isin(pk2.dropna().values)
                if int((~inpar2).sum()) < orphan_rows:
                    before = {"orphan_rows": orphan_rows, "orphan_distinct": int(ck[nn][~inpar].nunique())}
                    mode, ck_use, pk_use, nn, inpar = "aggressive", ck2, pk2, nn2, inpar2
                    orphan_rows = int((~inpar).sum())
        valid = ck_use[nn][inpar]
        counts = valid.value_counts()
        parent_rows = int(pk_use.notna().sum())
        rec = {"child": C.name, "child_col": c, "parent": P.name, "parent_pk": p, "evidence": evidence,
               "child_rows": len(ck_use), "null_fk": int(len(ck_use) - nn.sum()),
               "distinct_fk": int(ck_use[nn].nunique()), "orphan_rows": orphan_rows,
               "orphan_distinct": int(ck_use[nn][~inpar].nunique()),
               "orphan_pct": round(100 * orphan_rows / max(int(nn.sum()), 1), 4),
               "key_mode": mode, "before_correction": before,
               "parent_rows": parent_rows, "parents_with_children": int(len(counts)),
               "parent_coverage_pct": round(100 * len(counts) / max(parent_rows, 1), 2),
               "children_per_parent_max": int(counts.max()) if len(counts) else 0,
               "children_per_parent_mean": round(float(counts.mean()), 3) if len(counts) else 0,
               "cardinality": "1:N" if len(counts) and counts.max() > 1 else "1:1 (or 1:0..1)"}
        if evidence.startswith("value-overlap"):
            rec["status"] = "UNRESOLVED"
        elif orphan_rows:
            rec["status"] = "UNRESOLVED"
        elif mode == "aggressive":
            rec["status"] = "CORRECTED"
        else:
            rec["status"] = "VERIFIED"
        if mode == "aggressive" and orphan_rows == 0:
            rec["status"] = "CORRECTED"
        return rec

    def find_fks(self):
        parents = [(P, P.pk[0]) for P in self.T.values() if P.pk and len(P.pk) == 1]
        best = {}
        for C in self.T.values():
            for c in C.df.columns:
                if C.kinds[c] not in ("id", "int", "text", "numeric_text"):
                    continue
                for P, p in parents:
                    if P is C:
                        continue
                    if self.name_match(c, C, P, p):
                        try:
                            rec = self.probe_fk(C, c, P, p, "name")
                        except Exception as e:
                            self.errors.append({"step": f"fk {C.name}.{c}->{P.name}", "error": repr(e)})
                            continue
                        k = (C.name, c)
                        cur = best.get(k)
                        if cur is None or (rec["orphan_pct"], -rec["parent_rows"]) < (cur["orphan_pct"], -cur["parent_rows"]):
                            if cur is not None:
                                rec.setdefault("alternatives", []).append(cur["parent"])
                            best[k] = rec
                        else:
                            cur.setdefault("alternatives", []).append(P.name)
        self.fks = list(best.values())
        # weak, value-overlap-only candidates: repeating id-like columns not matched by name
        matched = {(r["child"], r["child_col"]) for r in self.fks}
        for C in self.T.values():
            for c in C.df.columns:
                if (C.name, c) in matched or C.kinds[c] not in ("id", "int") or not ID_RE.search(c):
                    continue
                if C.pk and c in C.pk:
                    continue
                s = C.df[c]
                if s.nunique() < 50 or s.nunique() == len(s):
                    continue
                for P, p in parents:
                    if P is C or P.df[p].nunique() < 50:
                        continue
                    ck = C.key(c, "basic").dropna()
                    if ck.isin(P.key(p, "basic", cache=True).dropna().values).mean() >= 0.999:
                        self.weak_fks.append({"child": C.name, "child_col": c, "parent": P.name,
                                              "parent_pk": p, "evidence": "value-overlap only (weak)"})

    def fk(self, child, parent):
        if child is None or parent is None:
            return None
        cands = [r for r in self.fks if r["child"] == child.name and r["parent"] == parent.name]
        return cands[0] if cands else None

    def fk_child_keys(self, r):
        return self.T[r["child"]].key(r["child_col"], r["key_mode"], cache=True)

    def fk_parent_keys(self, r):
        return self.T[r["parent"]].key(r["parent_pk"], r["key_mode"], cache=True)

    # ---------------------------------------------------------------- roles
    def assign_roles(self):
        claimed = {}
        for t in self.T.values():
            for role, pat in ROLE_PATTERNS.items():
                if re.search(pat, t.norm):
                    claimed.setdefault(role, []).append(t)
                    break
        for role, tabs in claimed.items():
            tabs.sort(key=lambda t: (0 if t.norm in (role, role + "s", role[:-1]) else 1, -len(t.df)))
            self.roles[role] = tabs[0]
            if len(tabs) > 1:
                self.role_alts[role] = [t.name for t in tabs[1:]]
        for role, fname in TABLE_OVERRIDES.items():
            for t in self.T.values():
                if t.file.lower() == str(fname).lower():
                    self.roles[role] = t
        for role, t in self.roles.items():
            t.role = role
            self.mapping[f"{role} (table)"] = f"{t.file} ({len(t.df):,} rows)"
        for role in ("orders", "items", "payments"):
            if role not in self.roles:
                self.notes.append(f"Table for role '{role}' NOT detected - dependent metrics will be UNSUPPORTED "
                                  f"(set TABLE_OVERRIDES['{role}'] to fix).")

    def pick(self, role, field, want="num", exclude=None):
        t = self.roles.get(role)
        if t is None:
            return None
        fk = f"{role}.{field}"
        ov = COLUMN_OVERRIDES.get(fk)
        if ov and ov in t.df.columns:
            self.mapping[fk] = ov + " (override)"
            return ov

        def ok(k):
            if want == "num":
                return k in NUM_KINDS
            if want == "date":
                return k == "date"
            if want == "id":
                return k in ("id", "int", "text", "numeric_text")
            return k not in NUM_KINDS and k != "date"

        cols = [c for c in t.df.columns if ok(t.kinds[c])]
        for pat in FIELDS.get(fk, []):
            hits = [c for c in cols if re.search(pat, norm(c)) and not (exclude and re.search(exclude, norm(c)))]
            if hits:
                if len(hits) > 1:
                    self.ambiguity.append({"field": fk, "chosen": hits[0], "other_matches": hits[1:]})
                self.mapping[fk] = hits[0]
                return hits[0]
        return None

    def role_id(self, role):
        t = self.roles.get(role)
        if t is None:
            return None
        if t.pk and len(t.pk) == 1:
            self.mapping[f"{role}.id"] = t.pk[0] + " (detected PK)"
            return t.pk[0]
        return self.pick(role, "id", "id")

    def item_line_cols(self):
        I = self.roles.get("items")
        if I is None:
            return None
        if I.pk:
            return I.pk
        return None

    def returns_line_cols(self):
        R, I = self.roles.get("returns"), self.roles.get("items")
        if R is None or I is None or not I.pk:
            return None
        if len(I.pk) == 1:
            r = self.fk(R, I)
            return [r["child_col"]] if r else None
        cols = []
        for comp in I.pk:
            m = [c for c in R.df.columns if norm(c) == norm(comp)]
            if not m:
                return None
            cols.append(m[0])
        return cols

    # ---------------------------------------------------------------- utils
    def new(self, key, metric, grain, denom, source, fmt="int"):
        f = Finding(key, metric, grain, denom, source, fmt)
        self.F[key] = f
        return f

    def dev_fsum(self, f, series, primary):
        alt = fsum(series)
        f.dev("independent math.fsum recomputation", alt, abs(alt - primary) <= max(0.005, abs(primary) * 1e-9),
              f"fsum={alt:,.4f}")

    def numeric_risk(self, f, t, col, label):
        ni = t.num_info(col)
        f.chk(f"{label}: non-numeric values", ni["fail"] == 0, f"{ni['fail']:,} unparsable")
        if ni["cleaned"]:
            f.corrections.append(f"{t.name}.{col} was text with commas/currency; cleaned before summing "
                                 f"({ni['fail_naive']:,} unparsable -> {ni['fail']:,})")
        x = t.num(col)
        f.chk(f"{label}: null values", int(x.isna().sum()) == 0, f"{int(x.isna().sum()):,} nulls", material=False)

    # ================================================================ METRICS
    def m_total_orders(self):
        O = self.roles.get("orders")
        f = self.new("total_orders", "Total orders", "order", "n/a (count)", O.file if O else "?")
        idc = self.role_id("orders")
        if O is None or idc is None:
            return f.finish("orders table or its ID column not identified")
        k = O.key(idc, "basic")
        rows, nn, distinct = len(O.df), int(k.notna().sum()), int(k.nunique())
        f.result = distinct
        f.source = f"{O.file}.{idc}"
        f.calc = f"COUNT(DISTINCT {O.name}.{idc}); file rows={rows:,}"
        f.limit("Counts distinct order IDs; cancelled/test orders are not excluded (no rule in data).")
        f.chk("null order IDs", nn == rows, f"{rows - nn:,} null")
        f.chk("duplicate order IDs", nn == distinct, f"{nn - distinct:,} duplicate IDs")
        agg = int(O.key(idc, "aggressive").nunique())
        f.chk("IDs differing only by case/whitespace/leading zeros", agg == distinct,
              f"basic={distinct:,} vs normalised={agg:,}")
        raw = int(O.df[idc].nunique())
        f.dev("raw (unstripped) distinct count", raw, raw == distinct, f"raw={raw:,} vs stripped={distinct:,}")
        f.extra.update({"rows": rows, "distinct": distinct})
        self.V["total_orders"] = distinct
        f.finish()

    def m_orders_with_lines(self):
        O, I = self.roles.get("orders"), self.roles.get("items")
        f = self.new("orders_with_lines", "Orders containing order lines", "order", "total orders",
                     f"{I.file if I else '?'} -> {O.file if O else '?'}")
        f2 = self.new("lineless_orders", "Line-less orders (orders with no order line)", "order",
                      "total orders", f.source)
        r = self.fk(I, O)
        if r is None:
            f.finish("no foreign key from order lines to orders could be established")
            return f2.finish("no foreign key from order lines to orders could be established")
        ck, pk = self.fk_child_keys(r), self.fk_parent_keys(r)
        pset = pk.dropna().unique()
        lines_orders = ck[ck.notna() & ck.isin(pset)].nunique()
        total = int(pk.nunique())
        f.result, f2.result = int(lines_orders), int(total - lines_orders)
        f.source = f2.source = f"{I.name}.{r['child_col']} -> {O.name}.{r['parent_pk']}"
        f.calc = f"COUNT(DISTINCT {I.name}.{r['child_col']}) restricted to IDs present in {O.name}"
        f2.calc = f"COUNT(DISTINCT {O.name}.{r['parent_pk']}) - orders with lines (anti-join)"
        for g in (f, f2):
            g.limit(f"Lines-to-orders key mode: {r['key_mode']}; orphan line rows: {r['orphan_rows']:,}.")
            g.chk("orphan line rows (lines whose order is missing)", r["orphan_rows"] == 0,
                  f"{r['orphan_rows']:,} rows / {r['orphan_distinct']:,} distinct IDs")
            g.chk("null FK in order lines", r["null_fk"] == 0, f"{r['null_fk']:,}", material=False)
        if r["key_mode"] == "aggressive":
            for g in (f, f2):
                g.corrections.append(f"FK matched only after case/whitespace/leading-zero normalisation "
                                     f"(orphans before: {r['before_correction']})")
        anti = int((~pk.isin(ck.dropna().unique())).sum())
        f.dev("anti-join identity: with_lines + lineless == total", anti + lines_orders,
              anti + lines_orders == total, f"{lines_orders:,} + {anti:,} vs {total:,}")
        f2.dev("anti-join count (orders absent from lines)", anti, anti == f2.result, f"anti-join={anti:,}")
        self.V.update({"orders_with_lines": int(lines_orders), "lineless": int(total - lines_orders)})
        f.finish()
        f2.finish()

    def items_cols(self):
        I = self.roles.get("items")
        qc = self.pick("items", "qty", "num")
        pc = self.pick("items", "price", "num", PRICE_EXCL)
        ac = self.pick("items", "amount", "num", AMT_EXCL)
        dc = self.pick("items", "discount", "num")
        return I, qc, pc, ac, dc

    def line_scope_stats(self, I):
        """orphan-line mask + PK duplicate info shared by units / sales metrics."""
        O = self.roles.get("orders")
        r = self.fk(I, O)
        orphan = pd.Series(False, index=I.df.index)
        if r is not None:
            ck, pset = self.fk_child_keys(r), self.fk_parent_keys(r).dropna().unique()
            orphan = ck.notna() & ~ck.isin(pset)
        pkdup = 0
        if I.pk:
            pkdup = int(comp_key(I, I.pk).duplicated().sum())
        return orphan, pkdup

    def m_units(self):
        I, qc, pc, ac, dc = self.items_cols()
        f = self.new("units", "Units (sum of line quantity)", "order line", "n/a (sum)",
                     f"{I.file}.{qc}" if I and qc else "?")
        if I is None or qc is None:
            return f.finish("order-lines table or quantity column not identified")
        x = I.num(qc)
        total = float(x.sum())
        f.result = int(round(total)) if abs(total - round(total)) < 1e-6 else total
        f.calc = f"SUM({I.name}.{qc}) over all order-line rows"
        f.limit("Gross units ordered; returns/cancellations are not netted off.")
        self.numeric_risk(f, I, qc, "qty")
        f.chk("zero or negative quantities", int((x <= 0).sum()) == 0, f"{int((x <= 0).sum()):,}")
        f.chk("non-integer quantities", bool((x.dropna() == np.floor(x.dropna())).all()), "fractional units present")
        orphan, pkdup = self.line_scope_stats(I)
        f.chk("orphan lines (order missing)", int(orphan.sum()) == 0,
              f"{int(orphan.sum()):,} lines carrying {float(x[orphan].sum()):,.0f} units")
        f.chk("duplicate line keys", pkdup == 0, f"{pkdup:,} duplicated line keys")
        f.chk("fully duplicated line rows", int(I.df.duplicated().sum()) == 0,
              f"{int(I.df.duplicated().sum()):,} exact duplicate rows")
        self.dev_fsum(f, x, total)
        f.extra["units_excluding_orphan_lines"] = float(x[~orphan].sum())
        self.V["units"] = total
        f.finish()

    def m_gross_sales(self):
        I, qc, pc, ac, dc = self.items_cols()
        f = self.new("gross_sales", "Gross sales", "order line", "n/a (sum)", "?", "money")
        if I is None or (not (qc and pc) and not ac):
            return f.finish("need qty x unit-price or a line-amount column in the order-lines table")
        alts = {}
        v_qp = v_amt = None
        if qc and pc:
            prod = I.num(qc) * I.num(pc)
            v_qp = float(prod.sum())
            alts[f"SUM({qc} x {pc})"] = v_qp
        if ac:
            v_amt = float(I.num(ac).sum())
            alts[f"SUM({ac})"] = v_amt
        primary_name = f"SUM({qc} x {pc})" if v_qp is not None else f"SUM({ac})"
        f.result = v_qp if v_qp is not None else v_amt
        f.source = f"{I.file}.[{qc} x {pc}]" if v_qp is not None else f"{I.file}.{ac}"
        f.calc = f"{primary_name} over all order-line rows (gross = before any discount)"
        f.limit("Gross = qty x list price before discount; tax, freight and discount treatment unknown.")
        for col, lab in ((qc, "qty"), (pc, "price"), (ac, "amount")):
            if col:
                self.numeric_risk(f, I, col, lab)
        if v_qp is not None and v_amt is not None:
            prodv, amtv = (I.num(qc) * I.num(pc)), I.num(ac)
            tol = 0.51 if bool((amtv.dropna() == np.floor(amtv.dropna())).mean() > 0.99) else 0.011
            share = float(((prodv - amtv).abs() <= tol).mean())
            f.chk("line amount column == qty x price", share >= 0.999,
                  f"{share * 100:.2f}% of rows agree; SUM(qty x price)={v_qp:,.2f} vs SUM({ac})={v_amt:,.2f} "
                  f"(gross-sales definition is ambiguous)")
            f.extra["row_agreement_qty_price_vs_amount"] = share
        orphan, pkdup = self.line_scope_stats(I)
        base = I.num(qc) * I.num(pc) if v_qp is not None else I.num(ac)
        f.chk("orphan lines (order missing)", int(orphan.sum()) == 0,
              f"{int(orphan.sum()):,} lines worth {float(base[orphan].sum()):,.2f}")
        f.chk("duplicate line keys", pkdup == 0, f"{pkdup:,}")
        f.chk("negative line values", int((base < 0).sum()) == 0, f"{int((base < 0).sum()):,}")
        self.dev_fsum(f, base, f.result)
        f.extra["alternatives"] = alts
        self.V["gross_sales"] = f.result
        f.finish()

    def m_payments(self):
        P = self.roles.get("payments")
        ac = self.pick("payments", "amount", "num")
        f = self.new("payments", "Payments (sum of payment amount, all rows)", "payment row", "n/a (sum)",
                     f"{P.file}.{ac}" if P and ac else "?", "money")
        if P is None or ac is None:
            return f.finish("payments table or amount column not identified")
        x = P.num(ac)
        f.result = float(x.sum())
        f.calc = f"SUM({P.name}.{ac}) over all payment rows (no status filter)"
        f.limit("Recorded payment amounts, NOT proven cash receipts (see section 8); status filter not applied.")
        self.numeric_risk(f, P, ac, "amount")
        f.chk("negative payment amounts (refunds/reversals inside table?)", int((x < 0).sum()) == 0,
              f"{int((x < 0).sum()):,} rows totalling {float(x[x < 0].sum()):,.2f}")
        pkdup = int(comp_key(P, P.pk).duplicated().sum()) if P.pk else 0
        f.chk("duplicate payment keys", pkdup == 0, f"{pkdup:,}")
        f.chk("fully duplicated payment rows", int(P.df.duplicated().sum()) == 0, f"{int(P.df.duplicated().sum()):,}")
        O = self.roles.get("orders")
        r = self.fk(P, O)
        if r is not None:
            ck_, ps_ = self.fk_child_keys(r), self.fk_parent_keys(r).dropna().unique()
            orph_ = ck_.notna() & ~ck_.isin(ps_)
            f.chk("orphan payments (order missing)", r["orphan_rows"] == 0,
                  f"{r['orphan_rows']:,} rows worth {float(x[orph_].sum()):,.2f}")
            f.extra["orphan_payment_amount"] = float(x[orph_].sum())
            if r["key_mode"] == "aggressive":
                f.limit("payments->orders FK only resolves after case/space/leading-zero normalisation (see corrections).")
        else:
            f.limit("No payments -> orders foreign key established.")
        self.dev_fsum(f, x, f.result)
        alts = {"SUM of positive amounts only": float(x[x > 0].sum())}
        sc = self.pick("payments", "status", "text")
        if sc:
            st = P.df[sc].astype(str).str.strip()
            byst = {}
            for s, g in x.groupby(st):
                byst[str(s)] = float(g.sum())
            f.extra["by_status"] = byst
            if len(byst) <= 10:
                for s, v in byst.items():
                    alts[f"SUM where {sc}='{s}'"] = v
        f.extra["alternatives"] = alts
        f.extra["rows"] = len(P.df)
        self.V["payments"] = f.result
        f.finish()

    def m_refunds(self):
        f = self.new("refunds", "Refunds", "refund/return row", "n/a (sum)", "?", "money")
        cands = []   # (priority, label, value, rows)
        for t in self.T.values():
            for c in t.df.columns:
                if t.kinds[c] in NUM_KINDS and re.search(r"refund", norm(c)):
                    x = t.num(c)
                    cands.append((0 if t.role == "refunds" else 1, f"{t.name}.{c}", float(x.sum()), int(x.notna().sum())))
        R = self.roles.get("refunds")
        if R is not None and not any(c[1].startswith(R.name + ".") for c in cands):
            ac = self.pick("refunds", "amount", "num")
            if ac:
                x = R.num(ac)
                cands.append((0, f"{R.name}.{ac}", float(x.sum()), int(x.notna().sum())))
        P = self.roles.get("payments")
        alts = {}
        if P is not None:
            ac = self.pick("payments", "amount", "num")
            if ac:
                x = P.num(ac)
                v_neg = abs(float(x[x < 0].sum()))
                if v_neg:
                    alts["ABS(SUM of negative payments)"] = v_neg
                for c in P.df.columns:
                    if P.kinds[c] == "text" and P.df[c].nunique() <= 20:
                        m = P.df[c].astype(str).str.contains("refund", case=False, na=False)
                        if m.any() and float(x[m].abs().sum()):
                            alts[f"ABS(SUM payments where {c} contains 'refund')"] = float(x[m].abs().sum())
        cands.sort(key=lambda z: z[0])
        if cands:
            _, lab, val, rows = cands[0]
            f.result, f.source = val, lab
            f.calc = f"SUM({lab}) ({rows:,} non-null rows)"
            for _, l2, v2, _r in cands[1:]:
                if v2:
                    alts[f"SUM({l2})"] = v2
            f.limit("Refund column meaning (cash returned vs credit note) is not documented in the CSV.")
            f.chk("single unambiguous refund source", len(cands) == 1 and not alts,
                  f"{len(cands)} refund-named column(s); other non-zero candidates: {list(alts)[:4]}")
            T0 = self.T[lab.split(".")[0]]
            self.numeric_risk(f, T0, lab.split(".", 1)[1], "refund")
            x = T0.num(lab.split(".", 1)[1])
            f.chk("negative refund values", int((x < 0).sum()) == 0, f"{int((x < 0).sum()):,}")
            self.dev_fsum(f, x, val)
        else:
            f.limit("No refund-named numeric column or table found; alternatives listed only.")
        f.extra["alternatives"] = alts
        self.V["refunds"] = f.result
        f.finish("no refund amount column/table found" if not cands else None)

    def m_returns(self):
        R, I, O = self.roles.get("returns"), self.roles.get("items"), self.roles.get("orders")
        f = self.new("returned_lines", "Returned distinct order lines", "order line", "order lines",
                     R.file if R else "?")
        g = self.new("orders_with_returns", "Orders with returns", "order", "orders with lines", "?")
        if R is None or I is None:
            f.finish("returns or order-lines table not identified")
            return g.finish("returns or order-lines table not identified")
        lcols = self.returns_line_cols()
        ikeys = comp_key(I, I.pk, cache=False) if I.pk else None
        ret_line_rows = None
        if lcols and ikeys is not None:
            rk = comp_key(R, lcols)
            valid = rk.notna()
            inp = rk.isin(ikeys.dropna().unique())
            distinct = int(rk[valid & inp].nunique())
            orphan_rows = int((valid & ~inp).sum())
            f.result = distinct
            f.source = f"{R.name}.{'+'.join(lcols)} -> {I.name}.{'+'.join(I.pk)}"
            f.calc = f"COUNT(DISTINCT line key in {R.name}) that exists in {I.name}"
            f.limit("A line is 'returned' if it appears in the returns table; partial vs full returns not distinguished.")
            f.chk("return rows pointing to missing lines", orphan_rows == 0, f"{orphan_rows:,} rows")
            f.chk("multiple return rows per line (row count != distinct lines)", len(R.df) == distinct,
                  f"{len(R.df):,} return rows vs {distinct:,} distinct lines", material=False)
            if len(R.df) != distinct:
                f.limit(f"Returns table has {len(R.df):,} rows for {distinct:,} distinct lines: COUNT(*) on returns would overstate; distinct count used.")
            f.chk("null line key in returns", int((~valid).sum()) == 0, f"{int((~valid).sum()):,}", material=False)
            alt = int(rk.dropna().nunique())
            f.dev("distinct line keys incl. orphans", alt, alt == distinct, f"incl. orphans={alt:,}")
            f.extra.update({"return_rows": len(R.df), "line_pk": I.pk, "distinct_lines_in_items": int(ikeys.nunique())})
            self.V["returned_lines"] = distinct
            ret_line_rows = rk[valid & inp]
            f.finish()
        else:
            # fall back to a flag column inside order items
            flag = [c for c in I.df.columns if re.search(r"^(isreturned|returned|returnflag|hasreturn)$", norm(c))]
            if flag:
                fl = I.df[flag[0]].astype(str).str.strip().str.lower().isin(["1", "true", "yes", "y", "t"])
                f.result = int(fl.sum())
                f.source, f.calc = f"{I.name}.{flag[0]}", f"COUNT(rows where {flag[0]} is truthy)"
                f.limit("Based on a flag column, not the returns table.")
                f.finish()
            else:
                f.finish("cannot link returns to order lines (no line key/FK found)")
        # ---------------- orders with returns
        r_ro = self.fk(R, O)
        via_ord = via_line = None
        if r_ro is not None:
            ck, pset = self.fk_child_keys(r_ro), self.fk_parent_keys(r_ro).dropna().unique()
            via_ord = int(ck[ck.notna() & ck.isin(pset)].nunique())
        r_io = self.fk(I, O)
        if ret_line_rows is not None and r_io is not None and ikeys is not None:
            mp = pd.DataFrame({"lk": ikeys, "ok": self.fk_child_keys(r_io)}).dropna().drop_duplicates("lk")
            mp = mp.set_index("lk")["ok"]
            ok = ret_line_rows.map(mp)
            via_line = int(ok.dropna().nunique())
        g.result = via_ord if via_ord is not None else via_line
        if g.result is None:
            return g.finish("no route from returns to orders (no order FK in returns, no line link)")
        g.source = (f"{R.name}.{r_ro['child_col']} -> {O.name}" if via_ord is not None
                    else f"{R.name} -> {I.name} -> {O.name}")
        g.calc = ("COUNT(DISTINCT order ID in returns) present in orders" if via_ord is not None
                  else "COUNT(DISTINCT order of each returned line via order lines)")
        g.limit("An order counts as 'with returns' if any of its lines/returns appears in the returns table.")
        if via_ord is not None and via_line is not None:
            g.dev("alternative route via returned lines -> order lines -> orders", via_line, via_ord == via_line,
                  f"direct={via_ord:,} vs via-lines={via_line:,}")
            if r_ro is not None and ret_line_rows is not None and ikeys is not None and r_io is not None:
                mp2 = pd.DataFrame({"lk": ikeys, "ok": self.fk_child_keys(r_io)}).dropna().drop_duplicates("lk").set_index("lk")["ok"]
                rk_all = comp_key(R, lcols)
                mism = int(((rk_all.map(mp2) != self.fk_child_keys(r_ro)) & rk_all.notna() & self.fk_child_keys(r_ro).notna()).sum())
                g.chk("returns.order_id agrees with the order of the returned line", mism == 0, f"{mism:,} conflicting rows")
        elif via_ord is not None or via_line is not None:
            g.dev("second independent route", None, True, "only one route available - no independent cross-check")
            g.limit("Only one route available; not cross-validated.")
        if r_ro is not None:
            g.chk("return rows pointing to missing orders", r_ro["orphan_rows"] == 0, f"{r_ro['orphan_rows']:,}")
        self.V["orders_with_returns"] = g.result
        g.finish()

    def m_orders_2024(self):
        O = self.roles.get("orders")
        dc = self.pick("orders", "date", "date")
        f = self.new("orders_2024", "2024 orders", "order", "total orders",
                     f"{O.file}.{dc}" if O and dc else "?")
        idc = self.role_id("orders")
        if O is None or dc is None or idc is None:
            return f.finish("orders table, ID column or order-date column not identified")
        p, info = O.dates(dc), O.date_info(dc)
        k = O.key(idc, "basic")
        yr = p.dt.year
        f.result = int(k[yr == 2024].nunique())
        f.calc = f"COUNT(DISTINCT {idc}) where YEAR({dc}) = 2024"
        f.limit("Depends on the chosen order-date column; time-of-day/timezone not considered.")
        yc = yr.value_counts().sort_index()
        f.extra["orders_by_year"] = {int(a): int(b) for a, b in yc.items()}
        f.extra["date_range"] = [str(p.min()), str(p.max())]
        f.chk("unparseable order dates", info["failed"] == 0, f"{info['failed']:,} unparsed, e.g. {info['failed_examples'][:3]}")
        if info["corrected"]:
            f.corrections.append(f"{dc}: default parser failed on {info['failed_default']:,} values; "
                                 f"format=mixed reduced to {info['failed']:,}")
        f.chk("null order dates", int(p.isna().sum()) == 0, f"{int(p.isna().sum()):,}", material=False)
        f.chk("dd/mm vs mm/dd ambiguity (matters only for month/day, not year)", True,
              f"ambiguous={info['ambiguous_dayfirst']}, inconsistent={info['inconsistent_slash_order']}")
        others = {}
        for c in O.df.columns:
            if c != dc and O.kinds[c] == "date":
                others[c] = int(k[O.dates(c).dt.year == 2024].nunique())
        f.chk("other date columns give a different 2024 count", all(v == f.result for v in others.values()),
              f"alternatives: {others}", material=bool(others))
        f.extra["other_date_columns_2024"] = others
        raw = O.df[dc].astype(str)
        y = raw.str.extract(r"^\s*(\d{4})[-/.]")[0]
        if y.notna().mean() < 0.5:
            y = raw.str.extract(r"[-/. ](\d{4})(?:[ T,].*)?$")[0]
        alt = int(k[(y == "2024").values].nunique())
        f.dev("regex year extraction (no date parsing)", alt, alt == f.result, f"regex={alt:,} vs parsed={f.result:,}")
        self.V["orders_2024"] = f.result
        f.finish()

    def m_rates(self):
        f = self.new("line_return_rate", "Line-level return rate", "order line", "distinct order lines", "?", "pct")
        g = self.new("order_return_rate", "Order-level return rate", "order", "orders containing order lines",
                     "?", "pct")
        rl = self.F.get("returned_lines")
        if rl is not None and rl.result is not None and rl.extra.get("distinct_lines_in_items"):
            den = rl.extra["distinct_lines_in_items"]
            f.result = rl.result / den
            f.source = "returned_lines / distinct order-line keys"
            f.calc = f"{rl.result:,} / {den:,}"
            f.extra.update({"numerator": rl.result, "denominator": den})
            f.limit("Row-count based; not weighted by units or value. Returns after the data extraction date unseen.")
            f.chk("returned-lines metric itself is clean", rl.status in ("VERIFIED", "CORRECTED"),
                  f"depends on returned_lines = {rl.status}")
            f.finish()
        else:
            f.finish("returned lines or line denominator not computable")
        ow, rr = self.F.get("orders_with_lines"), self.F.get("orders_with_returns")
        if ow is not None and rr is not None and ow.result and rr.result is not None:
            g.result = rr.result / ow.result
            g.source = "orders_with_returns / orders_with_lines"
            g.calc = f"{rr.result:,} / {ow.result:,}"
            tot = self.V.get("total_orders")
            g.extra.update({"numerator": rr.result, "denominator": ow.result,
                            "alt_rate_on_all_orders": (rr.result / tot) if tot else None})
            g.limit("Primary denominator = orders that have lines; rate on ALL orders shown in extra as an alternative.")
            g.chk("numerator metric is clean", rr.status in ("VERIFIED", "CORRECTED"), f"depends on orders_with_returns = {rr.status}")
            g.chk("denominator metric is clean", ow.status in ("VERIFIED", "CORRECTED"), f"depends on orders_with_lines = {ow.status}")
            g.dev("rate recomputed with all-order denominator", g.extra["alt_rate_on_all_orders"], True,
                  "informational: denominator choice changes the rate")
            g.finish()
        else:
            g.finish("orders-with-returns or orders-with-lines not computable")

    # ============================================================== FAN-OUT
    def fanout_analysis(self):
        O, I, P = self.roles.get("orders"), self.roles.get("items"), self.roles.get("payments")
        out = {"primary_demo": None, "generic": []}
        r_io, r_po = self.fk(I, O), self.fk(P, O)
        _, qc, pc, ac, _d = self.items_cols()
        pay_ac = self.pick("payments", "amount", "num")
        if O is None or r_io is None or r_po is None or pay_ac is None or (ac is None and not (qc and pc)):
            out["primary_demo"] = "NOT TESTABLE: need orders, order-lines (with amount or qty x price) and payments with FKs to orders."
        else:
            line_val = I.num(ac) if ac else I.num(qc) * I.num(pc)
            unit_val = I.num(qc) if qc else None
            pay_val = P.num(pay_ac)
            ik, pk_ = self.fk_child_keys(r_io), self.fk_child_keys(r_po)
            okeys = self.fk_parent_keys(r_io).dropna()
            base = pd.DataFrame(index=pd.Index(okeys.unique()))
            base["lines"] = ik.value_counts().reindex(base.index).fillna(0)
            base["pays"] = pk_.value_counts().reindex(base.index).fillna(0)
            base["line_amt"] = line_val.groupby(ik).sum().reindex(base.index).fillna(0)
            base["pay_amt"] = pay_val.groupby(pk_).sum().reindex(base.index).fillna(0)
            if unit_val is not None:
                base["units"] = unit_val.groupby(ik).sum().reindex(base.index).fillna(0)
            nl, npay = base["lines"].clip(lower=1), base["pays"].clip(lower=1)
            rows = float((nl * npay).sum())
            correct = {"orders (COUNT DISTINCT)": len(base), "line amount": float(base["line_amt"].sum()),
                       "payments": float(base["pay_amt"].sum())}
            naive = {"orders (COUNT(*) after joins)": rows,
                     "line amount": float((base["line_amt"] * npay).sum()),
                     "payments": float((base["pay_amt"] * nl).sum())}
            if unit_val is not None:
                correct["units"] = float(base["units"].sum())
                naive["units"] = float((base["units"] * npay).sum())
            demo = {"scope": "orders LEFT JOIN order_lines LEFT JOIN payments (only orders that exist in orders)",
                    "line_amount_source": ac or f"{qc} x {pc}", "payment_amount_source": pay_ac,
                    "orders_with_multiple_lines": int((base["lines"] > 1).sum()),
                    "orders_with_multiple_payments": int((base["pays"] > 1).sum()),
                    "table_level_totals_for_reference": {
                        "line_amount_all_rows": float(line_val.sum()), "payments_all_rows": float(pay_val.sum())},
                    "correct_aggregation": correct, "naive_join_aggregation": naive, "materialized_join_check": None}
            demo["inflation_ratio"] = {k2: (naive[a] / correct[b] if correct[b] else None)
                                       for k2, (a, b) in {"orders": ("orders (COUNT(*) after joins)", "orders (COUNT DISTINCT)"),
                                                          "line amount": ("line amount", "line amount"),
                                                          "payments": ("payments", "payments")}.items()}
            if rows <= 25_000_000:      # devil's advocate: actually perform the join
                a = pd.DataFrame({"k": okeys.unique()})
                b = pd.DataFrame({"k": ik, "la": line_val.values}).dropna(subset=["k"])
                c = pd.DataFrame({"k": pk_, "pa": pay_val.values}).dropna(subset=["k"])
                m = a.merge(b, on="k", how="left").merge(c, on="k", how="left")
                mj = {"joined_rows": len(m), "sum_line_amount": float(m["la"].sum()), "sum_payments": float(m["pa"].sum())}
                mj["agrees_with_analytic_naive"] = (len(m) == int(rows)
                                                    and abs(mj["sum_line_amount"] - naive["line amount"]) <= max(1, abs(naive["line amount"]) * 1e-9)
                                                    and abs(mj["sum_payments"] - naive["payments"]) <= max(1, abs(naive["payments"]) * 1e-9))
                demo["materialized_join_check"] = mj
            else:
                demo["materialized_join_check"] = f"skipped: {rows:,.0f} joined rows exceeds safety limit"
            out["primary_demo"] = demo
            # order-level reconciliation of line value vs payments
            diff = (base["pay_amt"] - base["line_amt"])
            out["order_level_line_vs_payment"] = {
                "orders_with_lines_and_payments": int(((base["lines"] > 0) & (base["pays"] > 0)).sum()),
                "orders_with_lines_no_payments": int(((base["lines"] > 0) & (base["pays"] == 0)).sum()),
                "orders_with_payments_no_lines": int(((base["lines"] == 0) & (base["pays"] > 0)).sum()),
                "orders_payments_equal_line_value(|d|<=1)": int(((diff.abs() <= 1) & (base["lines"] > 0) & (base["pays"] > 0)).sum()),
                "orders_payments_below_line_value": int(((diff < -1) & (base["lines"] > 0) & (base["pays"] > 0)).sum()),
                "orders_payments_above_line_value": int(((diff > 1) & (base["lines"] > 0) & (base["pays"] > 0)).sum()),
                "note": "Discounts, tax, freight and part-payments make exact equality NOT expected; interpretation requires documentation."}
        # generic 1:N inflation for parent amount-like columns
        for r in self.fks:
            if r["cardinality"] != "1:N":
                continue
            Pt, Ct = self.T[r["parent"]], self.T[r["child"]]
            pk = self.fk_parent_keys(r)
            cnt = self.fk_child_keys(r).value_counts()
            mult = pk.map(cnt).fillna(1).clip(lower=1)
            for c in Pt.df.columns:
                if Pt.kinds[c] in NUM_KINDS and re.search(r"amount|total|price|cost|value|sales|revenue|balance|budget", norm(c)):
                    x = Pt.num(c)
                    s0 = float(x.sum())
                    if s0:
                        out["generic"].append({"parent": Pt.name, "child": Ct.name, "parent_amount_col": c,
                                               "parent_rows": len(Pt.df), "rows_after_left_join": int(mult.sum()),
                                               "row_multiplication": round(float(mult.sum()) / len(Pt.df), 4),
                                               "sum_correct": s0, "sum_after_naive_join": float((x * mult).sum()),
                                               "inflation_ratio": round(float((x * mult).sum()) / s0, 4)})
        out["generic"].sort(key=lambda d: -d["inflation_ratio"])
        out["generic"] = out["generic"][:15]
        out["one_to_many_relationships"] = [
            {"parent": r["parent"], "child": r["child"], "max_children": r["children_per_parent_max"],
             "mean_children": r["children_per_parent_mean"]} for r in self.fks if r["cardinality"] == "1:N"]
        self.fanout = out

    # ============================================================ DISCOUNTS
    def discount_analysis(self):
        res = {"columns": [], "documentation_evidence": [], "overall_unit_status": "UNKNOWN", "hypothetical": None}
        for p in self.docs:
            if p.suffix.lower() in (".txt", ".md", ".json", ".yaml", ".yml", ".sql", ".rtf"):
                try:
                    txt = p.read_text(encoding="utf-8", errors="replace")[:400000]
                    lines = [ln.strip() for ln in txt.splitlines() if re.search(r"disc", ln, re.I)][:8]
                    res["documentation_evidence"].append({"file": p.name, "discount_mentions": lines})
                except Exception:
                    pass
            else:
                res["documentation_evidence"].append({"file": p.name, "discount_mentions": ["binary/office file - NOT parsed, check manually"]})
        for t in self.T.values():
            for c in t.df.columns:
                if not re.search(r"disc|rebate|markdown", norm(c)) or t.kinds[c] not in NUM_KINDS:
                    continue
                x = t.num(c)
                v = x.dropna()
                if v.empty:
                    continue
                raw = t.df[c]
                pct_sign = int(raw.astype(str).str.contains("%", regex=False).sum()) if is_texty(raw) else 0
                vc = v.round(6).value_counts()
                info = {"table": t.name, "column": c, "rows": len(x), "nulls": int(x.isna().sum()),
                        "distinct": int(v.nunique()), "min": float(v.min()), "max": float(v.max()),
                        "mean": float(v.mean()), "median": float(v.median()),
                        "quantiles": {q: float(v.quantile(q)) for q in (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99)},
                        "zeros": int((v == 0).sum()), "zero_share": float((v == 0).mean()),
                        "negatives": int((v < 0).sum()), "gt_1": int((v > 1).sum()), "gt_100": int((v > 100).sum()),
                        "all_within_0_1": bool(v.between(0, 1).all()), "all_within_0_100": bool(v.between(0, 100).all()),
                        "all_integer_valued": bool((v == np.floor(v)).all()),
                        "top_values": {str(k): int(n) for k, n in vc.head(15).items()},
                        "percent_sign_in_raw_values": pct_sign,
                        "name_hint": ("percent-like name" if re.search(r"pct|percent|perc", norm(c))
                                      else "amount-like name" if re.search(r"amt|amount|value|inr|rs", norm(c)) else "no unit hint in name"),
                        "arithmetic_tests": None, "unit_status": "UNKNOWN", "unit_basis": "distribution alone cannot establish the unit"}
                # arithmetic reconciliation against qty, price, line amount in the same table
                qc = next((k for k in t.df.columns if t.kinds[k] in NUM_KINDS and re.search(r"^(quantity|qty|units|orderqty)$", norm(k))), None)
                pcs = [k for k in t.df.columns if t.kinds[k] in NUM_KINDS and re.search(r"price|rate", norm(k)) and not re.search(PRICE_EXCL, norm(k))]
                acs = [k for k in t.df.columns if t.kinds[k] in NUM_KINDS and re.search(r"^(linetotal|lineamount|netamount|netsales|itemtotal|extendedprice|amount|total|sales|revenue|value)$", norm(k)) and k != c]
                if qc and pcs and acs:
                    q_, p_, a_ = t.num(qc), t.num(pcs[0]), t.num(acs[0])
                    m = (x > 0) & (q_ > 0) & (p_ > 0) & a_.notna()
                    n = int(m.sum())
                    if n >= 100:
                        integer_amounts = bool((a_[m] == np.floor(a_[m])).mean() > 0.99)
                        tol = 0.51 if integer_amounts else 0.011
                        base = q_[m] * p_[m]
                        d, a = x[m], a_[m]
                        hyp = {"amount = qty*price*(1-d/100)  [d is percent]": base * (1 - d / 100),
                               "amount = qty*price*(1-d)  [d is fraction]": base * (1 - d),
                               "amount = qty*price - d  [d is currency per line]": base - d,
                               "amount = qty*(price - d)  [d is currency per unit]": q_[m] * (p_[m] - d),
                               "amount = qty*price  [discount NOT reflected in this amount column]": base}
                        shares = {h: float(((e - a).abs() <= tol).mean()) for h, e in hyp.items()}
                        info["arithmetic_tests"] = {"amount_column": acs[0], "price_column": pcs[0], "qty_column": qc,
                                                    "rows_tested(discount>0)": n, "tolerance": tol, "share_of_rows_matching": shares}
                        disc_hyps = {h: s for h, s in shares.items() if "NOT reflected" not in h}
                        top = max(disc_hyps, key=disc_hyps.get)
                        second = sorted(disc_hyps.values())[-2]
                        if disc_hyps[top] >= 0.99 and second <= 0.5 and shares[[h for h in shares if 'NOT reflected' in h][0]] < 0.5:
                            info["unit_status"] = "INFERRED_FROM_ARITHMETIC"
                            info["unit_basis"] = f"{disc_hyps[top] * 100:.2f}% of discounted rows satisfy: {top}"
                        elif shares[[h for h in shares if 'NOT reflected' in h][0]] >= 0.99:
                            info["unit_basis"] = "amount column ignores the discount, so it cannot reveal the unit"
                        else:
                            info["unit_basis"] = "no single arithmetic hypothesis explains the amount column - unit remains UNKNOWN"
                if pct_sign and pct_sign >= 0.5 * len(v):
                    info["unit_status"] = "INFERRED_FROM_RAW_TEXT"
                    info["unit_basis"] = "raw cells contain a '%' sign"
                res["columns"].append(info)
        doc_hits = [e for e in res["documentation_evidence"] if e["discount_mentions"] and "NOT parsed" not in str(e["discount_mentions"])]
        if doc_hits:
            for c in res["columns"]:
                if c["unit_status"] == "UNKNOWN":
                    c["unit_basis"] += ("; documentation file(s) mention discounts (" + ", ".join(e["file"] for e in doc_hits)
                                        + ") - review the quoted lines below; NOT auto-accepted as proof")
        if any(c["unit_status"] != "UNKNOWN" for c in res["columns"]):
            res["overall_unit_status"] = "; ".join(f"{c['table']}.{c['column']}: {c['unit_status']}"
                                                   for c in res["columns"] if c["unit_status"] != "UNKNOWN")
        # hypothetical discounted revenue (labelled assumption)
        I, qc, pc, ac, dc = self.items_cols()
        if I is not None and qc and pc and dc:
            d = I.num(dc)
            v = d.dropna()
            base = I.num(qc) * I.num(pc)
            hyp = {"label": "ASSUMPTION - discount interpreted as percentage", "discount_column": f"{I.name}.{dc}",
                   "gross_sales": float(base.sum())}
            if len(v) and v.min() >= 0 and v.max() <= 100:
                net = float((base * (1 - d.fillna(0) / 100)).sum())
                hyp.update({"hypothetical_discounted_revenue": net,
                            "implied_total_discount": hyp["gross_sales"] - net,
                            "implied_discount_pct_of_gross": (hyp["gross_sales"] - net) / hyp["gross_sales"] if hyp["gross_sales"] else None})
                if v.max() <= 1:
                    hyp["warning"] = ("all values are within 0-1: read as percent this is at most 1% off; the column may "
                                      "instead be a FRACTION - do not trust this figure until the unit is confirmed")
            else:
                hyp["hypothetical_discounted_revenue"] = None
                hyp["warning"] = "values fall outside 0-100 (min %.4g, max %.4g): percentage reading NOT applied" % (v.min(), v.max())
            res["hypothetical"] = hyp
        self.discount = res

    # ========================================================= RECONCILIATION
    @staticmethod
    def classify(value, ref, approx=False, tol_abs=0.5):
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return "NOT TESTABLE"
        diff = abs(value - ref)
        rel = diff / abs(ref) if ref else (0 if diff == 0 else float("inf"))
        if approx:
            return "MATCH" if rel <= 0.005 else "CLOSE" if rel <= 0.05 else "DIFFERENT"
        if diff <= tol_abs:
            return "MATCH"
        return "CLOSE" if rel <= 0.01 else "DIFFERENT"

    def reconcile(self):
        rows = []
        for key, label, ref, approx, kind in REFERENCES:
            f = self.F.get(key)
            comp = f.result if f is not None else None
            row = {"reference_key": key, "metric": label, "role": "primary", "reference_value": ref,
                   "computed_value": comp, "abs_diff": None if comp is None else comp - ref,
                   "pct_diff": None if comp is None or not ref else 100 * (comp - ref) / ref,
                   "status": self.classify(comp, ref, approx),
                   "definition": (f.calc if f is not None else "metric not computed"),
                   "metric_audit_status": f.status if f is not None else "UNSUPPORTED"}
            rows.append(row)
            if f is not None:
                for name, val in f.extra.get("alternatives", {}).items():
                    if val is None:
                        continue
                    rows.append({"reference_key": key, "metric": label, "role": "alternative (informational)",
                                 "reference_value": ref, "computed_value": val, "abs_diff": val - ref,
                                 "pct_diff": 100 * (val - ref) / ref if ref else None,
                                 "status": self.classify(val, ref, approx), "definition": name,
                                 "metric_audit_status": "n/a"})
        hyp = self.discount.get("hypothetical") if self.discount else None
        if hyp and hyp.get("hypothetical_discounted_revenue") is not None:
            v = hyp["hypothetical_discounted_revenue"]
            ref = 3827746136
            rows.append({"reference_key": "gross_sales", "metric": "Gross sales", "role": "alternative (informational, ASSUMPTION)",
                         "reference_value": ref, "computed_value": v, "abs_diff": v - ref, "pct_diff": 100 * (v - ref) / ref,
                         "status": self.classify(v, ref), "definition": hyp["label"], "metric_audit_status": "n/a"})
        ow, rr = 259233, 27792
        rate_ref = rr / ow
        g = self.F.get("order_return_rate")
        comp = g.result if g else None
        rows.append({"reference_key": "order_return_rate", "metric": "Order-level return rate (derived from reference 27,792 / 259,233)",
                     "role": "derived", "reference_value": rate_ref, "computed_value": comp,
                     "abs_diff": None if comp is None else comp - rate_ref,
                     "pct_diff": None if comp is None else 100 * (comp - rate_ref) / rate_ref,
                     "status": self.classify(comp, rate_ref, False, 5e-5),
                     "definition": g.calc if g else "not computed", "metric_audit_status": g.status if g else "UNSUPPORTED"})
        rows.append({"reference_key": "reference_internal_check", "metric": "259,233 + 40,767 == 300,000 (reference self-consistency)",
                     "role": "reference_check", "reference_value": 300000, "computed_value": ow + 40767,
                     "abs_diff": ow + 40767 - 300000, "pct_diff": 0.0,
                     "status": "MATCH" if ow + 40767 == 300000 else "DIFFERENT", "definition": "arithmetic on the reference values only",
                     "metric_audit_status": "n/a"})
        self.recon = rows

    # ================================================ UNSUPPORTED ANALYTICS
    def hits(self, pattern, kinds=None, exclude=None):
        out = []
        for t in self.T.values():
            for c in t.df.columns:
                n = norm(c)
                if re.search(pattern, n) and not (exclude and re.search(exclude, n)):
                    if kinds is None or t.kinds[c] in kinds:
                        out.append(f"{t.name}.{c}")
        return out

    def unsupported_analytics(self):
        A = []

        def add(name, verdict, found, missing, why):
            A.append({"analytic": name, "verdict": verdict, "related_columns_found": found[:12],
                      "required_but_missing": missing, "reasoning": why})

        # inventory turnover
        onhand = self.hits(r"onhand|stock|inventory|qtyavailable|availableqty|reorder", NUM_KINDS)
        cost = self.hits(r"cogs|costprice|unitcost|standardcost|purchaseprice|purchasecost|landed", NUM_KINDS)
        snap = {}
        for full in onhand:
            tn, cn = full.split(".", 1)
            t = self.T[tn]
            dcs = [c for c in t.df.columns if t.kinds[c] == "date"]
            snap[tn] = {c: int(t.dates(c).nunique()) for c in dcs}
        multi = any(v > 1 for d in snap.values() for v in d.values())
        if not onhand:
            add("Inventory turnover", "UNSUPPORTED", [], ["stock/on-hand quantity"], "No stock-level column exists.")
        elif not multi:
            add("Inventory turnover", "UNSUPPORTED", onhand, ["multiple dated stock snapshots (average inventory)", "COGS or a costing basis"],
                f"Stock columns exist but no time series of snapshots (distinct snapshot dates: {snap}); turnover needs average inventory over a period.")
        else:
            add("Inventory turnover", "UNRESOLVED", onhand + cost, ["confirmation that snapshots are regular and complete", "COGS/cost basis for value-based turnover"],
                f"Snapshot dates exist ({snap}) so a unit-based turnover might be attempted, but regularity/completeness is unverified.")
        # profit / margin / COGS
        cg = self.hits(r"cogs|costprice|unitcost|standardcost|purchaseprice|purchasecost|landed|margin|profit", NUM_KINDS)
        gen_cost = self.hits(r"cost", NUM_KINDS, r"ship|freight|deliver|logist|cogs|costprice|unitcost")
        if not cg:
            add("Profit / margin / COGS", "UNSUPPORTED", gen_cost, ["product or line-level cost of goods", "cost effective dates"],
                "No cost-of-goods / margin column; generic 'cost' columns (if listed) are not shown to be COGS." if gen_cost else "No cost or margin column exists.")
        else:
            add("Profit / margin / COGS", "UNRESOLVED", cg, ["documentation that this is per-unit COGS in the same currency", "effective-dating of cost"],
                "A cost-like column exists, but its meaning, currency and time validity are not evidenced by the CSV; not inferred.")
        # delivery time
        dv = self.hits(r"deliver|receiv|arriv", ("date",), r"expect|estimat|promis|eta|due|planned|scheduled")
        sh = self.hits(r"ship|dispatch", ("date",))
        if not dv:
            add("Delivery time", "UNSUPPORTED", sh + self.hits(r"expect|estimat|promis|eta|due", ("date",)),
                ["actual delivered/received date"], "No actual delivery date column (expected/estimated/ship dates are not delivery).")
        else:
            add("Delivery time", "UNRESOLVED", dv + sh, ["confirmation that the date is the actual delivery, and its time zone"],
                "Delivery-like date column exists; ordering vs ship date must be validated before use.")
        # employee performance
        emp_t = self.roles.get("employees")
        link = [f"{r['child']}.{r['child_col']}" for r in self.fks if emp_t is not None and r["parent"] == emp_t.name]
        emp_cols = self.hits(r"employee|salesrep|salesperson|agentid|staffid")
        perf = self.hits(r"target|quota|kpi|rating|performance|commission", NUM_KINDS)
        if emp_t is None and not emp_cols:
            add("Employee performance", "UNSUPPORTED", [], ["employee dimension", "employee-to-transaction link"], "No employee table or employee columns.")
        elif not link and not [c for c in emp_cols if not c.startswith((emp_t.name if emp_t else "~") + ".")]:
            add("Employee performance", "UNSUPPORTED", emp_cols, ["employee foreign key on orders/lines/returns/payments"],
                "An employee table exists but no transactional table links to it, so no sales can be attributed to anyone.")
        else:
            add("Employee performance", "UNRESOLVED", list(dict.fromkeys(link + emp_cols + perf)), ["attribution rules (who is credited)", "targets/quotas or hours to normalise"],
                "A link exists, but credit assignment and any performance baseline are undocumented; raw volume per employee is not 'performance'.")
        # promotion ROI
        pr_link = self.hits(r"promo|campaign|coupon")
        pr_cost = self.hits(r"promo.*(cost|budget|spend)|campaign.*(cost|budget|spend)|(budget|spend|marketingcost)", NUM_KINDS)
        if not pr_link:
            add("Promotion ROI", "UNSUPPORTED", [], ["promotion definitions", "link to orders/lines", "promotion cost", "baseline"], "No promotion columns at all.")
        elif not pr_cost:
            add("Promotion ROI", "UNSUPPORTED", pr_link, ["promotion cost/budget/spend", "baseline/counterfactual sales"],
                "ROI = incremental margin / spend; no spend column and no baseline, so ROI cannot be computed (descriptive volumes only).")
        else:
            add("Promotion ROI", "UNRESOLVED", pr_link + pr_cost, ["baseline/counterfactual", "confirmation the cost column is total spend"],
                "Spend exists but incremental effect needs a baseline the data does not provide.")
        # signup cohorts
        C, O = self.roles.get("customers"), self.roles.get("orders")
        sc = self.pick("customers", "signup", "date") if C is not None else None
        if C is None or sc is None:
            add("Customer signup cohorts", "UNSUPPORTED", self.hits(r"customer.*(date|created)"), ["customer signup/registration date"],
                "No signup-like date on the customers table. (A first-order cohort is possible but is a different construct.)")
        else:
            detail = "no orders link to test plausibility"
            verdict, missing = "UNRESOLVED", ["documentation that this date is the true signup date"]
            r = self.fk(O, C) if O is not None else None
            oc = self.pick("orders", "date", "date") if O is not None else None
            if r is not None and oc:
                first = pd.DataFrame({"k": self.fk_child_keys(r), "d": O.dates(oc)}).dropna().groupby("k")["d"].min()
                ck = C.key(r["parent_pk"], r["key_mode"])
                sdate = pd.Series(C.dates(sc).values, index=ck.values)
                sdate = sdate[~sdate.index.isna()]
                sdate = sdate[~sdate.index.duplicated()]
                j = pd.DataFrame({"signup": sdate, "first": first}).dropna()
                bad = int((j["signup"] > j["first"]).sum())
                detail = f"{bad:,} of {len(j):,} customers have signup date AFTER their first order ({100 * bad / max(len(j), 1):.2f}%)"
                if bad / max(len(j), 1) > 0.005:
                    missing.append("signup dates consistent with first orders")
            add("Customer signup cohorts", "UNRESOLVED", [f"{C.name}.{sc}"], missing,
                f"Signup-like column exists; plausibility test: {detail}. Name alone is not proof of meaning.")
        # cash received
        P = self.roles.get("payments")
        stc = self.pick("payments", "status", "text") if P is not None else None
        if P is None:
            add("Cash received", "UNSUPPORTED", [], ["payments table"], "No payments table.")
        elif stc is None:
            add("Cash received", "UNSUPPORTED", self.hits(r"payment"), ["payment status / settlement evidence"],
                "Payment amounts exist but nothing shows they cleared/settled; recorded amount is not cash received.")
        else:
            vals = P.df[stc].astype(str).str.strip().value_counts().head(10).to_dict()
            add("Cash received", "UNRESOLVED", [f"{P.name}.{stc}"], ["definition of which statuses mean cash actually received", "settlement/bank date"],
                f"Status values present ({vals}) but their meaning is undocumented; 'success' is not proof of settlement.")
        # market conditions
        ext = self.hits(r"market|competitor|inflation|gdp|benchmark|forecast|exchange|indexvalue|macro|seasonalindex")
        latest = None
        for t in self.T.values():
            for c in t.df.columns:
                if t.kinds[c] == "date":
                    m = t.dates(c).max()
                    if pd.notna(m) and (latest is None or m > latest):
                        latest = m
        if latest is None:
            stale = "no dates found"
        else:
            gap = (pd.Timestamp(TODAY) - latest).days
            stale = (f"latest date anywhere in the data = {str(latest)[:10]} "
                     + (f"({gap:,} days before today)" if gap >= 0 else f"(that is {-gap:,} days IN THE FUTURE - suspicious)"))
        add("Current market conditions", "UNSUPPORTED", ext, ["external market/competitor/macro data", "data recent enough to be 'current'"],
            f"The CSVs are internal transactions only; {stale}. Column-name hits (if any) are not external market data.")
        self.analytics = A

    # ================================================================ OUTPUT
    def finalize_integrity(self):
        after = {}
        for name in self.integrity.get("before", {}):
            try:
                after[name] = sha256_file(self.data_dir / name)
            except Exception as e:
                after[name] = f"ERROR {e!r}"
        self.integrity["after"] = after
        self.integrity["unchanged"] = after == self.integrity.get("before")

    def all_corrections(self):
        out = []
        for t in self.T.values():
            out.extend(t.corrections)
        for r in self.fks:
            if r["key_mode"] == "aggressive":
                out.append({"table": r["child"], "column": r["child_col"], "type": "fk_key_normalisation",
                            "before": f"{r['before_correction']['orphan_rows']:,} orphan rows",
                            "after": f"{r['orphan_rows']:,} orphan rows after case/space/leading-zero normalisation vs {r['parent']}.{r['parent_pk']}"})
        return out

    def material_discrepancies(self):
        m = []
        for row in self.recon:
            if row["role"] == "primary" and row["status"] == "DIFFERENT":
                m.append(f"Reference mismatch: {row['metric']} computed {fmt_num_any(row['computed_value'])} vs reference {fmt_num_any(row['reference_value'])} ({row['pct_diff']:+.2f}%).")
        for r in self.fks:
            if r["orphan_rows"]:
                m.append(f"Orphan FK: {r['child']}.{r['child_col']} -> {r['parent']}.{r['parent_pk']}: {r['orphan_rows']:,} rows ({r['orphan_pct']}%) have no parent.")
        for f in self.F.values():
            for c in f.challenges:
                if c["material"]:
                    m.append(f"{f.metric}: {c['check']} - {c['detail']}")
            for d in f.devils:
                if not d["agrees"]:
                    m.append(f"{f.metric}: devil's-advocate disagreement - {d['check']} ({d['detail']})")
        for q in self.quality:
            for i in q["issues"]:
                if re.search(r"duplicated rows|no unique key|non-numeric|unparseable|date logic|negative", i):
                    m.append(f"Data quality [{q['table']}]: {i}")
        if not self.integrity.get("unchanged", True):
            m.append("SOURCE FILES CHANGED DURING THE RUN (hash mismatch) - investigate immediately.")
        seen, out = set(), []
        for x in m:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    def recommended_next_step(self, mats):
        if self.errors:
            return ("Some audit steps errored (see 'Run errors'). Paste this report back, fix TABLE_OVERRIDES/COLUMN_OVERRIDES "
                    "if a table/column was mis-detected, then re-run before building anything.")
        if any(row["role"] == "primary" and row["status"] in ("DIFFERENT", "NOT TESTABLE") for row in self.recon):
            return ("Paste this report back into Claude. Resolve each DIFFERENT / NOT TESTABLE reference metric first "
                    "(usually a definition or scope difference - see the 'alternative' rows in metric_reference_comparison.csv) "
                    "and only then design the dashboard on the VERIFIED metrics.")
        if mats:
            return "All reference metrics match, but data-quality/relationship issues remain; decide handling rules for them, then design the dashboard."
        return "No material discrepancy remains. Proceed to dashboard design using the VERIFIED metrics; keep the section-8 UNSUPPORTED analytics out of scope."

    def write_outputs(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        mats = self.material_discrepancies()
        md = self.render_md(mats)
        (self.out_dir / "audit_report.md").write_text(md, encoding="utf-8")
        results = {
            "run": {"timestamp": dt.datetime.now().isoformat(timespec="seconds"), "data_dir": str(self.data_dir),
                    "python": sys.version.split()[0], "pandas": pd.__version__, "numpy": np.__version__,
                    "platform": platform.platform(), "seconds": round(time.time() - self.t0, 1)},
            "source_integrity": {"unchanged": self.integrity.get("unchanged"), "sha256_before": self.integrity.get("before"),
                                 "sha256_after": self.integrity.get("after")},
            "notes": self.notes, "detected_roles": {k: v.file for k, v in self.roles.items()},
            "role_alternatives": self.role_alts, "column_mapping": self.mapping, "column_ambiguities": self.ambiguity,
            "file_inventory": self.inventory, "data_quality": self.quality,
            "relationships": self.fks, "weak_relationship_candidates": self.weak_fks,
            "core_metrics": {k: f.to_dict() for k, f in self.F.items()},
            "fanout": self.fanout, "discount_semantics": self.discount,
            "reconciliation": self.recon, "unsupported_analytics": self.analytics,
            "corrections_applied": self.all_corrections(), "material_discrepancies": mats,
            "errors": self.errors}
        (self.out_dir / "audit_results.json").write_text(
            json.dumps(clean_json(results), indent=2, ensure_ascii=False), encoding="utf-8")
        cols = ["reference_key", "metric", "role", "reference_value", "computed_value", "abs_diff", "pct_diff",
                "status", "definition", "metric_audit_status"]
        pd.DataFrame(self.recon).reindex(columns=cols).to_csv(self.out_dir / "metric_reference_comparison.csv",
                                                               index=False, encoding="utf-8-sig")
        self.log(f"Wrote outputs to {self.out_dir}")

    # ------------------------------------------------------------- markdown
    def render_md(self, mats):
        L = []
        w = L.append
        w("# DistribuX local audit report")
        w(f"Run: {dt.datetime.now():%Y-%m-%d %H:%M} | folder: `{self.data_dir}` | pandas {pd.__version__} | "
          f"{len(self.T)} CSVs loaded")
        w(f"Source files unchanged (SHA-256 before vs after): **{'YES' if self.integrity.get('unchanged') else 'NO - INVESTIGATE'}**")
        for n in self.notes:
            w(f"- NOTE: {n}")
        # 0 mapping
        w("\n## 0. Detected mapping (check this first - wrong detection = wrong metrics)")
        w("| Role / field | Detected |\n|---|---|")
        for k, v in self.mapping.items():
            w(f"| {cell(k)} | {cell(v)} |")
        for a in self.ambiguity:
            w(f"- AMBIGUOUS `{a['field']}`: used `{a['chosen']}`, other matches {a['other_matches']}")
        for role, alts in self.role_alts.items():
            w(f"- Role `{role}` also matched: {alts}")
        # 1 inventory
        w("\n## 1. File inventory")
        w("| File | Rows | Cols | Size MB | Encoding | Note |\n|---|--:|--:|--:|---|---|")
        for i in self.inventory:
            w(f"| {i['file']} | {i['rows']:,} | {i['columns']} | {i['size_bytes'] / 1e6:.2f} | {i['encoding']}/{repr(i['delimiter'])[1:-1]} | {cell(i['note'], 110)} |")
        w("\nColumns (name:kind):")
        w("```")
        for i in self.inventory:
            w(f"{i['file']}: " + ", ".join(f"{c}:{v.split(' ')[0]}" for c, v in i["column_types"].items()))
        w("```")
        # 2 quality
        w("\n## 2. Data quality")
        w("| Table | Rows | Dup rows | Chosen PK | Other PK candidates | Columns with nulls | Issues |\n|---|--:|--:|---|---|---|--:|")
        for q in self.quality:
            nulls = [f"{c['name']}({c['nulls']:,})" for c in q["columns"] if c["nulls"]]
            w(f"| {q['table']} | {q['rows']:,} | {q['duplicate_rows']:,} | {cell(q['chosen_pk'] or 'NONE')} | "
              f"{cell(q['candidate_primary_keys'][1:4])} | {cell(', '.join(nulls[:5]) or '-', 90)} | {len(q['issues'])} |")
        w("\nIssues flagged:")
        any_issue = False
        for q in self.quality:
            for i in q["issues"]:
                w(f"- [{q['table']}] {cell(i, 220)}")
                any_issue = True
        if not any_issue:
            w("- none")
        w("\nDate ranges:")
        w("| Table.column | Min | Max | Unparsed | Future | Method |\n|---|---|---|--:|--:|---|")
        for q in self.quality:
            for d in q["date_ranges"]:
                w(f"| {q['table']}.{d['column']} | {str(d['min'])[:10]} | {str(d['max'])[:10]} | {d['unparsed']:,} | {d['future_dates']:,} | {d['parse_method']} |")
        # 3 relationships
        w("\n## 3. Relationships (inferred by name; orphan check = child value missing from parent key)")
        w("| Child.col -> Parent.pk | Child rows | Orphan rows | Orphan % | Cardinality | Max/mean children | Parent coverage % | Key mode | Status |\n|---|--:|--:|--:|---|---|--:|---|---|")
        for r in self.fks:
            w(f"| {r['child']}.{r['child_col']} -> {r['parent']}.{r['parent_pk']} | {r['child_rows']:,} | {r['orphan_rows']:,} | "
              f"{r['orphan_pct']} | {r['cardinality']} | {r['children_per_parent_max']}/{r['children_per_parent_mean']} | "
              f"{r['parent_coverage_pct']} | {r['key_mode']} | {r['status']} |")
        if not self.fks:
            w("| (no relationships detected) | | | | | | | | |")
        if self.weak_fks:
            w("\nWeak candidates (value overlap only, UNRESOLVED): " +
              "; ".join(f"{x['child']}.{x['child_col']}->{x['parent']}.{x['parent_pk']}" for x in self.weak_fks[:10]))
        # 4 metrics
        w("\n## 4. Core metrics")
        w("| Metric | Result | Grain | Denominator | Source | Status | Limitation |\n| ------ | -----: | ----- | ----------- | ------ | ------ | ---------- |")
        for f in self.F.values():
            w(f"| {cell(f.metric, 70)} | {fmt_val(f.result, f.fmt)} | {cell(f.grain, 40)} | {cell(f.denominator, 45)} | "
              f"{cell(f.source, 70)} | **{f.status}** | {cell(' '.join(f.limitations), 200)} |")
        y = self.F.get("orders_2024")
        if y is not None and y.extra.get("orders_by_year"):
            w(f"\nOrders by year (order date): {y.extra['orders_by_year']}")
        # 5 fanout
        w("\n## 5. Grain and fan-out")
        d = self.fanout.get("primary_demo") if self.fanout else None
        if isinstance(d, str) or d is None:
            w(d or "not run")
        else:
            w(f"Scope: {d['scope']}. Orders with >1 line: {d['orders_with_multiple_lines']:,}; with >1 payment: {d['orders_with_multiple_payments']:,}.")
            w("| Measure | Correct aggregation | Naive join aggregation | Inflation x |\n|---|--:|--:|--:|")
            c, n = d["correct_aggregation"], d["naive_join_aggregation"]
            ir = d["inflation_ratio"]
            w(f"| Orders | {fnum(c['orders (COUNT DISTINCT)'])} | {fnum(n['orders (COUNT(*) after joins)'])} (rows) | {ir['orders']:.4f} |")
            w(f"| Line amount ({d['line_amount_source']}) | {fnum(c['line amount'], 2)} | {fnum(n['line amount'], 2)} | {ir['line amount']:.4f} |")
            w(f"| Payments ({d['payment_amount_source']}) | {fnum(c['payments'], 2)} | {fnum(n['payments'], 2)} | {ir['payments']:.4f} |")
            if "units" in c:
                w(f"| Units | {fnum(c['units'])} | {fnum(n['units'])} | {n['units'] / c['units']:.4f} |" if c["units"] else "")
            mj = d["materialized_join_check"]
            w(f"Devil's advocate - materialised join check: {mj if isinstance(mj, str) else ('AGREES with analytic naive result' if mj['agrees_with_analytic_naive'] else 'DISAGREES: ' + str(mj))}")
            ol = self.fanout.get("order_level_line_vs_payment")
            if ol:
                w("Order-level line value vs payments: " + "; ".join(f"{k}={v:,}" for k, v in ol.items() if k != "note") + f". {ol['note']}")
        if self.fanout.get("generic"):
            w("\nOther 1:N inflation risks (parent amount summed after a LEFT JOIN to its children):")
            w("| Parent.col | Child | Row multiplication | Inflation x |\n|---|---|--:|--:|")
            for g in self.fanout["generic"][:10]:
                w(f"| {g['parent']}.{g['parent_amount_col']} | {g['child']} | {g['row_multiplication']} | {g['inflation_ratio']} |")
        # 6 discount
        w("\n## 6. Discount semantics")
        if not self.discount.get("columns"):
            w("No discount-like numeric column found. Unit: UNKNOWN.")
        for c in self.discount.get("columns", []):
            w(f"**{c['table']}.{c['column']}** - unit status: **{c['unit_status']}** ({c['unit_basis']})")
            w(f"- n={c['rows']:,}, nulls={c['nulls']:,}, distinct={c['distinct']}, min={c['min']:.6g}, median={c['median']:.6g}, "
              f"mean={c['mean']:.6g}, max={c['max']:.6g}, zeros={c['zero_share'] * 100:.1f}%, negatives={c['negatives']:,}, >1: {c['gt_1']:,}, >100: {c['gt_100']:,}")
            w(f"- all within 0-1: {c['all_within_0_1']}; within 0-100: {c['all_within_0_100']}; integer-valued: {c['all_integer_valued']}; "
              f"% signs in raw cells: {c['percent_sign_in_raw_values']:,}; name hint: {c['name_hint']}")
            w(f"- top values: {c['top_values']}")
            if c["arithmetic_tests"]:
                w(f"- arithmetic tests on {c['arithmetic_tests']['rows_tested(discount>0)']:,} discounted rows: {c['arithmetic_tests']['share_of_rows_matching']}")
        for e in self.discount.get("documentation_evidence", []):
            w(f"- documentation file `{e['file']}`: {cell(e['discount_mentions'], 200)}")
        h = self.discount.get("hypothetical")
        if h:
            if h.get("hypothetical_discounted_revenue") is not None:
                w(f"- **{h['label']}**: hypothetical discounted revenue = {fnum(h['hypothetical_discounted_revenue'], 2)} "
                  f"(gross {fnum(h['gross_sales'], 2)}; implied discount {h['implied_discount_pct_of_gross'] * 100:.3f}% of gross)."
                  + (f" WARNING: {h['warning']}" if h.get("warning") else ""))
            else:
                w(f"- {h['label']}: NOT COMPUTED - {h.get('warning')}")
        # 7 recon
        w("\n## 7. Reconciliation vs reference values (never forced)")
        w("Rules: exact refs MATCH if |diff|<=0.5 (rounding), CLOSE if <=1%; approximate refs MATCH <=0.5%, CLOSE <=5%.")
        w("| Metric | Reference | Computed | Diff % | Status | Metric audit status |\n|---|--:|--:|--:|---|---|")
        for r in self.recon:
            if r["role"] in ("primary", "derived", "reference_check"):
                kind = "pct" if r["role"] == "derived" else ("money" if r["reference_key"] in ("gross_sales", "payments", "refunds") else "int")
                w(f"| {cell(r['metric'], 80)} | {fmt_val(r['reference_value'], kind)} | {fmt_val(r['computed_value'], kind)} | "
                  f"{pdiff(r['pct_diff'])} | **{r['status']}** | {r['metric_audit_status']} |")
        alts = [r for r in self.recon if r["role"].startswith("alternative")]
        if alts:
            w("\nAlternative definitions (informational; do NOT overwrite the primary status):")
            w("| Metric | Definition | Computed | Diff % | Status |\n|---|---|--:|--:|---|")
            for r in alts:
                w(f"| {cell(r['metric'], 40)} | {cell(r['definition'], 90)} | {fnum(r['computed_value'], 2)} | {pdiff(r['pct_diff'])} | {r['status']} |")
        # 8 unsupported
        w("\n## 8. Analytics the dataset can / cannot support")
        w("| Analytic | Verdict | Related columns found | Missing evidence | Reasoning |\n|---|---|---|---|---|")
        for a in self.analytics:
            w(f"| {a['analytic']} | **{a['verdict']}** | {cell(', '.join(a['related_columns_found']) or '-', 110)} | "
              f"{cell('; '.join(a['required_but_missing']), 110)} | {cell(a['reasoning'], 260)} |")
        # loop log
        w("\n## Evidence-gated loop log (one line per metric; targeted revalidation only where a correction fired)")
        w("| Metric | Status | Gate | Loop outcome |\n|---|---|---|---|")
        for f in self.F.values():
            w(f"| {cell(f.metric, 50)} | {f.status} | {f.gate} | {cell(f.loop_summary(), 420)} |")
        corr = self.all_corrections()
        if corr:
            w("\nCorrections applied on read (raw CSVs untouched):")
            for c in corr:
                w(f"- {c['table']}.{c['column']} [{c['type']}]: {c['before']} -> {c['after']}")
        # final
        by = {"VERIFIED": [], "CORRECTED": [], "UNRESOLVED": [], "UNSUPPORTED": []}
        for f in self.F.values():
            by[f.status].append(f"{f.metric} = {fmt_val(f.result, f.fmt)}" if f.result is not None else f.metric)
        for r in self.fks:
            by[r["status"]].append(f"FK {r['child']}.{r['child_col']} -> {r['parent']}")
        for a in self.analytics:
            if a["verdict"] in by:
                by[a["verdict"]].append(f"Analytic: {a['analytic']}")
        for k in ("VERIFIED", "CORRECTED", "UNRESOLVED", "UNSUPPORTED"):
            w(f"\n## {k}")
            for x in by[k] or ["(none)"]:
                w(f"- {cell(x, 200)}")
        w("\n## MATERIAL DISCREPANCIES")
        for x in mats or ["(none found)"]:
            w(f"- {cell(x, 300)}")
        if self.errors:
            w("\n## Run errors (steps that failed; other sections are still valid)")
            for e in self.errors:
                w(f"- {e['step']}: {cell(e['error'], 200)}")
        w("\n## RECOMMENDED NEXT STEP")
        w(self.recommended_next_step(mats))
        return "\n".join(L) + "\n"


def pdiff(v):
    return "n/a" if v is None else "%+.3f" % v


def fmt_num_any(v):
    if v is None:
        return "n/a"
    return fnum(v, 4 if isinstance(v, float) and abs(v) < 1 else (2 if isinstance(v, float) and v != int(v) else 0))


# =============================================================================
def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = [a for a in sys.argv[1:]]
    out_arg = None
    if "--out" in args:
        i = args.index("--out")
        out_arg = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
    data_dir = args[0] if args else DATA_DIR
    out_dir = Path(out_arg) if out_arg else Path(__file__).resolve().parent / OUTPUT_DIRNAME
    print(f"DistribuX local audit\n  data : {data_dir}\n  out  : {out_dir}\n  (source CSVs are opened read-only)\n")
    aud = Auditor(data_dir, out_dir)
    aud.run()
    st = {}
    for f in aud.F.values():
        st[f.status] = st.get(f.status, 0) + 1
    print("\n=== DONE ===")
    print(f"Metric statuses: {st}")
    print(f"Source files unchanged: {aud.integrity.get('unchanged')}")
    print(f"Open: {out_dir / 'audit_report.md'}  (paste it back into Claude)")


if __name__ == "__main__":
    main()
