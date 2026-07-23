import os
import requests
import time
import sqlite3
import logging
import argparse
from datetime import datetime
import anthropic
from dotenv import load_dotenv

load_dotenv()

psi_key = os.environ.get("PSI_API_KEY", "")
psi_endpoint = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
claude_key = os.environ.get("ANTHROPIC_API_KEY", "")
path = "audits.db"
retries = 3
retry_breaktime = 5  # PSI throttles hard if you hit it too fast, this seems to be enough
request_delay = 1.5  # stay under the free tier quota

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S",)
logger = logging.getLogger("site_auditor")

def audit_url(url, strategy="mobile"):
    params = {
        "url": url,
        "key": psi_key,
        "strategy": strategy,
        "category": ["performance"],
    }

    last_error = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(psi_endpoint, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            return _parse_psi_response(data, url, strategy)
        except requests.exceptions.RequestException as e:
            last_error = e
            logger.warning(f"Attempt {attempt}/{retries} failed")
            if attempt < retries:
                time.sleep(retry_breaktime * attempt)
        except KeyError as e:
            raise RuntimeError(f"Unexpected response, missing key: {e}")

    raise RuntimeError(f"All {retries} attempts failed: {last_error}")

def _parse_psi_response(data, url, strategy):
    lighthouse = data["lighthouseResult"]
    audits = lighthouse["audits"]

    return {
        "url": url,
        "strategy": strategy,
        "performance_score": round(lighthouse["categories"]["performance"]["score"] * 100),
        "lcp": audits["largest-contentful-paint"]["displayValue"],
        "cls": audits["cumulative-layout-shift"]["displayValue"],
        "tbt": audits["total-blocking-time"]["displayValue"],
        "fcp": audits["first-contentful-paint"]["displayValue"],
        "speed_index": audits["speed-index"]["displayValue"],
        "timestamp": datetime.now().isoformat(),
    }

def init_db(db_path=path):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        create table if not exists audits (id integer primary key autoincrement, url text not null, strategy text not null, performance_score real, lcp text, cls text, tbt text, fcp text, speed_index text, timestamp text not null)
    """)
    conn.commit()
    conn.close()

def save_result(result, db_path=path):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        Insert into audits (url, strategy, performance_score, lcp, cls, tbt, fcp, speed_index, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            result["url"], result["strategy"], result["performance_score"],
            result["lcp"], result["cls"], result["tbt"], result["fcp"],
            result["speed_index"], result["timestamp"],
        ),
    )
    conn.commit()
    conn.close()

def get_previous_score(url, strategy, db_path=path):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            "select performance_score, timestamp from audits where url = ? and strategy = ? order by timestamp desc limit 2",
            (url, strategy),
        )
        rows = cursor.fetchall()
    return rows[1] if len(rows) > 1 else None

def run_batch(urls, strategy="mobile", db_path=path):
    results = []
    for url in urls:
        logger.info(f"Auditing {url}")
        try:
            result = audit_url(url, strategy)
            # print(result)  # leave this here, useful when psi starts returning weird numbers
            save_result(result, db_path)

            previous = get_previous_score(url, strategy, db_path)
            if previous:
                delta = result["performance_score"] - previous[0]
                result["score_delta"] = delta
                arrow = "up" if delta > 0 else "down" if delta < 0 else "flat"
                logger.info(f"  Score: {result['performance_score']}/100 ({arrow} {abs(delta)} pts)")
            else:
                logger.info(f"  Score: {result['performance_score']}/100 (first audit)")
            
            results.append(result)
        except RuntimeError as e:
            logger.error(f"Failed: {e}")
        
        time.sleep(request_delay)
    
    return results

def summarize(result):
    delta_line = ""
    if "score_delta" in result:
        direction = "improved" if result["score_delta"] > 0 else "gotten worse" if result["score_delta"] < 0 else "stayed the same"
        delta_line = f"\nscore has {direction} by {abs(result['score_delta'])} points since last time"

    prompt = f"""pagespeed results for {result["url"]} ({result["strategy"]}):
score {result["performance_score"]}/100, LCP {result["lcp"]}, CLS {result["cls"]}, TBT {result["tbt"]}, FCP {result["fcp"]}, speed index {result["speed_index"]}
{delta_line}

turn this into 3 short sentences a non-technical business owner would get - is the site fast or slow and trending up or down, what's the biggest problem, and what's the one thing to fix first. keep it simple, no jargon"""

    client = anthropic.Anthropic(api_key=claude_key)
    message = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text

def add_summaries(results):
    for result in results:
        logger.info(f"Summarizing {result['url']} ...")
        try:
            result["summary"] = summarize(result)
        except Exception as e:
            logger.error(f"Summary failed for {result['url']}: {e}")
            result["summary"] = "Summary unavailable due to an error"
    return results

def flag_regressions(results, threshold=-5):
    regressions = [r for r in results if r.get("score_delta") is not None and r["score_delta"] <= threshold]
    if regressions:
        logger.warning(f"{len(regressions)} site regressed by more than {abs(threshold)} points")
        for r in regressions:
            logger.warning(f"{r['url']}: dropped {abs(r['score_delta'])} points")
    return regressions

def load_urls(path):
    urls = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls

def build_report(results, regressions, output_path="report.md"):
    with open(output_path, "w") as f:
        f.write("# Website Performance Report\n\n")

        if regressions:
            f.write("## Regressions\n\n")
            for r in regressions:
                f.write(f"- {r['url']} dropped {abs(r['score_delta'])} points (now {r['performance_score']}/100)\n")
            f.write("\n---\n\n")

        for r in results:
            f.write(f"## {r['url']}\n\n")
            f.write(f"**Performance score:** {r['performance_score']}/100")
            if "score_delta" in r:
                sign = "+" if r["score_delta"] >= 0 else ""
                f.write(f" ({sign}{r['score_delta']} since last time)")
            f.write("\n\n")

            f.write(f"- Largest Contentful Paint: {r['lcp']}\n")
            f.write(f"- Cumulative Layout Shift: {r['cls']}\n")
            f.write(f"- Total Blocking Time: {r['tbt']}\n")
            f.write(f"- First Contentful Paint: {r['fcp']}\n")
            f.write(f"- Speed Index: {r['speed_index']}\n\n")

            if "summary" in r:
                f.write(f"**Summary:** {r['summary']}\n\n")

            f.write("---\n\n")

    logger.info(f"Report written to {output_path}")

def main():
    parser = argparse.ArgumentParser(
        description="Audit website performance with PageSpeed Insights and track score changes over time."
    )
    parser.add_argument("urls", help="Path to a text file with one URL per line")
    parser.add_argument("--strategy", choices=["mobile", "desktop"], default="mobile", help="Device strategy to test (default: mobile)")
    parser.add_argument("--db", default=path, help="sqlite file to store history in, defaults to audits.db")
    parser.add_argument("--output", default="report.md", help="Path to write the markdown report")
    parser.add_argument("--summarize", action="store_true", help="Generate plain-language summaries with Claude (requires ANTHROPIC_API_KEY)")
    args = parser.parse_args()

    if not psi_key:
        logger.error("PSI_API_KEY environment variable is not set")
        raise SystemExit(1)
    if args.summarize and not claude_key:
        logger.error("--summarize requires the ANTHROPIC_API_KEY environment variable")
        raise SystemExit(1)

    urls = load_urls(args.urls)
    if not urls:
        logger.error(f"No URLs found in {args.urls}")
        raise SystemExit(1)

    init_db(args.db)
    results = run_batch(urls, strategy=args.strategy, db_path=args.db)

    if args.summarize:
        results = add_summaries(results)

    regressions = flag_regressions(results)
    build_report(results, regressions, output_path=args.output)

if __name__ == "__main__":
    main()
