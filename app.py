"""SalesPulse - role-based analytics website for flexible CSV datasets.

Uses Python standard libraries for import, profiling and analysis. Uploads do
not require sales-specific columns; recognized CRM/sales fields receive extra
KPIs, while all other tabular data gets data-driven summaries and insights.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import sqlite3
import threading
import secrets
import time
from urllib.parse import unquote, urlsplit
import hmac
from collections import defaultdict
from datetime import date, datetime
from functools import wraps
from pathlib import Path
from statistics import mean

from flask import (
    Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_FILE = DATA_DIR / "sales_pipeline_clean.csv"
BACKUP_FILE = DATA_DIR / "sales_pipeline_backup.csv"  # Legacy upload backup; retained for one-time migration.
META_FILE = DATA_DIR / "dataset_meta.json"
# Immutable bundled starter dataset + separate temporary administrator upload.
ORIGINAL_DATA_FILE = DATA_DIR / "original_sales_pipeline.csv"
ORIGINAL_META_FILE = DATA_DIR / "original_dataset_meta.json"
ACTIVE_UPLOAD_FILE = DATA_DIR / "active_uploaded_dataset.csv"
ACTIVE_META_FILE = DATA_DIR / "active_uploaded_meta.json"
DB_FILE = BASE_DIR / "instance" / "users.db"
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
STAGE_ORDER = ["Qualified", "Proposal", "Negotiation"]
LOST_VALUES = {"lost", "closed lost", "loss", "cancelled", "canceled", "cancel", "refunded", "refund", "failed", "rejected", "void", "voided", "chargeback", "declined", "returned", "return"}
MAX_LOGIN_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 15 * 60
LOCKOUT_SECONDS = 15 * 60
CANONICAL_FIELDS = [
    "Deal_ID", "Lead_Date", "Salesperson", "Company", "Industry", "Region",
    "Product", "Deal_Value", "Stage", "Probability", "Expected_Close_Date",
    "Actual_Close_Date", "Status", "Revenue", "Lead_Source", "Quantity",
]

app = Flask(__name__)
DEBUG_ENABLED = os.environ.get("FLASK_DEBUG") == "1"
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY and not DEBUG_ENABLED:
    raise RuntimeError("SECRET_KEY is required when Flask debug mode is off. Set a strong SECRET_KEY environment variable before starting SalesPulse.")
app.secret_key = SECRET_KEY or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
)

_CACHE = {"signature": None, "rows": None, "meta": None}
_CACHE_LOCK = threading.RLock()

# Common header names from retail, e-commerce, CRM, and sales-export datasets.
ALIASES = {
    "Deal_ID": ["deal id", "opportunity id", "opportunity", "deal", "transaction id", "order id", "invoice id", "id"],
    "Lead_Date": ["lead date", "created date", "created at", "opportunity date", "lead created date", "record date"],
    "Actual_Close_Date": ["actual close date", "closed date", "close date", "sales date", "sale date", "order date", "transaction date", "invoice date", "date", "purchase date"],
    "Expected_Close_Date": ["expected close date", "target close date", "forecast close date", "expected closing date", "close date"],
    "Salesperson": ["salesperson", "sales person", "sales rep", "sales representative", "representative", "rep name", "account executive", "sales executive", "owner", "agent"],
    "Company": ["company", "company name", "account", "account name", "customer", "customer name", "client", "client name", "buyer", "buyer name"],
    "Industry": ["industry", "customer industry", "sector", "business sector"],
    "Region": ["region", "territory", "sales region", "area", "zone", "country", "state", "location"],
    "Product": ["product", "product name", "item", "item name", "service", "service name", "sku", "category", "product category"],
    "Deal_Value": ["deal value", "deal amount", "opportunity amount", "opportunity value", "pipeline value", "potential value", "estimated deal value", "estimated amount", "contract value"],
    "Stage": ["stage", "sales stage", "deal stage", "pipeline stage", "opportunity stage"],
    "Probability": ["probability", "win probability", "close probability", "probability to close", "chance to win"],
    "Status": ["status", "deal status", "opportunity status", "sales status", "order status", "outcome", "result"],
    "Revenue": ["revenue", "total revenue", "gross revenue", "sales", "total sales", "sales amount", "total sales amount", "net sales", "gross sales", "amount", "total amount", "gross amount", "transaction amount", "transaction value", "order amount", "order value", "order total", "invoice amount", "invoice total", "sales value", "total value", "sales total", "total", "line total", "extended amount", "total price"],
    "Lead_Source": ["lead source", "source", "marketing source", "channel", "sales channel", "acquisition source", "origin"],
    "Quantity": ["quantity", "qty", "units", "units sold", "quantity sold", "items sold"],
    "Unit_Price": ["unit price", "price per unit", "selling price", "unit cost", "rate", "price"],
}


def today_iso() -> str:
    return date.today().isoformat()


def normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()


def parse_number(value, default=0.0) -> float:
    """Parse common US and European monetary/number formats safely.

    Examples: 1,234.50 -> 1234.50; 1.234,50 -> 1234.50;
    100,00 -> 100.00; 1,234 -> 1234.
    """
    if value is None:
        return default
    raw = str(value).strip()
    if not raw:
        return default
    negative = raw.startswith("(") and raw.endswith(")")
    raw = raw.replace("₹", "").replace("$", "").replace("€", "").replace("£", "")
    raw = re.sub(r"(?i)\b(inr|rs\.?|usd|eur|gbp)\b", "", raw).strip()
    raw = raw.replace(" ", "").replace("%", "").replace("(", "").replace(")", "")
    raw = re.sub(r"[^0-9,.-]", "", raw)
    if not raw or raw in {"-", ".", ","}:
        return default

    comma = raw.rfind(",")
    dot = raw.rfind(".")
    if comma >= 0 and dot >= 0:
        # The last punctuation is the decimal separator; the other is grouping.
        if comma > dot:
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif comma >= 0:
        trailing = len(raw) - comma - 1
        if trailing in (1, 2):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    # A single dot with three trailing digits is treated as a thousands separator.
    elif dot >= 0 and raw.count(".") == 1 and len(raw) - dot - 1 == 3:
        raw = raw.replace(".", "")

    try:
        number = float(raw)
        return -abs(number) if negative else number
    except (TypeError, ValueError):
        return default


def is_lost_status(value) -> bool:
    """Recognize terminal/non-sale statuses, including decorated export labels."""
    normalized = re.sub(r"[^a-z]+", " ", str(value or "").lower()).strip()
    if normalized in LOST_VALUES:
        return True
    non_sale_tokens = {
        "lost", "loss", "cancel", "cancelled", "canceled", "refund", "refunded",
        "failed", "rejected", "void", "voided", "chargeback", "declined",
        "returned", "return",
    }
    return bool(non_sale_tokens.intersection(normalized.split()))


def parse_probability(value, fallback=0.0) -> float:
    if value is None or str(value).strip() == "":
        return fallback
    raw = str(value).strip()
    had_percent = "%" in raw
    value = parse_number(raw, fallback)
    if had_percent or value > 1:
        value = value / 100.0
    return max(0.0, min(1.0, value))


def parse_date(value) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    # Try ISO first, then common spreadsheet/export formats.
    candidates = [
        "%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y",
        "%d-%b-%Y", "%d %b %Y", "%b %d, %Y", "%B %d, %Y",
        "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M", "%d/%m/%Y %H:%M:%S",
    ]
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    for fmt in candidates:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def find_header(headers, canonical):
    normalized = {normalize_header(h): h for h in headers}
    aliases = [normalize_header(x) for x in ALIASES[canonical]]
    # Exact matches first.
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
    # Soft matches for exports like "Customer Revenue (INR)".
    for alias in aliases:
        if len(alias) >= 5:
            for norm, original in normalized.items():
                if alias in norm or norm in alias:
                    return original
    return None


def detect_columns(headers, sample_rows):
    # Resolve headers globally so one column cannot accidentally be interpreted
    # as several fields (for example, "Sales" must not become Stage and Status).
    ordered_fields = CANONICAL_FIELDS + ["Unit_Price"]
    normalized_headers = [(normalize_header(h), h) for h in headers]
    mapping = {field: None for field in ordered_fields}
    used_headers = set()

    # Pass 1: prefer the canonical header names when several similar fields
    # exist (for example, prefer "Product" over "Product Category").
    for field in ordered_fields:
        primary = normalize_header(field.replace("_", " "))
        for normalized, original in normalized_headers:
            if original not in used_headers and normalized == primary:
                mapping[field] = original
                used_headers.add(original)
                break

    # Pass 2: exact alias matches.
    for field in ordered_fields:
        if mapping[field]:
            continue
        aliases = {normalize_header(x) for x in ALIASES[field]}
        for normalized, original in normalized_headers:
            if original not in used_headers and normalized in aliases:
                mapping[field] = original
                used_headers.add(original)
                break

    # Pass 3: phrase containment for decorated export headers such as
    # "Sales Amount (INR)". Do not reverse-match short words like "sales".
    for field in ordered_fields:
        if mapping[field]:
            continue
        aliases = sorted({normalize_header(x) for x in ALIASES[field] if len(normalize_header(x)) >= 8}, key=len, reverse=True)
        for alias in aliases:
            for normalized, original in normalized_headers:
                if original not in used_headers and alias in normalized:
                    mapping[field] = original
                    used_headers.add(original)
                    break
            if mapping[field]:
                break

    # Pass 4: allow an unambiguous core term with extra units or suffixes,
    # e.g. "Revenue (INR)" or "Amount USD". Exact/phrase matches above reserve
    # headers first, preventing "Sales Stage" being confused with "Sales".
    token_aliases = {
        "Revenue": {"revenue", "amount", "sales", "turnover"},
        "Stage": {"stage"},
        "Status": {"status"},
        "Probability": {"probability"},
        "Region": {"region", "territory"},
        "Industry": {"industry"},
        "Product": {"product"},
        "Quantity": {"quantity"},
        "Unit_Price": {"price"},
        "Salesperson": {"salesperson"},
        "Company": {"customer", "company"},
    }
    for field, tokens in token_aliases.items():
        if mapping.get(field):
            continue
        for normalized, original in normalized_headers:
            words = set(normalized.split())
            if original in used_headers or not tokens.intersection(words):
                continue
            if field == "Revenue" and "sales" in words and words.intersection({"channel", "territory", "region", "team", "rep", "person", "stage", "status", "date", "source", "price"}):
                continue
            mapping[field] = original
            used_headers.add(original)
            break

    stage_col = mapping.get("Stage")
    prob_col = mapping.get("Probability")
    status_col = mapping.get("Status")
    explicit_deal = mapping.get("Deal_Value")
    revenue_col = mapping.get("Revenue")

    pipeline_status_values = {
        "won", "closed won", "win", "lost", "closed lost", "loss", "open",
        "qualified", "proposal", "negotiation", "in progress", "prospecting"
    }
    status_signal = False
    if status_col:
        for row in sample_rows[:50]:
            text = re.sub(r"[^a-z]+", " ", str(row.get(status_col, "")).lower()).strip()
            if text in pipeline_status_values:
                status_signal = True
                break
    # A generic column called "Stage" by itself is not enough to assume a CRM funnel.
    # Require a probability/deal-value field or a stage plus CRM-like status values.
    def has_numeric_sample(column):
        if not column:
            return False
        return any(parse_number(row.get(column, ""), None) is not None for row in sample_rows[:50])

    explicit_deal_numeric = has_numeric_sample(explicit_deal)
    revenue_numeric = has_numeric_sample(revenue_col)
    quantity_price_numeric = has_numeric_sample(mapping.get("Quantity")) and has_numeric_sample(mapping.get("Unit_Price"))
    probability_signal = bool(prob_col and (stage_col or status_signal or explicit_deal_numeric))
    # Stage/Status are common words in many non-sales datasets. Do not classify
    # a general table as a CRM pipeline based on those two headers alone; ask
    # for an additional CRM identity field (deal ID, owner, or account/company).
    crm_identity_count = sum(bool(mapping.get(field)) for field in ("Deal_ID", "Salesperson", "Company"))
    pipeline_signal = bool(
        explicit_deal_numeric
        or probability_signal
        or (stage_col and status_signal and crm_identity_count >= 2)
    )

    # In pipeline files, an amount/deal column is potential value. In
    # transaction files, Sales/Amount/Revenue is realized value. Files without
    # a readable measure are still accepted as general tabular datasets.
    if pipeline_signal and not explicit_deal and revenue_col:
        mapping["Deal_Value"] = revenue_col

    has_sales_measure = bool(revenue_numeric or explicit_deal_numeric or quantity_price_numeric)
    generic_mode = not pipeline_signal and not has_sales_measure
    mapping["_pipeline_mode"] = pipeline_signal
    mapping["_generic_mode"] = generic_mode
    return mapping



def infer_generic_schema(headers, raw_rows):
    """Infer useful column types from arbitrary tabular data without requiring names."""
    profiles = []
    numeric_columns, categorical_columns, date_columns = [], [], []
    sample = raw_rows[:300]
    for header in headers:
        values = [str(row.get(header, "") or "").strip() for row in sample]
        nonempty = [v for v in values if v]
        if not nonempty:
            kind = "empty"
        else:
            date_hits = sum(bool(parse_date(v)) for v in nonempty)
            numeric_values = [parse_number(v, None) for v in nonempty]
            numeric_hits = sum(v is not None for v in numeric_values)
            unique_count = len(set(nonempty))
            id_like = bool(re.search(r"(^|\b)(id|code|uuid|key)(\b|$)", normalize_header(header)))
            if date_hits / len(nonempty) >= 0.75 and unique_count > 1:
                kind = "date"
                date_columns.append(header)
            elif numeric_hits / len(nonempty) >= 0.80 and not id_like:
                kind = "numeric"
                numeric_columns.append(header)
            else:
                kind = "categorical"
                # Show useful top-value summaries for ordinary category fields
                # even when they contain hundreds or thousands of distinct labels.
                # Pure identifier columns remain in the profile/table only.
                if not id_like and unique_count <= max(100, int(len(raw_rows) * 0.6)):
                    categorical_columns.append(header)
        all_values = [str(row.get(header, "") or "").strip() for row in raw_rows]
        distinct = len({v for v in all_values if v})
        profiles.append({
            "name": header,
            "type": kind,
            "non_empty": sum(bool(v) for v in all_values),
            "missing": sum(not bool(v) for v in all_values),
            "distinct": distinct,
        })
    return {
        "columns": list(headers),
        "profiles": profiles,
        "numeric_columns": numeric_columns,
        "categorical_columns": categorical_columns,
        "date_columns": date_columns,
    }


def canonicalize_sales_rows(headers, raw_rows):
    if not headers:
        raise ValueError("The CSV file needs a header row.")
    mapping = detect_columns(headers, raw_rows[:20])
    pipeline_mode = bool(mapping.get("_pipeline_mode"))
    generic_mode = bool(mapping.get("_generic_mode"))
    revenue_col = mapping.get("Revenue")
    deal_col = mapping.get("Deal_Value")
    qty_col = mapping.get("Quantity")
    unit_price_col = mapping.get("Unit_Price")
    status_col = mapping.get("Status")
    stage_col = mapping.get("Stage")
    prob_col = mapping.get("Probability")

    records = []
    skipped_rows = 0
    for index, raw in enumerate(raw_rows, start=1):
        def val(field):
            col = mapping.get(field)
            return raw.get(col, "") if col else ""

        lead_date = parse_date(val("Lead_Date"))
        actual_date = parse_date(val("Actual_Close_Date"))
        expected_date = parse_date(val("Expected_Close_Date"))
        if not actual_date and not pipeline_mode:
            actual_date = parse_date(val("Lead_Date"))
        if not expected_date:
            # For a CRM open opportunity, a missing expected-close date is unknown,
            # not the lead date (which would incorrectly mark every old lead overdue).
            expected_date = actual_date or (lead_date if not pipeline_mode else "")
        if not lead_date:
            lead_date = actual_date or expected_date

        if revenue_col:
            revenue = parse_number(raw.get(revenue_col, ""), 0.0)
        elif qty_col and unit_price_col:
            revenue = parse_number(raw.get(qty_col, ""), 0.0) * parse_number(raw.get(unit_price_col, ""), 0.0)
        else:
            revenue = 0.0
        deal_value = parse_number(raw.get(deal_col, ""), 0.0) if deal_col else revenue

        raw_status = str(raw.get(status_col, "") if status_col else "").strip().lower()
        raw_stage = str(raw.get(stage_col, "") if stage_col else "").strip()
        status_text = re.sub(r"[^a-z]+", " ", raw_status).strip()
        stage_text = re.sub(r"[^a-z]+", " ", raw_stage.lower()).strip()
        won_values = {"won", "closed won", "win", "success", "successful", "completed", "complete", "paid", "delivered", "fulfilled", "shipped"}
        open_values = {"open", "active", "pending", "in progress", "qualified", "proposal", "negotiation", "prospecting"}

        if generic_mode:
            # Retain every row from a generic dataset. Status-like values are
            # descriptive data here, not grounds for silently dropping records.
            status = "Record"
            stage = "Not applicable"
            probability = 0.0
            revenue = 0.0
            deal_value = 0.0
            actual_date = actual_date or lead_date or expected_date
        elif not pipeline_mode:
            # Cancelled, refunded, failed or returned transactions are not realized sales.
            if is_lost_status(status_text):
                skipped_rows += 1
                continue
            status = "Sale"
            stage = "Sales transaction"
            probability = 1.0
            if deal_value <= 0:
                deal_value = revenue
            # For transaction rows, the order/sale date is the actual transaction date.
            actual_date = actual_date or lead_date or expected_date
        else:
            combined = status_text or stage_text
            if combined in won_values or stage_text in won_values:
                status, stage = "Won", "Closed Won"
            elif is_lost_status(combined) or is_lost_status(stage_text):
                status, stage = "Lost", "Closed Lost"
            elif combined in open_values or stage_text in open_values or any(term in stage_text for term in ("qualif", "proposal", "negotiat")):
                status = "Open"
                if "qualif" in stage_text or status_text == "qualified":
                    stage = "Qualified"
                elif "proposal" in stage_text or status_text == "proposal":
                    stage = "Proposal"
                elif "negotiat" in stage_text or status_text == "negotiation":
                    stage = "Negotiation"
                elif raw_stage:
                    stage = raw_stage.strip().title()
                else:
                    stage = "Qualified"
            elif status_text:
                status, stage = "Open", raw_stage.title() if raw_stage else "Qualified"
            elif stage_text:
                status, stage = "Open", raw_stage.title()
            else:
                status, stage = "Open", "Qualified"

            fallback_probability = 1.0 if status == "Won" else 0.0 if status == "Lost" else {
                "qualified": 0.30, "proposal": 0.60, "negotiation": 0.80,
            }.get(stage.lower(), 0.35)
            probability = parse_probability(raw.get(prob_col, "") if prob_col else "", fallback_probability)
            if status == "Won":
                probability = 1.0
                if revenue <= 0:
                    revenue = deal_value
            elif status == "Lost":
                probability = 0.0
                revenue = 0.0
            else:
                revenue = 0.0

        record = {
            "Deal_ID": str(raw.get(mapping.get("Deal_ID"), "") if mapping.get("Deal_ID") else "").strip() or f"ROW-{index:06d}",
            "Lead_Date": lead_date,
            "Salesperson": str(val("Salesperson") or "Unknown").strip() or "Unknown",
            "Company": str(val("Company") or "Unknown").strip() or "Unknown",
            "Industry": str(val("Industry") or "Unknown").strip() or "Unknown",
            "Region": str(val("Region") or "Unknown").strip() or "Unknown",
            "Product": str(val("Product") or "Unknown").strip() or "Unknown",
            "Deal_Value": round(deal_value, 2),
            "Stage": stage,
            "Probability": round(probability, 4),
            "Expected_Close_Date": expected_date,
            "Actual_Close_Date": actual_date if (not pipeline_mode or status in {"Won", "Lost"}) else "",
            "Status": status,
            "Revenue": round(revenue, 2),
            "Lead_Source": str(val("Lead_Source") or "Unknown").strip() or "Unknown",
            "Quantity": round(parse_number(val("Quantity"), 0), 2),
            "Raw_Data": json.dumps({str(k): str(v or "") for k, v in raw.items()}, ensure_ascii=False),
        }
        record["Expected_Revenue"] = round(record["Deal_Value"] * record["Probability"], 2)
        record["Sales_Cycle_Days"] = ""
        if record["Lead_Date"] and record["Actual_Close_Date"]:
            try:
                record["Sales_Cycle_Days"] = (date.fromisoformat(record["Actual_Close_Date"]) - date.fromisoformat(record["Lead_Date"])).days
            except ValueError:
                pass
        records.append(record)

    if not records:
        raise ValueError(f"No valid data rows remain after excluding {skipped_rows} cancelled/refunded/failed/returned transaction rows.")
    pipeline_mode = pipeline_mode or any(r["Status"] in {"Open", "Lost"} or r["Stage"] in STAGE_ORDER for r in records)
    mapping_summary = {k: v for k, v in mapping.items() if not k.startswith("_") and v}
    dataset_type = "Exploratory dataset" if generic_mode else ("CRM sales pipeline" if pipeline_mode else "Transaction sales")
    mapping_summary["Dataset type"] = dataset_type
    return records, mapping_summary, pipeline_mode, skipped_rows


def read_csv_upload(file_bytes):
    decoded = None
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            decoded = file_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        raise ValueError("Could not decode this CSV. Save it as UTF-8 CSV and try again.")
    sample = decoded[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(decoded), dialect=dialect)
    raw_headers = list(reader.fieldnames or [])
    if not raw_headers:
        raise ValueError("The CSV file needs a header row.")
    # Fill blank headers and make duplicate headers unique so every column is retained.
    headers = []
    used = set()
    for index, raw_header in enumerate(raw_headers, start=1):
        base_name = str(raw_header or "").strip() or f"Column {index}"
        name = base_name
        suffix = 2
        while name.casefold() in used:
            name = f"{base_name} ({suffix})"
            suffix += 1
        used.add(name.casefold())
        headers.append(name)
    reader.fieldnames = headers
    raw_dict_rows = list(reader)
    extra_count = max((len(row.get(None) or []) for row in raw_dict_rows), default=0)
    extra_headers = []
    for index in range(1, extra_count + 1):
        base_name = f"Extra Column {index}"
        name = base_name
        suffix = 2
        while name.casefold() in used:
            name = f"{base_name} ({suffix})"
            suffix += 1
        used.add(name.casefold())
        extra_headers.append(name)
    headers.extend(extra_headers)
    rows = []
    for row in raw_dict_rows:
        record = {header: str(row.get(header, "") or "").strip() for header in headers if header not in extra_headers}
        extra_values = row.get(None) or []
        for index, header in enumerate(extra_headers):
            record[header] = str(extra_values[index] if index < len(extra_values) else "").strip()
        if any(value for value in record.values()):
            rows.append(record)
    records, mapping, pipeline_mode, skipped_rows = canonicalize_sales_rows(headers, rows)
    schema = infer_generic_schema(headers, rows)
    return records, mapping, pipeline_mode, skipped_rows, schema


def write_canonical_csv(path, rows):
    fields = CANONICAL_FIELDS + ["Expected_Revenue", "Sales_Cycle_Days", "Raw_Data"]
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def db_connect():
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','salesperson','region')),
            password_hash TEXT NOT NULL,
            salesperson TEXT,
            region TEXT,
            must_change_password INTEGER NOT NULL DEFAULT 0
        )""")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "must_change_password" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
        conn.execute("""CREATE TABLE IF NOT EXISTS login_attempts (
            username TEXT NOT NULL, ip_address TEXT NOT NULL, failures INTEGER NOT NULL,
            window_started REAL NOT NULL, locked_until REAL NOT NULL DEFAULT 0,
            PRIMARY KEY(username, ip_address)
        )""")
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count == 0:
            demo_users = [
                ("admin", "Administrator", "admin", "Admin@123", None, None),
                ("arun", "Arun", "salesperson", "Sales@123", "Arun", None),
                ("priya", "Priya", "salesperson", "Sales@123", "Priya", None),
                ("rahul", "Rahul", "salesperson", "Sales@123", "Rahul", None),
                ("sneha", "Sneha", "salesperson", "Sales@123", "Sneha", None),
                ("vikram", "Vikram", "salesperson", "Sales@123", "Vikram", None),
                ("ananya", "Ananya", "salesperson", "Sales@123", "Ananya", None),
                ("kiran", "Kiran", "salesperson", "Sales@123", "Kiran", None),
                ("meena", "Meena", "salesperson", "Sales@123", "Meena", None),
                ("north", "North Region", "region", "Region@123", None, "North"),
                ("south", "South Region", "region", "Region@123", None, "South"),
                ("east", "East Region", "region", "Region@123", None, "East"),
                ("west", "West Region", "region", "Region@123", None, "West"),
            ]
            conn.executemany(
                "INSERT INTO users(username,display_name,role,password_hash,salesperson,region,must_change_password) VALUES(?,?,?,?,?,?,?)",
                [(u, n, r, generate_password_hash(p), s, g, 1 if u == "admin" else 0) for u, n, r, p, s, g in demo_users],
            )
        else:
            # Existing installs created by prior versions get an enforced admin password change
            # when the original published demo credential is still in use.
            admin = conn.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()
            if admin and check_password_hash(admin[0], "Admin@123"):
                conn.execute("UPDATE users SET must_change_password=1 WHERE username='admin'")


