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
        findings = {}
        schema = self._df.schema
        sample = self._df.limit(sample_rows)

        for field in schema.fields:
            col_name = field.name
            dtype = str(field.dataType)
            sensitivity = self._infer_sensitivity(col_name)
            pii_types = []

            if "String" in dtype:
                col_vals = [r[col_name] for r in sample.select(col_name).collect()
                            if r[col_name] is not None]
                pii_types = self._detect_pii(col_vals)

            findings[col_name] = {
                "dtype": dtype,
                "sensitivity": sensitivity,
                "pii_types": pii_types,
                "has_pii": len(pii_types) > 0,
            }

        return GovReport(self._table, findings)

    def _infer_sensitivity(self, col_name: str) -> str:
        lower = col_name.lower()
        for level, keywords in SENSITIVITY_KEYWORDS.items():
            if any(kw in lower for kw in keywords):
                return level
        return "NONE"

    def _detect_pii(self, values: list[str]) -> list[str]:
        detected = set()
        sample = values[:200]
        for pii_type, pattern in PII_PATTERNS.items():
            if any(re.search(pattern, str(v)) for v in sample):
                detected.add(pii_type)
        return list(detected)


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

    def apply_tags(self):
        """Write Unity Catalog column tags for sensitivity classification."""
        if not self.table:
            print("⚠️  No table name — cannot apply UC tags")
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
                    print(f"  ⚠️  Could not tag {col}: {e}")
        print(f"✅ Tags applied to {self.table}")

    def to_dict(self) -> dict:
        return self.findings
