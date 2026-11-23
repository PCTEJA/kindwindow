"""Spark engine for KindWindow window features.

JVM-only design (this VM cannot run Python workers — see
``PYTHON_WORKER_LIMITATION`` below, verified by the coordinator and
re-checked in this step):

* Input is read with ``spark.read.json`` / ``spark.read.parquet`` — the
  JVM readers. No ``createDataFrame`` from Python objects, no Python
  UDFs anywhere in the hot path, no ``.collect()`` / ``.toPandas()`` on
  full data (tests collect only the 3-row golden fixture / 1-row
  synthetic result, i.e. the scored output, never the event stream).
* All feature logic is native DataFrame expressions: a dense per-home
  5-minute grid built with ``explode(sequence(...))`` (JVM), per-bucket
  aggregation, and Spark window functions for the preceding 1/6/24
  buckets, the target positive count over the current + next five
  buckets, and the running maximum prior event timestamp for recency.
* Timestamps are treated as naive local ticks: the session timezone is
  forced to UTC so Spark never reinterprets local date/time semantics,
  and all arithmetic is integer epoch seconds.

Method (kit 04_DATA_AND_PIPELINES.md, "Engine implementation"):

1. Per valid home/segment (a segment is one home's continuous file
   span; the source member/file is part of the segment key, so
   cross-file outages are boundaries and are never bridged), form a
   dense 5-minute grid *including empty buckets* from the first to the
   last bucket of the segment. The grid is built per segment, never as
   a full cross-product of homes x global time.
2. Aggregate observed events onto their bucket: total count, positive
   count, door count, and the bucket's latest exact event epoch.
3. Historical sums over the preceding 1/6/24 buckets give
   count_5m / count_30m / count_120m (all accepted events) and
   door_30m (preceding 6 buckets). Recency is ``t`` minus the maximum
   event epoch over all strictly earlier buckets.
4. The positive target count is the sum of positive counts over the
   current + next five buckets; ``y = 1`` iff that count is > 0.
5. Sample 30-minute origins: buckets on a half-hour boundary with
   ``t >= first_event + 120m`` and ``t + 30m <= last_event``.

Lookback / halo across physical partitions: the grid and all window
functions are computed over the whole segment *before* any month
partitioning, so history and recency carry across month boundaries
within a valid continuous file — an event in the prior month changes
the first next-month output (covered by
``tests/test_spark_engine.py::test_spark_prior_month_carry``). The
forward label halo is the up-to-five buckets after the last scored
origin, which exist in the grid only to supply target counts and are
dropped with the rest of the grid once origins are emitted. Month
(``year_month``) is derived from the scored ``t`` only at Parquet
write time, as a partition column. Cross-file outages remain
boundaries because segments never share a grid.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F

from kindwindow.engines.common import (
    BUCKET_SECONDS,
    DOOR_BUCKETS,
    HISTORY_BUCKETS,
    ORIGIN_SECONDS,
    OUTPUT_COLUMNS,
    PARQUET_CODEC,
    PARTITION_COLUMNS,
    TARGET_BUCKETS,
    TARGET_SECONDS,
    WARMUP_SECONDS,
)

__all__ = [
    "PYTHON_WORKER_LIMITATION",
    "build_windows",
    "explain_plan",
    "get_spark",
    "load_events",
    "write_windows",
]

PYTHON_WORKER_LIMITATION = (
    "Python workers fail in this VM: createDataFrame / Python UDFs raise "
    "java.net.SocketException: Broken pipe. This engine therefore uses only "
    "Spark's JVM readers (spark.read.json / .parquet) and native DataFrame "
    "expressions; see module docstring."
)

_TIME_FORMAT = "yyyy-MM-dd'T'HH:mm:ss"


def get_spark(app_name: str = "KindWindowSpark") -> SparkSession:
    """Build (or reuse) a local SparkSession with VM-safe settings.

    Sets ``PYSPARK_PYTHON`` to the current interpreter and
    ``SPARK_LOCAL_IP=127.0.0.1`` when unset, forces the session timezone
    to UTC (local-tick semantics, no DST reinterpretation), and uses
    ``local[2]`` so the fixture workload stays bounded.
    """
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)
    os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
    return (
        SparkSession.builder.appName(app_name)
        .master("local[2]")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.driver.host", "127.0.0.1")
        .config("spark.driver.bindAddress", "127.0.0.1")
        .getOrCreate()
    )


def load_events(spark: SparkSession, path: str | Path) -> DataFrame:
    """Read normalized events with the JVM JSON reader.

    Expected JSONL fields: ``home, member, row, time, sensor, type,
    message``. ``time`` is local ``YYYY-MM-DDTHH:MM:SS``. Returns a
    DataFrame with ``home, segment, epoch`` (long epoch seconds) plus
    ``is_positive`` / ``is_door`` flags (0/1 ints). No Python worker is
    involved: parsing and flagging are native expressions.
    """
    raw = spark.read.json(str(path))
    missing = {"home", "time", "type", "message"} - set(raw.columns)
    if missing:
        raise ValueError(f"events file {path} is missing columns: {sorted(missing)}")
    member = F.coalesce(F.col("member"), F.lit("")) if "member" in raw.columns else F.lit("")
    ts = F.to_timestamp(F.col("time"), _TIME_FORMAT)
    return (
        raw.withColumn("ts", ts)
        .withColumn("epoch", F.unix_timestamp(F.col("ts")).cast("long"))
        .withColumn("segment", F.concat_ws("|", F.col("home"), member))
        .withColumn(
            "is_positive",
            F.when(
                (F.col("type") == "motion") & (F.col("message") == "ON"),
                F.lit(1),
            )
            .when(
                (F.col("type") == "door") & F.col("message").isin("OPEN", "CLOSE"),
                F.lit(1),
            )
            .otherwise(F.lit(0))
            .cast("int"),
        )
        .withColumn(
            "is_door",
            F.when(F.col("type") == "door", F.lit(1)).otherwise(F.lit(0)).cast("int"),
        )
        .filter(F.col("ts").isNotNull() & F.col("epoch").isNotNull())
        .select("home", "segment", "epoch", "is_positive", "is_door")
    )


def build_windows(events: DataFrame) -> DataFrame:
    """Compute scored windows from an events DataFrame (see module docstring).

    Returns exactly ``OUTPUT_COLUMNS``: home, t (``YYYY-MM-DDTHH:MM:SS``),
    count_5m, count_30m, count_120m, door_30m, recency_seconds, y — all
    integer columns. Only scored origins are emitted; grid buckets used
    solely as history/label halo are dropped here.
    """
    # Per-bucket aggregates: counts and the bucket's latest exact event time.
    bucketed = (
        events.withColumn("bucket", F.col("epoch") - (F.col("epoch") % BUCKET_SECONDS))
        .groupBy("home", "segment", "bucket")
        .agg(
            F.count("*").cast("long").alias("n"),
            F.sum("is_positive").cast("long").alias("n_pos"),
            F.sum("is_door").cast("long").alias("n_door"),
            F.max("epoch").alias("bucket_max_epoch"),
        )
    )

    # Per-segment span, for the dense grid and the eligibility bounds.
    spans = (
        events.groupBy("home", "segment")
        .agg(
            F.min("epoch").alias("min_epoch"),
            F.max("epoch").alias("max_epoch"),
        )
        .withColumn("min_bucket", F.col("min_epoch") - (F.col("min_epoch") % BUCKET_SECONDS))
        .withColumn("max_bucket", F.col("max_epoch") - (F.col("max_epoch") % BUCKET_SECONDS))
    )

    # Dense per-segment 5-minute grid, including empty buckets. Built per
    # segment with sequence(), never a homes x global-time cross-product.
    grid = spans.select(
        "home",
        "segment",
        "min_epoch",
        "max_epoch",
        F.explode(
            F.sequence(F.col("min_bucket"), F.col("max_bucket"), F.lit(BUCKET_SECONDS))
        ).alias("bucket"),
    )

    dense = (
        grid.join(bucketed, on=["home", "segment", "bucket"], how="left")
        .withColumn("n", F.coalesce(F.col("n"), F.lit(0)))
        .withColumn("n_pos", F.coalesce(F.col("n_pos"), F.lit(0)))
        .withColumn("n_door", F.coalesce(F.col("n_door"), F.lit(0)))
    )

    w = Window.partitionBy("segment").orderBy("bucket")

    def _prev_sum(col_name: str, buckets: int) -> F.Column:
        return F.sum(col_name).over(w.rowsBetween(-buckets, -1))

    featured = (
        dense.withColumn("count_5m", _prev_sum("n", HISTORY_BUCKETS["count_5m"]))
        .withColumn("count_30m", _prev_sum("n", HISTORY_BUCKETS["count_30m"]))
        .withColumn("count_120m", _prev_sum("n", HISTORY_BUCKETS["count_120m"]))
        .withColumn("door_30m", _prev_sum("n_door", DOOR_BUCKETS))
        .withColumn(
            "prev_max_epoch",
            F.max("bucket_max_epoch").over(w.rowsBetween(Window.unboundedPreceding, -1)),
        )
        .withColumn(
            "target_pos",
            F.sum("n_pos").over(w.rowsBetween(0, TARGET_BUCKETS - 1)),
        )
    )

    # Scored origins only: half-hour boundary, >=120m of history, and a
    # complete [t, t+30m) target inside the segment span.
    origins = (
        featured.filter(
            (F.col("bucket") % ORIGIN_SECONDS == 0)
            & (F.col("bucket") >= F.col("min_epoch") + WARMUP_SECONDS)
            & (F.col("bucket") + TARGET_SECONDS <= F.col("max_epoch"))
        )
        .withColumn("t", F.from_unixtime(F.col("bucket"), _TIME_FORMAT))
        .withColumn("recency_seconds", (F.col("bucket") - F.col("prev_max_epoch")).cast("long"))
        .withColumn("y", F.when(F.col("target_pos") > 0, F.lit(1)).otherwise(F.lit(0)).cast("int"))
        .select(
            "home",
            "t",
            F.col("count_5m").cast("int"),
            F.col("count_30m").cast("int"),
            F.col("count_120m").cast("int"),
            F.col("door_30m").cast("int"),
            "recency_seconds",
            "y",
        )
        .orderBy("home", "t")
    )
    # Guarantee the golden column order.
    return origins.select(*OUTPUT_COLUMNS)


def write_windows(windows: DataFrame, output: str | Path) -> Path:
    """Write windows as Snappy Parquet, partitioned by home + year-month.

    ``year_month`` is derived from the scored ``t`` after all features
    are computed, so month partitioning never truncates history/recency.
    Returns the output path.
    """
    out = Path(output)
    (
        windows.withColumn("year_month", F.substring(F.col("t"), 1, 7))
        .write.mode("overwrite")
        .partitionBy(*PARTITION_COLUMNS)
        .option("compression", PARQUET_CODEC)
        .parquet(str(out))
    )
    return out


def explain_plan(df: DataFrame) -> str:
    """Return the (JVM) query plan of ``df`` as text, for test artifacts."""
    try:
        return df._jdf.queryExecution().toString()
    except Exception:  # pragma: no cover - fallback if internals change
        return "(query plan unavailable via _jdf.queryExecution)"
