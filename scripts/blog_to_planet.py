#!/usr/bin/env python3
"""Reverse-publish tianjikuajing blog/*.md to 天机跨境 Knowledge Planet."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

GROUP_ID = "51115818158554"
MIRROR = Path("/workspace/zsxq_sync/blog_mirror")
MAPPING_PATH = Path("/workspace/zsxq_sync/mapping/sync.json")
FILE_LIST = Path("/tmp/blog_files.json")
STAGING = Path("/tmp/zsxq_publish_md")
REPORT_PATH = Path("/workspace/zsxq_sync/reports/blog_to_planet_report.json")
MAX_BODY = 48000  # chars; leave headroom
SLEEP_SEC = 1.5
TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(TZ).isoformat()


def parse_front_matter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n(.*)$", text, re.S)
    if not m:
        return {}, text
    fm: dict = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm, m.group(2).lstrip("\n")


def prepare_markdown(blog_path: str, raw: str) -> tuple[str, dict, str]:
    fm, body = parse_front_matter(raw)
    title = fm.get("title") or Path(blog_path).stem
    orig = fm.get("original_url") or fm.get("url") or ""
    github_url = f"https://github.com/yangyayun2016/tianjikuajing/blob/master/{blog_path}"
    parts = []
    if title:
        parts.append(f"# {title}\n")
    link = orig or github_url
    parts.append(f"> 原文：{link}\n")
    parts.append(f"> GitHub：{github_url}\n")
    parts.append(body)
    content = "\n".join(parts)
    truncated = False
    if len(content) > MAX_BODY:
        content = (
            content[:MAX_BODY]
            + f"\n\n…（正文过长已截断，完整版见原文：{link}）\n"
        )
        truncated = True
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return content, {**fm, "title": title, "original_url": orig, "truncated": truncated}, body_hash


def load_mapping() -> dict:
    if MAPPING_PATH.exists():
        return json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    return {"planet_to_git": [], "blog_to_planet": [], "notes": "", "groups": {}}


def save_mapping(data: dict) -> None:
    MAPPING_PATH.parent.mkdir(parents=True, exist_ok=True)
    MAPPING_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def index_blog_to_planet(entries: list) -> dict:
    return {e["blog_path"]: e for e in entries if e.get("blog_path")}


def run_create(md_path: Path) -> dict:
    cmd = [
        "zsxq-cli",
        "topic",
        "+create",
        "--group-id",
        GROUP_ID,
        "--markdown-file",
        str(md_path),
        "--json",
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    if p.returncode != 0:
        return {"success": False, "error": err or out or f"exit {p.returncode}", "raw": out}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"success": False, "error": f"non-json: {out[:500]}", "raw": out}


def run_edit(topic_id: str, text: str) -> dict:
    cmd = [
        "zsxq-cli",
        "topic",
        "+edit",
        "--topic-id",
        str(topic_id),
        "--text",
        text,
        "--json",
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    if p.returncode != 0:
        return {"success": False, "error": err or out or f"exit {p.returncode}", "raw": out}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"success": False, "error": f"non-json: {out[:500]}", "raw": out}


def main() -> int:
    files = json.loads(FILE_LIST.read_text(encoding="utf-8"))
    mapping = load_mapping()
    b2p = mapping.setdefault("blog_to_planet", [])
    by_path = index_blog_to_planet(b2p)

    report = {
        "started_at": now_iso(),
        "total": len(files),
        "created": [],
        "edited": [],
        "skipped": [],
        "failed": [],
    }

    STAGING.mkdir(parents=True, exist_ok=True)

    for i, blog_path in enumerate(files, 1):
        local = MIRROR / blog_path
        if not local.exists():
            report["failed"].append({"blog_path": blog_path, "error": "missing local mirror"})
            print(f"[{i}/{len(files)}] FAIL missing {blog_path}", flush=True)
            continue
        raw = local.read_text(encoding="utf-8")
        content, meta, body_hash = prepare_markdown(blog_path, raw)
        existing = by_path.get(blog_path)

        if existing and existing.get("content_hash") == body_hash and existing.get("topic_id"):
            report["skipped"].append(
                {"blog_path": blog_path, "topic_id": existing["topic_id"], "reason": "same_hash"}
            )
            print(f"[{i}/{len(files)}] SKIP {blog_path}", flush=True)
            continue

        safe_name = blog_path.replace("/", "__")
        md_path = STAGING / f"{safe_name}"
        md_path.write_text(content, encoding="utf-8")

        if existing and existing.get("topic_id") and existing.get("content_hash") != body_hash:
            print(f"[{i}/{len(files)}] EDIT {blog_path} -> {existing['topic_id']}", flush=True)
            res = run_edit(existing["topic_id"], content)
            if res.get("success") is False or (not res.get("topic") and "error" in res):
                # Some versions return topic without success key
                if not res.get("topic") and res.get("success") is not True:
                    err = res.get("error") or str(res)[:300]
                    report["failed"].append({"blog_path": blog_path, "error": err, "action": "edit"})
                    print(f"  FAIL edit: {err}", flush=True)
                    time.sleep(SLEEP_SEC)
                    continue
            topic_id = str(
                (res.get("topic") or {}).get("topic_id") or existing["topic_id"]
            )
            existing.update(
                {
                    "content_hash": body_hash,
                    "title": meta.get("title"),
                    "original_url": meta.get("original_url"),
                    "updated_at": now_iso(),
                    "truncated": meta.get("truncated", False),
                }
            )
            report["edited"].append({"blog_path": blog_path, "topic_id": topic_id})
            save_mapping(mapping)
            REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            time.sleep(SLEEP_SEC)
            continue

        print(f"[{i}/{len(files)}] CREATE {blog_path}", flush=True)
        res = run_create(md_path)
        topic = res.get("topic") or {}
        topic_id = topic.get("topic_id")
        if not topic_id:
            # retry once with stronger truncation if length error
            err = res.get("error") or str(res)[:400]
            if "长" in err or "limit" in err.lower() or "too" in err.lower() or len(content) > 20000:
                short = content[:20000] + f"\n\n…（已截断，完整版见原文：{meta.get('original_url') or blog_path}）\n"
                md_path.write_text(short, encoding="utf-8")
                print("  retry truncated 20k", flush=True)
                res = run_create(md_path)
                topic = res.get("topic") or {}
                topic_id = topic.get("topic_id")
            if not topic_id:
                err = res.get("error") or str(res)[:400]
                report["failed"].append({"blog_path": blog_path, "error": err, "action": "create"})
                print(f"  FAIL create: {err}", flush=True)
                save_mapping(mapping)
                REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                time.sleep(SLEEP_SEC)
                continue

        entry = {
            "blog_path": blog_path,
            "topic_id": str(topic_id),
            "content_hash": body_hash,
            "title": meta.get("title"),
            "original_url": meta.get("original_url"),
            "published_at": now_iso(),
            "truncated": meta.get("truncated", False),
            "group_id": GROUP_ID,
        }
        if existing:
            existing.update(entry)
        else:
            b2p.append(entry)
            by_path[blog_path] = entry
        report["created"].append({"blog_path": blog_path, "topic_id": str(topic_id)})
        print(f"  OK topic_id={topic_id}", flush=True)
        save_mapping(mapping)
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        time.sleep(SLEEP_SEC)

    report["finished_at"] = now_iso()
    report["counts"] = {
        "total": report["total"],
        "created": len(report["created"]),
        "edited": len(report["edited"]),
        "skipped": len(report["skipped"]),
        "failed": len(report["failed"]),
    }
    mapping["notes"] = (
        mapping.get("notes", "")
        + f"; blog_to_planet sync {report['finished_at']} created={report['counts']['created']} skipped={report['counts']['skipped']} failed={report['counts']['failed']}"
    )
    save_mapping(mapping)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("DONE", json.dumps(report["counts"], ensure_ascii=False), flush=True)
    return 0 if not report["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
