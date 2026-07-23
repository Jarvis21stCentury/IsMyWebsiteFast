import streamlit as st
import pandas as pd
from datetime import datetime
from main import init_db, run_batch, add_summaries, flag_regressions, build_report

st.set_page_config(page_title="IsMyWebsiteFast", layout="wide")
init_db()
st.title("IsMyWebsiteFast")
st.caption("Tool that allows for businesses to get reports on how fast their website is!")

with st.sidebar:
    st.header("Settings")
    strategy = st.selectbox("Device", ["mobile", "desktop"], index=0)
    regression_threshold = st.slider("Regression threshold (points)", min_value=-50, max_value=-1, value=-5)

urls_input = st.text_area("Enter URLs", placeholder="", height=150)
run_button = st.button("Run Analysis", type="primary")

if run_button:
    urls = [u.strip() for u in urls_input.splitlines() if u.strip()]
    if not urls:
        st.error("Please enter only one url per line")
    else:
        progress = st.empty()
        with st.spinner(f"Analyzing {len(urls)} sites ..."):
            progress.info(f"Running checks for {strategy} ...")
            results = run_batch(urls, strategy=strategy)
            if results:
                progress.info("Generating summary...")
                results = add_summaries(results)
            regressions = flag_regressions(results, threshold=regression_threshold)
        progress.empty()
        
        if not results:
            st.error("No results! Check your URLs!")
        else:
            if regressions:
                st.warning(f"{len(regressions)} sites regressed by more than {abs(regression_threshold)} points since the last time.")
            
            df = pd.DataFrame(results)[["url", "strategy", "performance_score", "lcp", "cls", "tbt", "fcp"]]
            st.subheader("Results")
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.subheader("Reports")
            
            for r in results:
                delta_str = ""
                if "score_delta" in r:
                    sign = "+" if r["score_delta"] >= 0 else ""
                    delta_str = f" ({sign}{r['score_delta']} since last time)"

                with st.expander(f"{r['url']} - {r['performance_score']}/100{delta_str}"):
                    col1, col2, col3 = st.columns(3)
                    col1.metric("LCP", r["lcp"])
                    col2.metric("CLS", r["cls"])
                    col3.metric("TBT", r["tbt"])
                    if "summary" in r:
                        st.write(r["summary"])

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_path = f"report_{timestamp}.md"
            build_report(results, regressions, output_path=report_path)
            
            with open(report_path, "rb") as f:
                st.download_button("Download Report", data=f, file_name=report_path, mime="text/markdown",)