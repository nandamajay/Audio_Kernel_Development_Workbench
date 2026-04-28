"""Upstream parser/classification engine for live + offline sources."""

from __future__ import annotations

import gzip
import json
import mailbox
import os
import re
from collections import defaultdict
from datetime import datetime
from email import policy
from email.parser import BytesParser
from email.message import Message
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple


MERGE_HINT_RE = re.compile(r"\b(applied|queued|picked up|will apply)\b", re.IGNORECASE)
REVIEWED_BY_RE = re.compile(r"^Reviewed-by:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
PATCH_SUBJECT_RE = re.compile(r"\[PATCH(?:\s+v(?P<v>\d+))?(?:\s+(?P<i>\d+)\/(?P<n>\d+))?[^\]]*\]", re.IGNORECASE)
SUBJECT_PREFIX_RE = re.compile(r"^(re:\s*)+", re.IGNORECASE)
DIFF_SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")


def _to_dt(raw: str | None) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt is None:
            return None
        return dt.replace(tzinfo=None)
    except Exception:
        return None


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    return dt.date().isoformat()


def _as_text(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="replace")
    return str(payload)


def _message_body(msg: Message) -> str:
    if msg.is_multipart():
        parts: List[str] = []
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition", "")).lower()
            if ctype == "text/plain" and "attachment" not in disp:
                try:
                    charset = part.get_content_charset() or "utf-8"
                    parts.append(part.get_payload(decode=True).decode(charset, errors="replace"))
                except Exception:
                    parts.append(_as_text(part.get_payload()))
        return "\n".join(parts)
    try:
        charset = msg.get_content_charset() or "utf-8"
        data = msg.get_payload(decode=True)
        if data is None:
            return _as_text(msg.get_payload())
        return data.decode(charset, errors="replace")
    except Exception:
        return _as_text(msg.get_payload())


def _normalize_subject(subject: str) -> str:
    text = SUBJECT_PREFIX_RE.sub("", subject or "").strip()
    text = PATCH_SUBJECT_RE.sub("", text).strip()
    text = re.sub(r"\s+", " ", text)
    return text.lower()


def _patch_meta_from_subject(subject: str) -> Tuple[int, Optional[int], Optional[int]]:
    match = PATCH_SUBJECT_RE.search(subject or "")
    if not match:
        return 1, None, None
    version = int(match.group("v") or 1)
    idx = int(match.group("i")) if match.group("i") else None
    total = int(match.group("n")) if match.group("n") else None
    return version, idx, total


def _count_diff_lines(text: str) -> Tuple[int, int]:
    add = 0
    rem = 0
    for line in (text or "").splitlines():
        if line.startswith("+++ ") or line.startswith("--- "):
            continue
        if line.startswith("+"):
            add += 1
        elif line.startswith("-"):
            rem += 1
    return add, rem


def _date_candidates(series_dict: Dict[str, Any]) -> List[datetime]:
    dates: List[datetime] = []
    for m in series_dict.get("messages", []) or []:
        dt = m.get("date_dt")
        if isinstance(dt, datetime):
            dates.append(dt)
            continue
        parsed = _to_dt(m.get("date"))
        if parsed:
            dates.append(parsed)
    return sorted(dates)


def classify_status(series_dict: Dict[str, Any]) -> str:
    """Return MERGED | REVIEWED_NOT_MERGED | PENDING."""
    messages = series_dict.get("messages", []) or []
    merged = False
    reviewed = False

    for msg in messages:
        body = _as_text(msg.get("body"))
        subject = _as_text(msg.get("subject"))
        blob = f"{subject}\n{body}"
        if not msg.get("is_patch") and MERGE_HINT_RE.search(blob):
            merged = True
        if REVIEWED_BY_RE.search(blob):
            reviewed = True

    if merged:
        return "MERGED"
    if reviewed:
        return "REVIEWED_NOT_MERGED"
    return "PENDING"


def extract_version_info(series_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Extract reroll/version timing metrics."""
    messages = series_dict.get("messages", []) or []
    versions: List[int] = []
    version_dates: Dict[int, datetime] = {}
    final_patch_count = 0
    v1_dt: Optional[datetime] = None
    max_ver_dt: Optional[datetime] = None

    apply_date: Optional[datetime] = None
    for msg in messages:
        subj = _as_text(msg.get("subject"))
        body = _as_text(msg.get("body"))
        dt = msg.get("date_dt") if isinstance(msg.get("date_dt"), datetime) else _to_dt(msg.get("date"))
        version, _idx, total = _patch_meta_from_subject(subj)
        if msg.get("is_patch") or PATCH_SUBJECT_RE.search(subj):
            versions.append(version)
            if total:
                final_patch_count = max(final_patch_count, total)
            if dt and version not in version_dates:
                version_dates[version] = dt
        if dt and not msg.get("is_patch") and MERGE_HINT_RE.search(f"{subj}\n{body}"):
            apply_date = dt if apply_date is None else min(apply_date, dt)

    if not versions:
        versions = [1]
    v_min = min(versions)
    v_max = max(versions)
    versions_list = [f"v{v}" for v in sorted(set(versions))]
    version_count = len(versions_list)

    if version_dates:
        v1_dt = version_dates.get(1) or min(version_dates.values())
        max_ver_dt = version_dates.get(v_max) or max(version_dates.values())
    else:
        dates = _date_candidates(series_dict)
        if dates:
            v1_dt = dates[0]
            max_ver_dt = dates[-1]

    days_to_merge = None
    if apply_date and v1_dt:
        days_to_merge = max(0, (apply_date - v1_dt).days)

    reviewer_info = extract_reviewer_info(series_dict)
    first_review_date = _to_dt(reviewer_info.get("first_review_date"))
    days_to_apply = days_to_merge
    apply_basis = None
    maintainer_delay_days = None
    if apply_date:
        if first_review_date and first_review_date <= apply_date:
            apply_basis = "review"
            maintainer_delay_days = max(0, (apply_date - first_review_date).days)
        else:
            apply_basis = "v1"

    return {
        "v_min": v_min,
        "v_max": v_max,
        "version_count": version_count,
        "versions_list": versions_list,
        "v1_posted": _iso(v1_dt),
        "vN_posted": _iso(max_ver_dt),
        "days_to_merge": days_to_merge,
        "apply_date": _iso(apply_date),
        "days_to_apply": days_to_apply,
        "apply_basis": apply_basis,
        "maintainer_delay_days": maintainer_delay_days,
        "final_patch_count": final_patch_count or int(series_dict.get("final_patch_count") or 0) or 1,
    }


def extract_reviewer_info(series_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Extract reviewer names/count and first review timing."""
    reviewers: List[str] = []
    first_review: Optional[datetime] = None
    messages = series_dict.get("messages", []) or []

    first_patch_dt = None
    for msg in messages:
        if msg.get("is_patch"):
            first_patch_dt = msg.get("date_dt") if isinstance(msg.get("date_dt"), datetime) else _to_dt(msg.get("date"))
            if first_patch_dt:
                break

    for msg in messages:
        body = _as_text(msg.get("body"))
        for rv in REVIEWED_BY_RE.findall(body):
            clean = re.sub(r"\s+", " ", rv).strip()
            if clean and clean not in reviewers:
                reviewers.append(clean)
            dt = msg.get("date_dt") if isinstance(msg.get("date_dt"), datetime) else _to_dt(msg.get("date"))
            if dt and (first_review is None or dt < first_review):
                first_review = dt

    days_to_first_review = None
    if first_patch_dt and first_review:
        days_to_first_review = max(0, (first_review - first_patch_dt).days)

    return {
        "reviewed_by_count": len(reviewers),
        "reviewers_list": reviewers,
        "first_review_date": _iso(first_review),
        "days_to_first_review": days_to_first_review,
    }


def extract_line_stats(series_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Count added/removed lines from unified diff bodies."""
    per_patch_stats: List[Dict[str, Any]] = []
    total_add = 0
    total_rem = 0

    for idx, msg in enumerate(series_dict.get("messages", []) or [], start=1):
        if not (msg.get("is_patch") or "diff --git" in _as_text(msg.get("body"))):
            continue
        body = _as_text(msg.get("body"))
        add, rem = _count_diff_lines(body)
        total_add += add
        total_rem += rem
        per_patch_stats.append(
            {
                "patch_index": idx,
                "subject": _as_text(msg.get("subject")),
                "added": add,
                "removed": rem,
            }
        )

    return {
        "added_lines": total_add,
        "removed_lines": total_rem,
        "net_lines": total_add - total_rem,
        "per_patch_stats": per_patch_stats,
    }


def _build_series(raw_messages: List[Dict[str, Any]], author_email: str) -> List[Dict[str, Any]]:
    by_msgid: Dict[str, Dict[str, Any]] = {}
    children: Dict[str, List[str]] = defaultdict(list)
    root_cache: Dict[str, str] = {}

    for m in raw_messages:
        msgid = m.get("msgid")
        if msgid:
            by_msgid[msgid] = m
    for m in raw_messages:
        parent = m.get("in_reply_to")
        msgid = m.get("msgid")
        if parent and msgid:
            children[parent].append(msgid)

    def find_root(msgid: str) -> str:
        if msgid in root_cache:
            return root_cache[msgid]
        cur = by_msgid.get(msgid)
        seen = set()
        while cur and cur.get("in_reply_to") and cur.get("in_reply_to") in by_msgid and cur.get("in_reply_to") not in seen:
            seen.add(cur.get("msgid"))
            cur = by_msgid.get(cur.get("in_reply_to"))
        root = (cur or {}).get("msgid") or msgid
        root_cache[msgid] = root
        return root

    root_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    author_email_l = (author_email or "").lower().strip()
    candidate_roots: set[str] = set()

    for m in raw_messages:
        msgid = m.get("msgid")
        if not msgid:
            continue
        root = find_root(msgid)
        root_groups[root].append(m)

        frm = _as_text(m.get("from")).lower()
        subj = _as_text(m.get("subject"))
        if author_email_l and author_email_l in frm and PATCH_SUBJECT_RE.search(subj):
            candidate_roots.add(root)

    if not candidate_roots:
        candidate_roots = set(root_groups.keys())

    series_list: List[Dict[str, Any]] = []
    for root in candidate_roots:
        messages = sorted(
            root_groups.get(root, []),
            key=lambda x: x.get("date_dt") or _to_dt(x.get("date")) or datetime.min,
        )
        if not messages:
            continue

        patch_msgs = [m for m in messages if m.get("is_patch") or PATCH_SUBJECT_RE.search(_as_text(m.get("subject")))]
        title_src = patch_msgs[0] if patch_msgs else messages[0]
        title = _as_text(title_src.get("subject")) or "Untitled patch series"
        normalized_title = _normalize_subject(title)

        lore_url = ""
        for m in messages:
            arch = _as_text(m.get("archived_at"))
            if arch:
                lore_url = arch.strip("<>")
                break
        if not lore_url and title_src.get("msgid"):
            lore_url = f"https://lore.kernel.org/all/{title_src.get('msgid').strip('<>')}/"

        commit_shas: List[str] = []
        for m in messages:
            for sha in DIFF_SHA_RE.findall(_as_text(m.get("body"))):
                if sha not in commit_shas:
                    commit_shas.append(sha)

        series_list.append(
            {
                "id": f"series-{root.strip('<>')[:48]}",
                "root_msgid": root,
                "title": title,
                "normalized_title": normalized_title,
                "messages": messages,
                "commit_shas": commit_shas,
                "lore_url": lore_url,
            }
        )

    return series_list


def parse_mbox_gz(file_path: str, author_email: str) -> List[Dict[str, Any]]:
    """Parse .mbox or .mbox.gz into enriched series dictionaries."""
    if not os.path.exists(file_path):
        return []

    raw_messages: List[Dict[str, Any]] = []
    lower = file_path.lower()

    if lower.endswith(".gz"):
        with gzip.open(file_path, "rb") as handle:
            blob = handle.read()
        split_msgs = blob.split(b"\nFrom ")
        if split_msgs:
            split_msgs[0] = split_msgs[0].replace(b"\r\n", b"\n")
        for idx, chunk in enumerate(split_msgs):
            if idx > 0:
                chunk = b"From " + chunk
            if not chunk.strip():
                continue
            try:
                msg = BytesParser(policy=policy.default).parsebytes(chunk)
            except Exception:
                continue
            raw_messages.append(_message_to_dict(msg))
    else:
        mbox = mailbox.mbox(file_path)
        for msg in mbox:
            raw_messages.append(_message_to_dict(msg))

    return enrich_series(_build_series(raw_messages, author_email), author_email)

def _message_to_dict(msg: Message) -> Dict[str, Any]:
    subject = _as_text(msg.get("Subject"))
    body = _message_body(msg)
    msgid = _as_text(msg.get("Message-ID") or "").strip()
    in_reply_to = _as_text(msg.get("In-Reply-To") or "").strip()
    date_raw = _as_text(msg.get("Date") or "").strip()
    return {
        "subject": subject,
        "body": body,
        "msgid": msgid,
        "in_reply_to": in_reply_to,
        "from": _as_text(msg.get("From") or ""),
        "date": date_raw,
        "date_dt": _to_dt(date_raw),
        "is_patch": bool(PATCH_SUBJECT_RE.search(subject)),
        "archived_at": _as_text(msg.get("Archived-At") or ""),
    }


def enrich_series(raw_series_list: List[Dict[str, Any]], author_email: str) -> List[Dict[str, Any]]:
    """Run classification/extraction over live or offline series rows."""
    enriched: List[Dict[str, Any]] = []
    now = datetime.utcnow().isoformat() + "Z"

    for row in raw_series_list or []:
        series = dict(row)
        if "messages" not in series:
            # live single-row fallback: synthesize message list
            subject = _as_text(series.get("title") or series.get("subject"))
            date = _as_text(series.get("date"))
            body = _as_text(series.get("summary") or "")
            series["messages"] = [
                {
                    "subject": subject,
                    "body": body,
                    "date": date,
                    "date_dt": _to_dt(date),
                    "is_patch": bool(PATCH_SUBJECT_RE.search(subject)),
                }
            ]
            series["title"] = subject or "Untitled patch series"
            series["id"] = series.get("id") or f"series-{_normalize_subject(subject)[:40]}"

        status = classify_status(series)
        ver = extract_version_info(series)
        rev = extract_reviewer_info(series)
        lines = extract_line_stats(series)

        merged = {
            "id": str(series.get("id") or ""),
            "title": _as_text(series.get("title") or ""),
            "status": status,
            "versions": ver["versions_list"],
            "version_count": ver["version_count"],
            "final_patch_count": ver["final_patch_count"],
            "v1_posted": ver["v1_posted"],
            "vN_posted": ver["vN_posted"],
            "days_to_merge": ver["days_to_merge"],
            "apply_date": ver["apply_date"],
            "days_to_apply": ver["days_to_apply"],
            "apply_basis": ver["apply_basis"],
            "maintainer_delay_days": ver["maintainer_delay_days"],
            "reviewed_by_count": rev["reviewed_by_count"],
            "first_review_date": rev["first_review_date"],
            "days_to_first_review": rev["days_to_first_review"],
            "reviewers": rev["reviewers_list"],
            "added_lines": lines["added_lines"],
            "removed_lines": lines["removed_lines"],
            "net_lines": lines["net_lines"],
            "commit_shas": list(series.get("commit_shas") or []),
            "lore_url": _as_text(series.get("lore_url") or series.get("url") or ""),
            "updated_at": now,
            "author_email": author_email,
        }
        enriched.append(merged)

    # Reroll clustering fallback for live feeds lacking thread info:
    # same normalized title within 90 days is treated as one journey.
    clusters: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in enriched:
        key = _normalize_subject(item.get("title") or "")
        clusters[key].append(item)

    collapsed: List[Dict[str, Any]] = []
    for key, items in clusters.items():
        if not items:
            continue
        if len(items) == 1:
            collapsed.append(items[0])
            continue

        items_sorted = sorted(items, key=lambda x: x.get("v1_posted") or "9999-12-31")
        base = dict(items_sorted[-1])
        base["versions"] = [f"v{i+1}" for i in range(len(items_sorted))]
        base["version_count"] = len(base["versions"])
        base["v1_posted"] = items_sorted[0].get("v1_posted")
        base["vN_posted"] = items_sorted[-1].get("vN_posted")
        if base.get("apply_date") and base.get("v1_posted"):
            try:
                base["days_to_merge"] = max(
                    0,
                    (datetime.fromisoformat(base["apply_date"]) - datetime.fromisoformat(base["v1_posted"])).days,
                )
                base["days_to_apply"] = base["days_to_merge"]
            except Exception:
                pass
        collapsed.append(base)

    return collapsed


def summary_from_series(series: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build summary payload used by charts and stats endpoints."""
    total = len(series)
    merged_rows = [s for s in series if s.get("status") == "MERGED"]
    reviewed_rows = [s for s in series if s.get("status") == "REVIEWED_NOT_MERGED"]
    pending_rows = [s for s in series if s.get("status") == "PENDING"]

    def _avg(values: Iterable[Optional[int]]) -> float:
        vals = [v for v in values if isinstance(v, int)]
        return round(sum(vals) / len(vals), 2) if vals else 0.0

    month_bucket: Dict[str, Dict[str, int]] = defaultdict(lambda: {"count": 0, "merged": 0, "pending": 0})
    for s in series:
        month = str(s.get("v1_posted") or "")[:7]
        if not month or len(month) != 7:
            continue
        month_bucket[month]["count"] += 1
        if s.get("status") == "MERGED":
            month_bucket[month]["merged"] += 1
        if s.get("status") == "PENDING":
            month_bucket[month]["pending"] += 1

    monthly = [
        {"month": m, **vals}
        for m, vals in sorted(month_bucket.items())
    ]

    rev_bucket: Dict[int, int] = defaultdict(int)
    for s in series:
        vc = int(s.get("version_count") or 1)
        rev_bucket[4 if vc >= 4 else vc] += 1
    version_distribution = [
        {"revisions": key, "count": rev_bucket.get(key, 0)}
        for key in [1, 2, 3, 4]
    ]

    hist_keys = [("0-7d", 0, 7), ("8-14d", 8, 14), ("15-30d", 15, 30), ("31-60d", 31, 60), ("60d+", 61, 99999)]
    hist_counter = {k: 0 for k, _, _ in hist_keys}
    for s in merged_rows:
        days = s.get("days_to_merge")
        if not isinstance(days, int):
            continue
        for label, lo, hi in hist_keys:
            if lo <= days <= hi:
                hist_counter[label] += 1
                break

    histogram = [{"bucket": k, "count": hist_counter[k]} for k, _, _ in hist_keys]

    def _break(rows: List[Dict[str, Any]]) -> Dict[str, int]:
        return {
            "series": len(rows),
            "patches": sum(int(r.get("final_patch_count") or 0) for r in rows),
            "lines": sum(int(r.get("added_lines") or 0) + int(r.get("removed_lines") or 0) for r in rows),
        }

    return {
        "total_series": total,
        "merged": len(merged_rows),
        "reviewed_not_merged": len(reviewed_rows),
        "pending": len(pending_rows),
        "total_patches": sum(int(s.get("final_patch_count") or 0) for s in series),
        "total_lines_added": sum(int(s.get("added_lines") or 0) for s in series),
        "avg_days_to_merge": _avg((s.get("days_to_merge") for s in merged_rows)),
        "avg_days_to_apply": _avg((s.get("days_to_apply") for s in merged_rows)),
        "avg_maintainer_delay": _avg((s.get("maintainer_delay_days") for s in merged_rows)),
        "breakdown": {
            "MERGED": _break(merged_rows),
            "REVIEWED_NOT_MERGED": _break(reviewed_rows),
            "PENDING": _break(pending_rows),
        },
        "monthly_submissions": monthly,
        "version_distribution": version_distribution,
        "days_to_merge_histogram": histogram,
    }


def to_json(series: List[Dict[str, Any]]) -> str:
    return json.dumps(series, ensure_ascii=False)
