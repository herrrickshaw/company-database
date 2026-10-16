#!/usr/bin/env python3
"""Pull Finland's PRH open-data company register (100/page) into raw/fi/page_N.json. Resumable; 8 threads, polite."""
import json, pathlib, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
OUT = pathlib.Path(__file__).parent / "raw" / "fi"; OUT.mkdir(parents=True, exist_ok=True)
URL = "https://avoindata.prh.fi/opendata-ytj-api/v3/companies?page={}"
def get(p):
    f = OUT / f"page_{p}.json"
    if f.exists(): return p, 0
    for attempt in range(6):
        try:
            data = urllib.request.urlopen(urllib.request.Request(URL.format(p), headers={"User-Agent": "company-lists/1.0"}), timeout=60).read()
            f.write_bytes(data); time.sleep(0.15); return p, len(json.loads(data)["companies"])
        except Exception as e:
            time.sleep(2 ** attempt)
    return p, -1
total = json.load(urllib.request.urlopen(URL.format(1)))["totalResults"]; pages = -(-total // 100)
print("total", total, "pages", pages, flush=True)
fails = 0
with ThreadPoolExecutor(8) as ex:
    for i, (p, n) in enumerate(ex.map(get, range(1, pages + 1))):
        if n < 0: fails += 1
        if i % 500 == 0: print(i, "pages done, fails", fails, flush=True)
print("done, fails", fails)
