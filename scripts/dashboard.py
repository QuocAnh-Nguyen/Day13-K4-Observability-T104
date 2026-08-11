from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.cli import configure_utf8_stdio

LOG_PATH = REPO_ROOT / "data" / "logs.jsonl"
DASHBOARD_CONFIG = REPO_ROOT / "config" / "dashboard.yaml"
SLO_CONFIG = REPO_ROOT / "config" / "slo.yaml"

WINDOW_MINUTES = 60
REFRESH_SECONDS = 30

TIME_KEY = "ts"
EVENTS = ["request_received", "response_sent", "request_failed"]


def percentile(values: pd.Series, p: int) -> float:
    if values.empty:
        return 0.0
    return float(values.quantile(p / 100.0))


def load_logs(path: Path = LOG_PATH) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    if TIME_KEY in df.columns:
        df[TIME_KEY] = pd.to_datetime(df[TIME_KEY], utc=True, errors="coerce")
        df = df.dropna(subset=[TIME_KEY])
    return df


def load_thresholds() -> dict:
    config = yaml.safe_load(SLO_CONFIG.read_text(encoding="utf-8"))
    slis = config.get("slis", {})
    return {
        "latency_p95_ms": slis.get("latency_p95_ms", {}).get("objective", 3000),
        "error_rate_pct": slis.get("error_rate_pct", {}).get("objective", 2),
        "cost_usd": slis.get("daily_cost_usd", {}).get("objective", 2.5),
        "quality": slis.get("quality_score_avg", {}).get("objective", 0.75),
        "tokens": 50000,
    }


@st.cache_data(show_spinner=False, ttl=REFRESH_SECONDS - 1)
def read_window(now_iso: str) -> pd.DataFrame:
    df = load_logs()
    if df.empty:
        return df
    now = datetime.fromisoformat(now_iso)
    cutoff = now - timedelta(minutes=WINDOW_MINUTES)
    return df[df[TIME_KEY] >= cutoff]


def render_latency(df: pd.DataFrame, threshold: float) -> None:
    st.subheader("1. Latency percentiles")
    sent = df[df["event"] == "response_sent"]
    if sent.empty:
        st.info("No response_sent events in window")
        return
    lat = sent["latency_ms"].dropna()
    p50, p95, p99 = percentile(lat, 50), percentile(lat, 95), percentile(lat, 99)
    c1, c2, c3 = st.columns(3)
    c1.metric("p50", f"{p50:.0f} ms")
    c2.metric("p95", f"{p95:.0f} ms", delta=None)
    c3.metric("p99", f"{p99:.0f} ms")
    st.caption(f"unit: ms | SLO p95 ≤ {threshold:.0f} ms")

    series = sent.set_index(TIME_KEY)["latency_ms"].sort_index().resample("1min").mean()
    chart = pd.DataFrame({"time": series.index, "latency_ms": series.values})
    import altair as alt

    base = alt.Chart(chart).mark_line(point=True).encode(
        x=alt.X("time:T", title="time"),
        y=alt.Y("latency_ms:Q", title="latency (ms)"),
    )
    rule = alt.Chart(pd.DataFrame({"y": [threshold]})).mark_rule(
        color="red", strokeDash=[4, 4]
    ).encode(y="y:Q")
    st.altair_chart(base + rule, use_container_width=True)


def render_traffic(df: pd.DataFrame, _threshold: float) -> None:
    st.subheader("2. Traffic")
    recv = df[df["event"] == "request_received"]
    st.metric("Requests in window", int(len(recv)))
    if not recv.empty:
        rpm = recv.set_index(TIME_KEY).resample("1min").size().rename("requests").reset_index()
        import altair as alt

        chart = alt.Chart(rpm).mark_bar().encode(
            x=alt.X("time:T", title="time"),
            y=alt.Y("requests:Q", title="requests / min"),
        )
        st.altair_chart(chart, use_container_width=True)
    else:
        st.info("No request_received events in window")


