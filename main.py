import os
import requests
import time
import json
import sqlite3
import logging
import argparse
from datetime import datetime

import anthropic

psi_key = os.environ.get("PSI_API_KEY", "")
psi_endpoint = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
claude_key = os.environ.get("ANTHROPIC_API_KEY", "")
path = "audits.db"
retries = 3
retry_breaktime = 5
request_delay = 1.5

ai = anthropic.Anthropic(api_key=claude_key)

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

    raise RuntimeError(f"All {retries} attempts failed")

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
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        """select performance_score, timestamp from audits where url = ? and strategy = ? order by timestamp desc limit 2""",
        (url, strategy),
    )
    rows = cursor.fetchall()
    conn.close()
    return rows[1] if len(rows) > 1 else None

def run_batch(urls, strategy="mobile", db_path=path):
    results = []
    for url in urls:
        logger.info(f"Auditing {url}")
        try:
            result = audit_url(url, strategy)
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