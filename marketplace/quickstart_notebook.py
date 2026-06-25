# Databricks notebook source
# MAGIC %md
# MAGIC # dash-gov — Data Governance
# MAGIC
# MAGIC Scan tables for PII and apply Unity Catalog sensitivity tags.
# MAGIC
# MAGIC **Install and launch:**

# COMMAND ----------

# MAGIC %pip install dash-gov

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import dashgov
dashgov.launch()

# COMMAND ----------
# MAGIC %md
# MAGIC ## Python API (optional — for automation)
# MAGIC
# MAGIC ```python
# MAGIC import dashgov
# MAGIC # See docs/api/ for full API reference
# MAGIC ```