def render_errors(df: pd.DataFrame, threshold: float) -> None:
    st.subheader("3. Error rate and breakdown")
    recv = len(df[df["event"] == "request_received"])
    failed = df[df["event"] == "request_failed"]
    rate = (len(failed) / recv * 100) if recv else 0.0
    st.metric("Error rate", f"{rate:.2f}%", delta=None)
    st.caption(f"unit: percent | SLO ≤ {threshold:.1f}%")
    if not failed.empty:
        breakdown = failed["error_type"].fillna("unknown").value_counts().rename_axis("error_type").reset_index(name="count")
        st.dataframe(breakdown, use_container_width=True)
    else:
        st.info("No request_failed events in window")


def render_cost(df: pd.DataFrame, threshold: float) -> None:
    st.subheader("4. Cost")
    sent = df[df["event"] == "response_sent"]
    if sent.empty:
        st.info("No response_sent events in window")
        return
    total = float(sent["cost_usd"].sum())
    st.metric("Total cost (window)", f"${total:.4f}")
    st.caption(f"unit: USD | SLO ≤ ${threshold:.2f}")

    series = sent.set_index(TIME_KEY)["cost_usd"].sort_index().resample("1min").sum()
    cost_df = pd.DataFrame({"time": series.index, "cost_usd": series.values})
    import altair as alt

    base = alt.Chart(cost_df).mark_line().encode(
        x=alt.X("time:T", title="time"),
        y=alt.Y("cost_usd:Q", title="USD"),
    )
    rule = alt.Chart(pd.DataFrame({"y": [threshold]})).mark_rule(
        color="red", strokeDash=[4, 4]
    ).encode(y="y:Q")
    st.altair_chart(base + rule, use_container_width=True)


def render_tokens(df: pd.DataFrame, threshold: float) -> None:
    st.subheader("5. Token usage")
    sent = df[df["event"] == "response_sent"]
    if sent.empty:
        st.info("No response_sent events in window")
        return
    tin = float(sent["tokens_in"].sum())
    tout = float(sent["tokens_out"].sum())
    c1, c2 = st.columns(2)
    c1.metric("Input tokens", f"{tin:,.0f}")
    c2.metric("Output tokens", f"{tout:,.0f}")
    st.caption(f"unit: tokens | SLO window total ≤ {threshold:,.0f}")


def render_quality(df: pd.DataFrame, threshold: float) -> None:
    st.subheader("6. Quality proxy")
    sent = df[df["event"] == "response_sent"]
    if sent.empty:
        st.info("No response_sent events in window")
        return
    avg = float(sent["quality_score"].mean())
    st.metric("Mean quality score", f"{avg:.2f}")
    st.caption(f"unit: score_0_to_1 | SLO ≥ {threshold:.2f}")

    series = sent.set_index(TIME_KEY)["quality_score"].sort_index().resample("1min").mean()
    q_df = pd.DataFrame({"time": series.index, "quality_score": series.values})
    import altair as alt

    base = alt.Chart(q_df).mark_line().encode(
        x=alt.X("time:T", title="time"),
        y=alt.Y("quality_score:Q", title="score"),
    )
    rule = alt.Chart(pd.DataFrame({"y": [threshold]})).mark_rule(
        color="red", strokeDash=[4, 4]
    ).encode(y="y:Q")
    st.altair_chart(base + rule, use_container_width=True)


def render_panels(thresholds: dict) -> None:
    now = datetime.now(timezone.utc)
    df = read_window(now.isoformat())

    st.caption(
        f"Window: last {WINDOW_MINUTES} min (UTC) | auto-refresh every {REFRESH_SECONDS}s"
    )

    render_latency(df, thresholds["latency_p95_ms"])
    render_traffic(df, thresholds.get("traffic", 1))
    render_errors(df, thresholds["error_rate_pct"])
    render_cost(df, thresholds["cost_usd"])
    render_tokens(df, thresholds["tokens"])
    render_quality(df, thresholds["quality"])

    st.caption(f"Rows in window: {len(df)}")


def main() -> None:
    configure_utf8_stdio()
    st.set_page_config(page_title="Day 13 AI Observability", page_icon="📈", layout="wide")
    st.title("Day 13 AI Observability")

    thresholds = load_thresholds()

    with st.fragment(run_every=REFRESH_SECONDS):
        render_panels(thresholds)


if __name__ == "__main__":
    main()
