#!/usr/bin/env python3
"""每天跑一次：拉取 candidates.json 里的各个源 → 按过滤规则筛选 → 实测线路 → 只保留当天能播的，
输出 live/<region>[-extra].m3u 和 App 读取的 sources.json。只用标准库；只解析文本，不执行任何下载内容。

判定“可用”：HLS 清单可取且含分片，分片前 2KB 可读（>=500B）；非 HLS 直连流读到 >=4096B。
安全阀：某个源探测后存活比例 < MIN_ALIVE_RATIO 或总数为 0（多半是探测环境问题）时，该源保留上一次的输出，不覆盖。
"""
import concurrent.futures as cf
import json
import os
import re
import sys
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = "Mozilla/5.0 (Linux; Android 11; TV) AppleWebKit/537.36 Chrome/120 Safari/537.36"
TIMEOUT = 6
MIN_ALIVE_RATIO = 0.03


def http(url, limit, rng=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **({"Range": rng} if rng else {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        if r.status not in (200, 206):
            return None
        return r.read(limit)


def alive(url):
    try:
        body = http(url, 256 * 1024)
        if body is None:
            return False
        if body[:16].decode("utf-8", "ignore").lstrip("﻿ \r\n\t").startswith("#EXTM3U"):
            text, base = body.decode("utf-8", "ignore"), url
            for _ in range(2):
                items = [x.strip() for x in text.splitlines() if x.strip() and not x.startswith("#")]
                if not items:
                    return False
                first = urllib.parse.urljoin(base, items[0])
                if "#EXT-X-STREAM-INF" in text:
                    b2 = http(first, 256 * 1024)
                    if b2 is None:
                        return False
                    text, base = b2.decode("utf-8", "ignore"), first
                    continue
                seg = http(first, 2048, "bytes=0-2047")
                return seg is not None and len(seg) >= 500
            return False
        data = http(url, 4096)
        return data is not None and len(data) >= 4096
    except Exception:
        return False


def parse(text):
    lines = text.splitlines()
    out = []
    for i, l in enumerate(lines):
        if not l.startswith("#EXTINF"):
            continue
        url = ""
        for j in range(i + 1, min(i + 4, len(lines))):
            s = lines[j].strip()
            if s and not s.startswith("#"):
                url = s
                break
        if not url.startswith("http"):
            continue
        name = l.rsplit(",", 1)[-1].strip()
        g = re.search(r'group-title="([^"]*)"', l)
        out.append({"extinf": l, "name": name, "group": g.group(1) if g else "", "url": url})
    return out


def passes(src, e):
    g, n = e["group"], e["name"]
    inc_g = src.get("includeGroups")
    inc_n = src.get("includeNames", [])
    if inc_g is not None and g not in inc_g and not any(k.lower() in n.lower() for k in inc_n):
        return False
    if g in src.get("excludeGroups", []):
        return False
    return not any(k.lower() in n.lower() for k in src.get("excludeNames", []))


def region_of(src, e):
    n = e["name"].lower()
    if any(k.lower() in n for k in src.get("twNames", [])):
        return "TW"
    if any(k.lower() in n for k in src.get("cnNames", [])):
        return "CN"
    return src["region"]


def write_m3u(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for e in entries:
            f.write(e["extinf"] + "\n" + e["url"] + "\n")


def main():
    cfg = json.load(open(os.path.join(ROOT, "candidates.json"), encoding="utf-8"))
    live = os.path.join(ROOT, "live")
    os.makedirs(live, exist_ok=True)
    published = []
    report = []
    for src in cfg["sources"]:
        try:
            raw = http(src["url"], 30 * 1024 * 1024).decode("utf-8", "ignore")
        except Exception as ex:
            print(f"[{src['id']}] fetch failed: {ex}", file=sys.stderr)
            raw = None
        entries = [e for e in parse(raw) if passes(src, e)] if raw else []
        seen, uniq = set(), []
        for e in entries:
            if e["url"] not in seen:
                seen.add(e["url"])
                uniq.append(e)
        with cf.ThreadPoolExecutor(max_workers=64) as ex:
            ok = list(ex.map(lambda e: alive(e["url"]), uniq))
        good = [e for e, k in zip(uniq, ok) if k]
        ratio = len(good) / len(uniq) if uniq else 0
        # 按地区拆分输出（twNames 会把部分条目归到 TW）
        by_region = {}
        for e in good:
            by_region.setdefault(region_of(src, e), []).append(e)
        healthy = uniq and ratio >= MIN_ALIVE_RATIO
        report.append(f"{src['id']}: candidates={len(uniq)} alive={len(good)} ({100*ratio:.0f}%){'' if healthy else '  UNHEALTHY -> keep previous'}")
        regions = set(by_region) | {src["region"]}
        for r in sorted(regions):
            fid = src["id"] if r == src["region"] else f"{src['id']}-{r}"
            path = os.path.join(live, fid + ".m3u")
            if healthy:
                write_m3u(path, by_region.get(r, []))
            if os.path.exists(path):
                published.append({
                    "id": fid, "region": r, "primary": bool(src.get("primary")),
                    "url": cfg["raw_base"] + urllib.parse.quote(fid) + ".m3u",
                })
    out = {"version": 1, "sources": published}
    with open(os.path.join(ROOT, "sources.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("\n".join(report))


if __name__ == "__main__":
    main()