def get_user(username=None):
    username = username if username is not None else session.get("username")
    if not username:
        return None
    with db_connect() as conn:
        row = conn.execute(
            "SELECT username,display_name,role,salesperson,region,must_change_password FROM users WHERE username=?", (username,)
        ).fetchone()
    return dict(row) if row else None


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        user = get_user()
        if not user:
            return redirect(url_for("login", next=request.path))
        if user.get("must_change_password") and request.endpoint != "change_password":
            return redirect(url_for("change_password"))
        return fn(*args, **kwargs)
    return wrapped


def roles_required(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            user = get_user()
            if not user:
                return redirect(url_for("login", next=request.path))
            if user.get("must_change_password") and request.endpoint != "change_password":
                return redirect(url_for("change_password"))
            if user["role"] not in roles:
                abort(403)
            return fn(*args, **kwargs)
        return wrapped
    return decorator


def csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


@app.before_request
def protect_post_requests():
    if request.method == "POST":
        expected = session.get("_csrf_token", "")
        supplied = request.form.get("csrf_token", "")
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            abort(400, description="Security token missing or expired. Refresh the page and try again.")


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": csrf_token}


def login_attempt_state(username, ip_address):
    now = time.time()
    with db_connect() as conn:
        row = conn.execute("SELECT failures,window_started,locked_until FROM login_attempts WHERE username=? AND ip_address=?", (username, ip_address)).fetchone()
    if not row:
        return False, 0
    if float(row["locked_until"] or 0) > now:
        return True, int(float(row["locked_until"]) - now)
    return False, 0


def record_login_failure(username, ip_address):
    now = time.time()
    with db_connect() as conn:
        row = conn.execute("SELECT failures,window_started FROM login_attempts WHERE username=? AND ip_address=?", (username, ip_address)).fetchone()
        if not row or now - float(row["window_started"]) > LOGIN_WINDOW_SECONDS:
            failures, started = 1, now
        else:
            failures, started = int(row["failures"]) + 1, float(row["window_started"])
        locked_until = now + LOCKOUT_SECONDS if failures >= MAX_LOGIN_ATTEMPTS else 0
        conn.execute("INSERT INTO login_attempts(username,ip_address,failures,window_started,locked_until) VALUES(?,?,?,?,?) ON CONFLICT(username,ip_address) DO UPDATE SET failures=excluded.failures,window_started=excluded.window_started,locked_until=excluded.locked_until", (username, ip_address, failures, started, locked_until))


def clear_login_failures(username, ip_address):
    with db_connect() as conn:
        conn.execute("DELETE FROM login_attempts WHERE username=? AND ip_address=?", (username, ip_address))


def _read_meta_file(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def ensure_original_dataset():
    """Create a protected baseline and migrate data replaced by older app versions.

    New uploads are never written to the starter dataset. If this is an upgrade
    from the legacy version (which overwrote DATA_FILE), its backup is restored
    as the original and the formerly active upload is moved to the upload slot.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    legacy_meta = _read_meta_file(META_FILE)
    if ORIGINAL_DATA_FILE.exists():
        # If a user upgrades in place without replacing legacy runtime files,
        # preserve that older upload in the new active-upload slot.
        if (not ACTIVE_UPLOAD_FILE.exists()
                and legacy_meta.get("source") == "Uploaded by administrator"
                and DATA_FILE.exists() and BACKUP_FILE.exists()):
            shutil.copy2(DATA_FILE, ACTIVE_UPLOAD_FILE)
            shutil.copy2(META_FILE, ACTIVE_META_FILE)
        if not ORIGINAL_META_FILE.exists():
            if legacy_meta.get("source") == "Bundled starter dataset":
                shutil.copy2(META_FILE, ORIGINAL_META_FILE)
            else:
                ORIGINAL_META_FILE.write_text(json.dumps({
                    "data_type": "CRM sales pipeline",
                    "detected_columns": {field: field for field in CANONICAL_FIELDS if field != "Quantity"},
                    "generic_schema": {}, "row_count": 0,
                    "source": "Bundled starter dataset", "uploaded_at": "",
                    "original_filename": ORIGINAL_DATA_FILE.name, "skipped_rows": 0,
                }, indent=2), encoding="utf-8")
        return

    legacy_was_uploaded = legacy_meta.get("source") == "Uploaded by administrator"
    if legacy_was_uploaded and BACKUP_FILE.exists():
        # Preserve the previously active upload separately, restore its original backup.
        if DATA_FILE.exists() and not ACTIVE_UPLOAD_FILE.exists():
            shutil.copy2(DATA_FILE, ACTIVE_UPLOAD_FILE)
        shutil.copy2(META_FILE, ACTIVE_META_FILE)
        shutil.copy2(BACKUP_FILE, ORIGINAL_DATA_FILE)
        ORIGINAL_META_FILE.write_text(json.dumps({
            "data_type": "CRM sales pipeline",
            "detected_columns": {field: field for field in CANONICAL_FIELDS if field != "Quantity"},
            "generic_schema": {}, "row_count": 0,
            "source": "Bundled starter dataset", "uploaded_at": "",
            "original_filename": "sales_pipeline_clean.csv", "skipped_rows": 0,
        }, indent=2), encoding="utf-8")
    elif DATA_FILE.exists():
        shutil.copy2(DATA_FILE, ORIGINAL_DATA_FILE)
        if META_FILE.exists():
            shutil.copy2(META_FILE, ORIGINAL_META_FILE)
    else:
        raise FileNotFoundError(f"Original starter dataset not found: {DATA_FILE}")


def has_uploaded_dataset():
    return ACTIVE_UPLOAD_FILE.is_file()


def active_dataset_path():
    ensure_original_dataset()
    return ACTIVE_UPLOAD_FILE if ACTIVE_UPLOAD_FILE.is_file() else ORIGINAL_DATA_FILE


def active_meta_path():
    ensure_original_dataset()
    return ACTIVE_META_FILE if ACTIVE_UPLOAD_FILE.is_file() and ACTIVE_META_FILE.is_file() else ORIGINAL_META_FILE


def load_meta():
    ensure_original_dataset()
    meta = _read_meta_file(active_meta_path())
    if meta:
        return meta
    return {"data_type": "CRM sales pipeline", "detected_columns": {}, "generic_schema": {}, "row_count": 0, "source": "Bundled starter dataset", "uploaded_at": ""}


def reset_data_cache():
    with _CACHE_LOCK:
        _CACHE.update({"signature": None, "rows": None, "meta": None})


def load_data(force=False):
    data_path = active_dataset_path()
    if not data_path.exists():
        raise FileNotFoundError(f"Dataset not found: {data_path}")
    stat = data_path.stat()
    meta_path = active_meta_path()
    try:
        meta_stat = meta_path.stat()
        meta_signature = (meta_stat.st_mtime_ns, meta_stat.st_size)
    except OSError:
        meta_signature = (0, 0)
    signature = (str(data_path.resolve()), stat.st_mtime_ns, stat.st_size, meta_signature)
    with _CACHE_LOCK:
        if force or _CACHE["rows"] is None or _CACHE["signature"] != signature:
            with open(data_path, "r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                rows = list(reader)
            normalized = []
            for index, row in enumerate(rows, 1):
                record = {field: row.get(field, "") or "" for field in CANONICAL_FIELDS}
                try:
                    record["_raw_data"] = json.loads(row.get("Raw_Data") or "{}")
                    if not isinstance(record["_raw_data"], dict):
                        record["_raw_data"] = {}
                except json.JSONDecodeError:
                    record["_raw_data"] = {}
                record["Deal_ID"] = record["Deal_ID"] or f"ROW-{index:06d}"
                for field in ("Deal_Value", "Probability", "Revenue", "Quantity"):
                    record[field] = parse_number(record[field])
                record["Probability"] = max(0.0, min(1.0, record["Probability"]))
                record["Expected_Revenue"] = parse_number(row.get("Expected_Revenue"), record["Deal_Value"] * record["Probability"])
                record["Sales_Cycle_Days"] = parse_number(row.get("Sales_Cycle_Days"), -1)
                if record["Sales_Cycle_Days"] < 0:
                    record["Sales_Cycle_Days"] = None
                for key in ("Status", "Stage", "Region", "Salesperson", "Product", "Industry", "Lead_Source", "Company"):
                    record[key] = str(record.get(key) or "Unknown")
                # Overdue is calculated at read time so it stays current without mutating the CSV.
                record["Is_Overdue"] = bool(record["Status"] == "Open" and record.get("Expected_Close_Date") and record["Expected_Close_Date"] < today_iso())
                normalized.append(record)
            _CACHE["rows"] = normalized
            _CACHE["signature"] = signature
            _CACHE["meta"] = load_meta()
        # Cached dictionaries are treated as immutable. API code copies only rows it augments.
        return _CACHE["rows"]


def scoped_data(user):
    rows = load_data()
    if user["role"] == "salesperson":
        who = str(user.get("salesperson") or "").casefold()
        rows = [r for r in rows if str(r.get("Salesperson", "")).casefold() == who]
    elif user["role"] == "region":
        region = str(user.get("region") or "").casefold()
        rows = [r for r in rows if str(r.get("Region", "")).casefold() == region]
    return rows


def analytics_date_value(row):
    """One consistent filter date; generic files use their first inferred date column."""
    meta = _CACHE.get("meta") or load_meta()
    if meta.get("data_type") == "Exploratory dataset":
        date_columns = (meta.get("generic_schema") or {}).get("date_columns", [])
        if date_columns:
            return parse_date((row.get("_raw_data") or {}).get(date_columns[0]))
    return row.get("Actual_Close_Date") or row.get("Expected_Close_Date") or row.get("Lead_Date") or ""


def filtered_data(user, ignore_fields=None):
    rows = scoped_data(user)
    params = request.args
    ignore_fields = set(ignore_fields or ())
    field_params = {"Region": "Region", "Product": "Product", "Industry": "Industry", "Lead_Source": "Lead_Source", "Stage": "Stage", "Status": "Status"}
    if user["role"] == "admin":
        field_params["Salesperson"] = "Salesperson"
    for param, field in field_params.items():
        if field in ignore_fields:
            continue
        value = params.get(param)
        if value and value != "All":
            rows = [r for r in rows if str(r.get(field, "")) == value]
    # Generic category chart selections are encoded as a JSON object so any
    # uploaded column can become a safe cross-filter without a hardcoded schema.
    if (_CACHE.get("meta") or {}).get("data_type") == "Exploratory dataset":
        try:
            generic_filters = json.loads(params.get("gfilter", "{}"))
        except (TypeError, json.JSONDecodeError):
            generic_filters = {}
        if isinstance(generic_filters, dict):
            for field, value in list(generic_filters.items())[:8]:
                if field and value is not None:
                    rows = [r for r in rows if str((r.get("_raw_data") or {}).get(field, "") or "(Blank)") == str(value)]
        try:
            generic_ranges = json.loads(params.get("grange", "{}"))
        except (TypeError, json.JSONDecodeError):
            generic_ranges = {}
        if isinstance(generic_ranges, dict):
            for field, bounds in list(generic_ranges.items())[:4]:
                try:
                    lower, upper = [float(part) for part in str(bounds).split("|", 1)]
                except (TypeError, ValueError):
                    continue
                rows = [r for r in rows if (lambda value: value is not None and lower <= value <= upper)(parse_number((r.get("_raw_data") or {}).get(field), None))]
    start = params.get("start_date", "")
    end = params.get("end_date", "")
    if start:
        rows = [r for r in rows if analytics_date_value(r) and analytics_date_value(r) >= start]
    if end:
        rows = [r for r in rows if analytics_date_value(r) and analytics_date_value(r) <= end]
    month = params.get("month")
    if month:
        rows = [r for r in rows if analytics_date_value(r).startswith(month)]
    return rows


def unique_values(rows, field):
    return sorted({str(r.get(field) or "Unknown") for r in rows if str(r.get(field) or "Unknown").strip()})


def filter_options(user):
    rows = scoped_data(user)
    return {
        "regions": unique_values(rows, "Region") if user["role"] == "admin" else [],
        "salespeople": unique_values(rows, "Salesperson") if user["role"] == "admin" else [],
        "products": unique_values(rows, "Product"),
        "industries": unique_values(rows, "Industry"),
        "lead_sources": unique_values(rows, "Lead_Source"),
    }


def group_sum(rows, field, value_field="Revenue", status=None):
    totals = defaultdict(float)
    for row in rows:
        if status and row.get("Status") != status:
            continue
        key = str(row.get(field) or "Unknown")
        totals[key] += float(row.get(value_field) or 0)
    items = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    return {"labels": [k for k, _ in items], "values": [round(v, 2) for _, v in items]}



def generic_analytics(rows, schema):
    """Build dataset-driven profiles, summaries, charts and plain-language insights."""
    raw_rows = [r.get("_raw_data", {}) for r in rows]
    columns = schema.get("columns") or (list(raw_rows[0].keys()) if raw_rows else [])
    numeric_columns = schema.get("numeric_columns", [])
    categorical_columns = schema.get("categorical_columns", [])
    date_columns = schema.get("date_columns", [])
    numeric_summary = []
    numeric_values = {}
    for field in numeric_columns:
        values = [parse_number(raw.get(field), None) for raw in raw_rows]
        values = [float(v) for v in values if v is not None]
        if not values:
            continue
        ordered = sorted(values)
        mid = len(ordered) // 2
        median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
        numeric_values[field] = values
        numeric_summary.append({
            "field": field, "count": len(values), "sum": round(sum(values), 4),
            "mean": round(mean(values), 4), "median": round(median, 4),
            "min": round(min(values), 4), "max": round(max(values), 4),
        })
    numeric_summary.sort(key=lambda item: item["count"], reverse=True)

    category_charts = []
    primary_numeric = numeric_summary[0]["field"] if numeric_summary else None
    use_numeric_grouping = bool(primary_numeric and min(numeric_values.get(primary_numeric, [0])) >= 0)
    for field in categorical_columns[:6]:
        totals = defaultdict(float)
        counts = defaultdict(int)
        for raw in raw_rows:
            key = str(raw.get(field, "") or "(Blank)")
            counts[key] += 1
            if use_numeric_grouping:
                value = parse_number(raw.get(primary_numeric), None)
                if value is not None:
                    totals[key] += value
        ranked = sorted(counts, key=lambda key: (totals[key] if use_numeric_grouping else counts[key]), reverse=True)[:12]
        category_charts.append({
            "field": field,
            "metric": primary_numeric if use_numeric_grouping else "Record count",
            "aggregation": "sum" if use_numeric_grouping else "count",
            "labels": ranked,
            "values": [round(totals[key], 4) if use_numeric_grouping else counts[key] for key in ranked],
        })

    trend = {"date_field": None, "metric_field": None, "aggregation": "count", "labels": [], "values": []}
    if date_columns:
        date_field = date_columns[0]
        metric_field = primary_numeric
        monthly = defaultdict(float)
        for raw in raw_rows:
            date_value = parse_date(raw.get(date_field))
            if not date_value:
                continue
            month = date_value[:7]
            if metric_field:
                val = parse_number(raw.get(metric_field), None)
                if val is not None:
                    monthly[month] += val
            else:
                monthly[month] += 1
        trend = {
            "date_field": date_field, "metric_field": metric_field,
            "aggregation": "sum" if metric_field else "count",
            "labels": sorted(monthly), "values": [round(monthly[k], 4) for k in sorted(monthly)],
        }

    numeric_histograms = []
    for field in numeric_columns[:4]:
        values = numeric_values.get(field, [])
        if not values:
            continue
        minimum, maximum = min(values), max(values)
        if minimum == maximum:
            labels = [f"{minimum:,.2f}"]
            counts = [len(values)]
            ranges = [[minimum, maximum]]
        else:
            bin_count = min(8, max(4, int(len(values) ** 0.5)))
            width = (maximum - minimum) / bin_count
            counts = [0] * bin_count
            ranges = []
            labels = []
            for index in range(bin_count):
                lower = minimum + index * width
                upper = maximum if index == bin_count - 1 else minimum + (index + 1) * width
                ranges.append([round(lower, 6), round(upper, 6)])
                labels.append(f"{lower:,.2f} – {upper:,.2f}")
            for value in values:
                idx = min(bin_count - 1, int((value - minimum) / width))
                counts[idx] += 1
        numeric_histograms.append({"field": field, "labels": labels, "values": counts, "ranges": ranges})

    total_cells = len(raw_rows) * len(columns)
    missing_cells = sum(1 for raw in raw_rows for field in columns if str(raw.get(field, "") or "").strip() == "")
    duplicate_count = 0
    seen = set()
    for raw in raw_rows:
        signature = tuple(str(raw.get(field, "") or "") for field in columns)
        if signature in seen:
            duplicate_count += 1
        else:
            seen.add(signature)

    insights = [f"The dataset contains {len(raw_rows):,} rows and {len(columns):,} columns."]
    if numeric_summary:
        top = numeric_summary[0]
        insights.append(f"{top['field']} has an average of {top['mean']:,.2f} and ranges from {top['min']:,.2f} to {top['max']:,.2f}.")
    if category_charts:
        top_chart = category_charts[0]
        if top_chart["labels"]:
            insights.append(f"For {top_chart['field']}, the most frequent/top contributing value is {top_chart['labels'][0]}.")
    if trend["labels"]:
        peak_index = max(range(len(trend["values"])), key=lambda i: trend["values"][i])
        metric_label = trend["metric_field"] or "records"
        insights.append(f"The strongest month is {trend['labels'][peak_index]} by {trend['aggregation']} of {metric_label}.")
    if total_cells:
        missing_pct = missing_cells / total_cells * 100
        insights.append(f"{missing_cells:,} of {total_cells:,} cells are blank ({missing_pct:.1f}%).")
    if duplicate_count:
        insights.append(f"There are {duplicate_count:,} duplicate rows based on all uploaded columns.")
    if date_columns:
        parsed_dates = [parse_date(raw.get(date_columns[0])) for raw in raw_rows]
        parsed_dates = [d for d in parsed_dates if d]
        if parsed_dates:
            insights.append(f"{date_columns[0]} covers {min(parsed_dates)} through {max(parsed_dates)}.")

    type_lookup = {item.get("name"): item.get("type", "unknown") for item in schema.get("profiles", [])}
    profiles = []
    for field in columns:
        vals = [str(raw.get(field, "") or "").strip() for raw in raw_rows]
        profiles.append({
            "name": field, "type": type_lookup.get(field, "unknown"),
            "non_empty": sum(bool(v) for v in vals),
            "missing": sum(not bool(v) for v in vals),
            "distinct": len({v for v in vals if v}),
        })
    return {
        "row_count": len(raw_rows), "column_count": len(columns),
        "numeric_columns": numeric_columns, "categorical_columns": categorical_columns,
        "date_columns": date_columns, "profiles": profiles,
        "numeric_summary": numeric_summary[:12], "category_charts": category_charts,
        "numeric_histograms": numeric_histograms,
        "trend": trend, "insights": insights,
        "quality": {"missing_cells": missing_cells, "total_cells": total_cells,
                    "duplicate_rows": duplicate_count,
                    "completeness_pct": round(100 * (1 - missing_cells / total_cells), 1) if total_cells else 100},
    }


def analytics_payload(rows, revenue_rows=None):
    meta = _CACHE.get("meta") or load_meta()
    data_type = meta.get("data_type", "CRM sales pipeline")
    pipeline_mode = data_type == "CRM sales pipeline"
    revenue_rows = rows if revenue_rows is None else revenue_rows
    if data_type == "Exploratory dataset":
        generic = generic_analytics(rows, meta.get("generic_schema") or {})
        user_scope_note = ""
        # If the uploaded file has no recognized owner/region field, non-admin
        # accounts cannot be safely mapped to rows and therefore see no records.
        current_user = get_user()
        detected = meta.get("detected_columns", {})
        if current_user and current_user.get("role") == "salesperson" and not detected.get("Salesperson"):
            user_scope_note = " This dataset has no recognized salesperson/owner column, so salesperson accounts cannot be mapped to individual rows. Ask an administrator to review account mappings."
        elif current_user and current_user.get("role") == "region" and not detected.get("Region"):
            user_scope_note = " This dataset has no recognized region/territory column, so regional accounts cannot be mapped to individual rows. Ask an administrator to review account mappings."
        return {
            "kpis": {
                "total_revenue": None, "pipeline_value": None, "expected_revenue": None,
                "win_rate": None, "loss_rate": None, "conversion_rate": None,
                "avg_deal": None, "avg_cycle": None, "opportunities": len(rows),
                "won_deals": None, "lost_deals": None, "open_deals": None,
                "overdue_deals": None, "overdue_pipeline_value": None, "has_pipeline": False,
            },
            "dataset": {
                "data_type": "Exploratory dataset",
                "row_count": len(rows),
                "detected_columns": meta.get("detected_columns", {}),
                "message": "No sales-specific column template is required. The dashboard profiles the uploaded columns and derives summaries, distributions, trends and data-quality insights from the available values." + user_scope_note,
            },
            "generic": generic,
        }

    won = [r for r in rows if r.get("Status") == "Won"]
    lost = [r for r in rows if r.get("Status") == "Lost"]
    opened = [r for r in rows if r.get("Status") == "Open"]
    overdue_open = [r for r in opened if r.get("Is_Overdue") or (r.get("Expected_Close_Date") and r["Expected_Close_Date"] < today_iso())]
    active_open = [r for r in opened if r not in overdue_open]
    closed_count = len(won) + len(lost)
    all_count = len(rows)

    revenue_won = [r for r in revenue_rows if r.get("Status") == "Won"] if pipeline_mode else list(revenue_rows)
    total_revenue = sum(float(r.get("Revenue") or 0) for r in revenue_won)
    pipeline_value = sum(float(r.get("Deal_Value") or 0) for r in active_open) if pipeline_mode else None
    expected_revenue = sum(float(r.get("Expected_Revenue") or 0) for r in active_open) if pipeline_mode else None

    monthly_totals = defaultdict(float)
    for row in revenue_won:
        month_date = analytics_date_value(row)
        if month_date and len(month_date) >= 7:
            monthly_totals[month_date[:7]] += float(row.get("Revenue") or 0)
    monthly_items = sorted(monthly_totals.items())

    stage_totals = defaultdict(float)
    stage_counts = defaultdict(int)
    for row in active_open:
        stage = str(row.get("Stage") or "Qualified")
        stage_totals[stage] += float(row.get("Deal_Value") or 0)
        stage_counts[stage] += 1
    other_stages = sorted(k for k in stage_totals if k not in STAGE_ORDER)
    stage_labels = [s for s in STAGE_ORDER if s in stage_totals] + other_stages

    cycle_values = [float(r["Sales_Cycle_Days"]) for r in revenue_won if r.get("Sales_Cycle_Days") not in (None, "", -1)]
    closed_revenue_count = len([r for r in revenue_rows if r.get("Status") in {"Won", "Lost"}])
    revenue_won_count = len(revenue_won)
    kpis = {
        "total_revenue": round(total_revenue, 2),
        "pipeline_value": round(pipeline_value, 2) if pipeline_value is not None else None,
        "expected_revenue": round(expected_revenue, 2) if expected_revenue is not None else None,
        "win_rate": round((len(won) / closed_count * 100), 2) if pipeline_mode and closed_count else None,
        "loss_rate": round((len(lost) / closed_count * 100), 2) if pipeline_mode and closed_count else None,
        "conversion_rate": round((len(won) / all_count * 100), 2) if pipeline_mode and all_count else None,
        "avg_deal": round(mean([float(r.get("Revenue") or 0) for r in revenue_won]), 2) if revenue_won else 0,
        "avg_cycle": round(mean(cycle_values), 1) if pipeline_mode and cycle_values else None,
        "opportunities": all_count,
        "won_deals": len(won) if pipeline_mode else 0,
        "lost_deals": len(lost) if pipeline_mode else 0,
        "open_deals": len(opened) if pipeline_mode else 0,
        "overdue_deals": len(overdue_open) if pipeline_mode else 0,
        "overdue_pipeline_value": round(sum(float(r.get("Deal_Value") or 0) for r in overdue_open), 2) if pipeline_mode else None,
        "has_pipeline": pipeline_mode,
    }
    status_counts = {"labels": ["Won", "Lost", "Open"], "values": [len(won), len(lost), len(opened)]} if pipeline_mode else {"labels": ["Sales records"], "values": [all_count]}
    return {
        "kpis": kpis,
        "dataset": {
            "data_type": meta.get("data_type", "CRM sales pipeline"),
            "row_count": all_count,
            "detected_columns": meta.get("detected_columns", {}),
            "message": (f"CRM pipeline metrics are available. {len(overdue_open):,} open opportunities are overdue and are reported separately from active pipeline value." if pipeline_mode else "Transaction sales dataset detected. Revenue and product/region analytics are available; pipeline-specific KPIs are not applicable because no opportunity stages were supplied.")
        },
        "monthly": {"labels": [k for k, _ in monthly_items], "values": [round(v, 2) for _, v in monthly_items]},
        "stage": {"labels": stage_labels, "values": [round(stage_totals[s], 2) for s in stage_labels], "counts": [stage_counts[s] for s in stage_labels]},
        "salesperson": group_sum(revenue_won, "Salesperson"),
        "product": group_sum(revenue_won, "Product"),
        "region": group_sum(revenue_won, "Region"),
        "pipeline_region": group_sum(active_open, "Region", "Deal_Value"),
        "industry": group_sum(revenue_won, "Industry"),
        "lead_source": group_sum(revenue_won, "Lead_Source"),
        "status_counts": status_counts,
    }


def page_context(user):
    return {"user": user, "options": filter_options(user)}


@app.context_processor
def inject_user():
    return {"logged_in_user": get_user()}


@app.route("/")
def welcome():
    user = get_user()
    if user:
        return redirect(url_for("dashboard"))
    return render_template("welcome.html", user=None)


@app.route("/login", methods=["GET", "POST"])
def login():
    if get_user():
        user = get_user()
        return redirect(url_for("change_password" if user.get("must_change_password") else "dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        ip_address = request.remote_addr or "unknown"
        locked, seconds_left = login_attempt_state(username, ip_address)
        if locked:
            flash(f"Too many unsuccessful attempts. Try again in about {max(1, seconds_left // 60 + 1)} minute(s).", "error")
            return render_template("login.html"), 429
        with db_connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            clear_login_failures(username, ip_address)
            session.clear()
            session["username"] = row["username"]
            csrf_token()
            if row["must_change_password"]:
                return redirect(url_for("change_password"))
            destination = request.args.get("next", "")
            parsed = urlsplit(destination)
            decoded_path = unquote(parsed.path)
            if (not destination.startswith("/") or destination.startswith("//") or "\\" in destination
                    or "\\" in decoded_path or parsed.scheme or parsed.netloc or decoded_path.startswith("//")):
                destination = url_for("dashboard")
            return redirect(destination)
        record_login_failure(username, ip_address)
        flash("Invalid username or password. Please try again.", "error")
    return render_template("login.html")


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    user = get_user()
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        with db_connect() as conn:
            row = conn.execute("SELECT password_hash FROM users WHERE username=?", (user["username"],)).fetchone()
            if not row or not check_password_hash(row["password_hash"], current):
                flash("Current password is incorrect.", "error")
            elif len(new_password) < 12:
                flash("New password must contain at least 12 characters.", "error")
            elif new_password != confirm:
                flash("New password and confirmation do not match.", "error")
            else:
                conn.execute("UPDATE users SET password_hash=?,must_change_password=0 WHERE username=?", (generate_password_hash(new_password), user["username"]))
                session.clear()
                session["username"] = user["username"]
                csrf_token()
                flash("Password changed successfully.", "success")
                return redirect(url_for("dashboard"))
    return render_template("change_password.html", user=user)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("welcome"))


@app.route("/dashboard")
@login_required
def dashboard():
    user = get_user()
    context = page_context(user)
    context["has_uploaded_dataset"] = has_uploaded_dataset()
    context["active_dataset_meta"] = load_meta()
    return render_template("dashboard.html", **context)


@app.route("/pipeline")
@login_required
def pipeline():
    return render_template("pipeline.html", **page_context(get_user()))


@app.route("/revenue")
@login_required
def revenue():
    return render_template("revenue.html", **page_context(get_user()))


@app.route("/opportunities")
@login_required
def opportunities():
    return render_template("opportunities.html", **page_context(get_user()))


@app.route("/remove-uploaded-dataset", methods=["POST"])
@roles_required("admin")
def remove_uploaded_dataset():
    """Remove only the temporary upload and restore the protected starter dataset."""
    try:
        if not ACTIVE_UPLOAD_FILE.exists():
            flash("There is no uploaded dataset active. The original dataset is already being used.", "info")
        else:
            ACTIVE_UPLOAD_FILE.unlink()
            try:
                ACTIVE_META_FILE.unlink()
            except FileNotFoundError:
                pass
            # Clean up interrupted upload temp files without touching the original data.
            for temp_path in (ACTIVE_UPLOAD_FILE.with_suffix(".upload.tmp"), ACTIVE_META_FILE.with_suffix(".tmp")):
                try:
                    temp_path.unlink()
                except FileNotFoundError:
                    pass
            reset_data_cache()
            load_data(force=True)
            flash("Uploaded dataset removed. The original bundled dataset and its insights have been restored.", "success")
    except Exception as exc:
        app.logger.exception("Unable to remove uploaded dataset")
        flash(f"Could not remove the uploaded dataset: {exc}", "error")
    return redirect(url_for("dashboard"))


@app.route("/upload", methods=["GET", "POST"])
@roles_required("admin")
def upload():
    if request.method == "POST":
        uploaded = request.files.get("file")
        if not uploaded or not uploaded.filename:
            flash("Choose a CSV dataset to upload.", "error")
            return redirect(url_for("upload"))
        safe_name = secure_filename(uploaded.filename)
        if Path(safe_name).suffix.lower() != ".csv":
            flash("Please upload a .csv file. Excel workbooks must first be saved as CSV.", "error")
            return redirect(url_for("upload"))
        try:
            content = uploaded.read(MAX_UPLOAD_BYTES + 1)
            if len(content) > MAX_UPLOAD_BYTES:
                raise ValueError("The file is larger than 15 MB.")
            rows, mapping, pipeline_mode, skipped_rows, generic_schema = read_csv_upload(content)
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            ensure_original_dataset()
            temp_data = ACTIVE_UPLOAD_FILE.with_suffix(".upload.tmp")
            temp_meta = ACTIVE_META_FILE.with_suffix(".tmp")
            write_canonical_csv(temp_data, rows)
            dataset_type = mapping.get("Dataset type") or ("CRM sales pipeline" if pipeline_mode else "Transaction sales")
            meta = {
                "data_type": dataset_type,
                "detected_columns": {k: v for k, v in mapping.items() if k != "Dataset type"},
                "generic_schema": generic_schema,
                "row_count": len(rows),
                "skipped_rows": skipped_rows,
                "original_filename": safe_name,
                "source": "Uploaded by administrator",
                "uploaded_at": datetime.now().isoformat(timespec="seconds"),
            }
            temp_meta.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            os.replace(temp_meta, ACTIVE_META_FILE)
            os.replace(temp_data, ACTIVE_UPLOAD_FILE)
            reset_data_cache()
            load_data(force=True)
            detected = ", ".join(f"{k} ← {v}" for k, v in mapping.items() if k != "Dataset type")
            flash(f"Upload successful: {len(rows):,} rows accepted as {meta['data_type']}. Skipped {skipped_rows:,} cancelled/refunded/failed transaction rows where applicable. {len(generic_schema.get('columns', []))} columns profiled. {('Detected: ' + detected + '.') if detected else 'No standard sales columns were needed.'}", "success")
            if mapping.get("Salesperson"):
                with db_connect() as conn:
                    salesperson_users = conn.execute("SELECT username,salesperson FROM users WHERE role='salesperson'").fetchall()
                uploaded_people = {str(r.get("Salesperson") or "").strip().casefold() for r in rows}
                unmatched = [f"{u['username']} ({u['salesperson']})" for u in salesperson_users if str(u['salesperson'] or "").strip().casefold() not in uploaded_people]
                if unmatched:
                    flash("Access warning: these salesperson accounts have no matching name in the new dataset and will see an empty view. Ask an administrator to update the salesperson-to-login mapping: " + ", ".join(unmatched), "error")
            return redirect(url_for("dashboard"))
        except Exception as exc:
            app.logger.exception("Dataset upload failed")
            flash(f"Upload failed: {exc}", "error")
        return redirect(url_for("upload"))

    meta = load_meta()
    return render_template("upload.html", **page_context(get_user()), dataset_meta=meta)


@app.route("/api/analytics")
@login_required
def api_analytics():
    try:
        user = get_user()
        rows = filtered_data(user)
        # Stage/Status clicks only narrow the pipeline/outcome analysis. Revenue views remain visible.
        revenue_rows = filtered_data(user, ignore_fields={"Stage", "Status"})
        return jsonify(analytics_payload(rows, revenue_rows))
    except Exception as exc:
        app.logger.exception("Analytics API failed")
        return jsonify({"error": f"Unable to load analytics data: {exc}"}), 500


@app.route("/api/opportunities")
@login_required
def api_opportunities():
    try:
        # The cache is shared and treated as immutable; this endpoint may sort rows.
        rows = list(filtered_data(get_user()))
        query = request.args.get("q", "").strip().casefold()
        if query:
            rows = [r for r in rows if any(query in str(value).casefold() for value in list(r.values()) + list((r.get("_raw_data") or {}).values()) if value is not None)]
        meta = _CACHE.get("meta") or load_meta()
        if meta.get("data_type") == "Exploratory dataset":
            schema = meta.get("generic_schema") or {}
            columns = schema.get("columns", [])
            sort_field = request.args.get("sort_field", "")
            sort_order = request.args.get("sort_order", "desc").lower()
            if sort_field in columns:
                field_types = {item.get("name"): item.get("type") for item in schema.get("profiles", [])}
                field_type = field_types.get(sort_field)
                def generic_sort_key(row):
                    value = str((row.get("_raw_data") or {}).get(sort_field, "") or "").strip()
                    if not value:
                        return (0, "")
                    if field_type == "numeric":
                        return (1, parse_number(value, 0.0))
                    if field_type == "date":
                        return (1, parse_date(value) or value)
                    return (1, value.casefold())
                rows.sort(key=generic_sort_key, reverse=(sort_order != "asc"))
            display_rows = [{col: (r.get("_raw_data") or {}).get(col, "") for col in columns} for r in rows]
            return jsonify({"rows": display_rows, "columns": columns, "count": len(display_rows), "total": len(rows), "generic": True, "has_pipeline": False})
        sort = request.args.get("sort", "expected_revenue")
        descending = request.args.get("order", "desc").lower() != "asc"
        sort_fields = {
            "expected_revenue": "Expected_Revenue", "close_date": "Expected_Close_Date",
            "deal_value": "Deal_Value", "revenue": "Revenue",
        }
        field = sort_fields.get(sort, "Expected_Revenue")
        present = [r for r in rows if r.get(field) not in (None, "")]
        missing = [r for r in rows if r.get(field) in (None, "")]
        present.sort(key=lambda r: r.get(field), reverse=descending)
        # Copy only rows that are augmented for the response; keep the shared cache immutable.
        display = [dict(row) for row in (present + missing)]
        pipeline_mode = (_CACHE.get("meta") or load_meta()).get("data_type", "CRM sales pipeline") == "CRM sales pipeline"
        for row in display:
            row["Probability"] = round(float(row.get("Probability") or 0), 4) if pipeline_mode else None
            row["Is_Overdue"] = bool(row.get("Status") == "Open" and row.get("Expected_Close_Date") and row["Expected_Close_Date"] < today_iso())
        return jsonify({"rows": display, "count": len(display), "total": len(rows), "has_pipeline": pipeline_mode})
    except Exception as exc:
        app.logger.exception("Opportunity API failed")
        return jsonify({"error": f"Unable to load opportunities: {exc}"}), 500


@app.errorhandler(400)
def bad_request(error):
    description = getattr(error, "description", "The request could not be processed.")
    return render_template("error.html", code=400, message=description, user=get_user()), 400


@app.errorhandler(403)
def forbidden(_error):
    return render_template("error.html", code=403, message="Your account does not have permission to view this page.", user=get_user()), 403


@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, message="The page you requested could not be found.", user=get_user()), 404


@app.errorhandler(413)
def too_large(_error):
    return render_template("error.html", code=413, message="The uploaded file exceeds the 15 MB limit.", user=get_user()), 413


init_db()

if __name__ == "__main__":
    # Debug mode is off by default; set FLASK_DEBUG=1 only for local development.
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=os.environ.get("FLASK_DEBUG") == "1")
