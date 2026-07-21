from __future__ import annotations
from typing import Optional
import re


PII_PATTERNS = {
    "email": r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+",
    "phone": r"\+?\d[\d\s\-().]{7,}\d",
    "credit_card": r"\b(?:\d[ -]?){13,16}\b",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "passport": r"\b[A-Z]{1,2}\d{6,9}\b",
    "national_id": r"\b\d{3}-\d{3}-\d{4}-\d\b",
}

SENSITIVITY_KEYWORDS = {
    "HIGH": ["salary", "income", "password", "secret", "credit_card", "ssn", "passport",
             "national_id", "emirates_id", "iban", "account_number"],
    "MEDIUM": ["email", "phone", "address", "dob", "birth", "gender", "nationality"],
    "LOW": ["name", "city", "country", "region", "department"],
}

# How many sampled values per column are regex-scanned for PII
_PII_VALUE_SAMPLE = 200


def luhn_valid(digits: str) -> bool:
    """Luhn checksum — filters the 13-16-digit credit_card regex's false
    positives (account numbers, tracking ids) down to plausible card numbers."""
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def detect_pii_in_values(values: list) -> list[str]:
    """Pure-Python PII detection over a list of sampled string values."""
    detected = set()
    sample = values[:_PII_VALUE_SAMPLE]
    for pii_type, pattern in PII_PATTERNS.items():
        for v in sample:
            m = re.search(pattern, str(v))
            if not m:
                continue
            if pii_type == "credit_card" and not luhn_valid(re.sub(r"[ -]", "", m.group())):
                continue
            detected.add(pii_type)
            break
    return sorted(detected)


def infer_sensitivity(col_name: str) -> str:
    lower = col_name.lower()
    for level, keywords in SENSITIVITY_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return level
    return "NONE"


def run_scan(table: str = None, df=None, sample_rows: int = 1000) -> "GovReport":
    """Scan a UC table (or DataFrame) for PII and sensitivity — the
    top-level entrypoint; equivalent to GovernanceScanner(...).scan()."""
    return GovernanceScanner(df=df, table=table).scan(sample_rows=sample_rows)


class GovernanceScanner:
    """
    Scan Databricks tables for PII, classify sensitivity, and apply tags.

    Usage::
        scanner = GovernanceScanner(table="catalog.schema.customers")
        report = scanner.scan()
        report.display()
        report.apply_tags()   # writes UC column tags
    """

    def __init__(self, df=None, table: str = None):
        self._table = table
        self._df = self._load(df, table)

    def _load(self, df, table):
        if df is not None:
            return df
        from pyspark.sql import SparkSession
        return SparkSession.getActiveSession().table(table)

    def scan(self, sample_rows: int = 1000) -> "GovReport":
        schema = self._df.schema
        string_cols = [f.name for f in schema.fields if "String" in str(f.dataType)]

        # One collect() for the whole sample — not one Spark job per column.
        sampled: dict[str, list] = {c: [] for c in string_cols}
        if string_cols:
            for row in self._df.select(*string_cols).limit(sample_rows).collect():
                for col in string_cols:
                    value = row[col]
                    if value is not None:
                        sampled[col].append(value)

        findings = {}
        for field in schema.fields:
            pii_types = detect_pii_in_values(sampled.get(field.name, []))
            findings[field.name] = {
                "dtype": str(field.dataType),
                "sensitivity": infer_sensitivity(field.name),
                "pii_types": pii_types,
                "has_pii": len(pii_types) > 0,
            }

        return GovReport(self._table, findings)


class GovReport:
    def __init__(self, table: Optional[str], findings: dict):
        self.table = table
        self.findings = findings

    def display(self):
        print(f"Governance scan: {self.table or 'DataFrame'}")
        print(f"{'Column':<30} {'Sensitivity':<12} {'PII Types'}")
        print("-" * 65)
        for col, info in self.findings.items():
            pii = ", ".join(info["pii_types"]) or "—"
            print(f"{col:<30} {info['sensitivity']:<12} {pii}")

    def rows(self) -> list[dict]:
        """Findings as a list of row-dicts, ready for dashui.data_table_html()."""
        return [
            {
                "column": col,
                "dtype": info["dtype"],
                "sensitivity": info["sensitivity"],
                "pii_types": ", ".join(info["pii_types"]) or "—",
            }
            for col, info in self.findings.items()
        ]

    def apply_tags(self):
        """Write Unity Catalog column tags for sensitivity classification."""
        if not self.table:
            print("Warning: no table name — cannot apply UC tags")
            return
        from pyspark.sql import SparkSession
        spark = SparkSession.getActiveSession()
        for col, info in self.findings.items():
            if info["sensitivity"] != "NONE":
                try:
                    spark.sql(
                        f"ALTER TABLE {self.table} ALTER COLUMN `{col}` "
                        f"SET TAGS ('sensitivity' = '{info['sensitivity']}')"
                    )
                except Exception as e:
                    print(f"  Warning: could not tag {col}: {e}")
        print(f"Tags applied to {self.table}")

    def to_dict(self) -> dict:
        return self.findings
