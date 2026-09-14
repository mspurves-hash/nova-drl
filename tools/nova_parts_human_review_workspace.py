#!/usr/bin/env python3
"""
Nova DRL Human Parts Review Workspace

Human-verification-only review surface for frozen v1.4.3 replacement mentions.

Policy
------
- Frozen source evidence is read-only.
- No semantic/fuzzy grouping.
- No automatic approval.
- No Qdrant writes.
- Decisions live in a separate append-only JSONL ledger.
- "Suppress from future review" never deletes evidence.
- A decision only applies while that candidate's evidence hash is unchanged.
"""

import argparse
import hashlib
import html
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

VERSION = "human-review-1"
VALID_DECISIONS = {"pending", "confirm", "keep_separate", "reject"}


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def normalized_ws(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def stable_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_id(prefix, *parts):
    text = "\n".join(str(x) for x in parts)
    return prefix + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def sha256_file(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_jsonl(path):
    rows = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except Exception as exc:
                raise RuntimeError(f"Invalid JSONL at {path}:{line_no}: {exc}")
    return rows


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def append_jsonl(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def pn_bucket_key(value):
    # Conservative only: case normalization + whitespace removal.
    # Punctuation stays intact, so slash/no-slash OCR variants remain separate
    # until a human gives them the same canonical label.
    return re.sub(r"\s+", "", normalized_ws(value).upper())


def description_bucket_key(value):
    # Exact wording bucket apart from whitespace and case.
    return normalized_ws(value).casefold()


def build_candidates(mentions, family):
    buckets = defaultdict(list)

    for m in mentions:
        pn = normalized_ws(m.get("part_number"))
        desc = normalized_ws(m.get("description") or m.get("raw_quote"))
        quote = normalized_ws(m.get("raw_quote") or desc)

        if pn:
            kind = "explicit_part_number"
            key = pn_bucket_key(pn)
        else:
            kind = "description_only"
            key = description_bucket_key(desc)

        if not key:
            continue

        buckets[(kind, key)].append({
            "mention_id": str(m.get("mention_id") or ""),
            "repair_event_id": str(m.get("repair_event_id") or ""),
            "part_number": pn or None,
            "description": desc,
            "raw_quote": quote,
            "quantity": m.get("quantity"),
            "source_record_ids": list(m.get("source_record_ids") or []),
        })

    candidates = []

    for (kind, key), rows in buckets.items():
        event_ids = sorted({r["repair_event_id"] for r in rows if r["repair_event_id"]})
        pns = sorted({r["part_number"] for r in rows if r["part_number"]})
        descriptions = sorted({r["description"] for r in rows if r["description"]})

        examples = []
        for r in rows:
            if r["raw_quote"] and r["raw_quote"] not in examples:
                examples.append(r["raw_quote"])
            if len(examples) >= 8:
                break

        pieces = 0
        qty_rows = 0
        qty_unstated = 0
        for r in rows:
            qty = r.get("quantity")
            if isinstance(qty, bool):
                qty = None
            try:
                qty = int(qty) if qty is not None else None
            except Exception:
                qty = None

            if qty is None:
                qty_unstated += 1
            else:
                qty_rows += 1
                pieces += qty

        candidate_id = stable_id("pc_", family, kind, key)

        evidence_basis = []
        for r in sorted(
            rows,
            key=lambda x: (x["repair_event_id"], x["mention_id"], x["raw_quote"]),
        ):
            evidence_basis.append({
                "mention_id": r["mention_id"],
                "repair_event_id": r["repair_event_id"],
                "part_number": r["part_number"],
                "description": r["description"],
                "raw_quote": r["raw_quote"],
                "quantity": r["quantity"],
                "source_record_ids": r["source_record_ids"],
            })

        evidence_hash = hashlib.sha256(
            stable_json(evidence_basis).encode("utf-8")
        ).hexdigest()

        review_id = "rv_" + candidate_id[3:] + "_" + evidence_hash[:12]

        candidates.append({
            "candidate_id": candidate_id,
            "review_id": review_id,
            "evidence_hash": evidence_hash,
            "family": family,
            "candidate_kind": kind,
            "bucket_key": key,
            "display_label": pns[0] if pns else (descriptions[0] if descriptions else key),
            "part_number_variants": pns,
            "description_variants": descriptions[:20],
            "repair_event_count": len(event_ids),
            "repair_event_ids": event_ids,
            "mention_count": len(rows),
            "recorded_pieces": pieces,
            "quantity_bearing_mentions": qty_rows,
            "quantity_unstated_mentions": qty_unstated,
            "evidence_examples": examples,
            "mention_ids": [r["mention_id"] for r in rows],
        })

    candidates.sort(
        key=lambda x: (
            -x["repair_event_count"],
            -x["mention_count"],
            x["display_label"].casefold(),
            x["candidate_id"],
        )
    )
    return candidates


def load_latest_decisions(ledger_path):
    latest = {}
    for row in read_jsonl(ledger_path):
        cid = row.get("candidate_id")
        if cid:
            latest[str(cid)] = row
    return latest


def split_current_and_stale(candidates, latest):
    by_id = {c["candidate_id"]: c for c in candidates}
    current, stale = {}, {}

    for cid, d in latest.items():
        c = by_id.get(cid)
        if c is None:
            stale[cid] = d
        elif d.get("evidence_hash") == c["evidence_hash"]:
            current[cid] = d
        else:
            stale[cid] = d

    return current, stale


def review_state(candidates, latest):
    rows = []
    for c in candidates:
        row = dict(c)
        d = latest.get(c["candidate_id"])

        if d is None:
            row["review_status"] = "unreviewed"
            row["current_decision"] = None
            row["stale_decision"] = None
        elif d.get("evidence_hash") == c["evidence_hash"]:
            row["review_status"] = "current"
            row["current_decision"] = d
            row["stale_decision"] = None
        else:
            row["review_status"] = "stale"
            row["current_decision"] = None
            row["stale_decision"] = d

        rows.append(row)
    return rows


def review_html(title):
    safe_title = html.escape(title)
    return """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
body { font-family: system-ui, sans-serif; margin:0; padding:18px; max-width:1500px; }
h1 { margin-top:0; }
.toolbar { position:sticky; top:0; background:white; padding:10px 0; border-bottom:1px solid #aaa; z-index:5; }
.card { border:1px solid #aaa; border-radius:8px; padding:14px; margin:14px 0; }
.meta { display:flex; flex-wrap:wrap; gap:14px; margin:8px 0; }
.small { font-size:.9rem; }
.warn { font-weight:700; }
.examples { white-space:pre-wrap; font-family:ui-monospace, monospace; }
input[type=text], textarea, select { width:100%; box-sizing:border-box; padding:7px; margin:4px 0 9px; }
textarea { min-height:70px; }
button { padding:8px 12px; }
.status { margin-left:8px; font-weight:700; }
fieldset { margin-top:10px; }
details { margin-top:8px; }
</style>
</head>
<body>
<h1>__TITLE__</h1>
<div class="toolbar">
<label><input id="showResolved" type="checkbox"> Show current resolved/suppressed</label>
&nbsp;
<label><input id="showOneOffs" type="checkbox"> Show 1-event candidates</label>
&nbsp;
<label><input id="showStale" type="checkbox" checked> Show stale decisions</label>
&nbsp;
<button onclick="loadData()">Refresh</button>
<span id="summary" class="status"></span>
</div>
<div id="cards"></div>

<script>
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  })[c]);
}

async function loadData() {
  const r = await fetch('/api/data');
  const data = await r.json();
  const showResolved = document.getElementById('showResolved').checked;
  const showOneOffs = document.getElementById('showOneOffs').checked;
  const showStale = document.getElementById('showStale').checked;

  let rows = data.candidates.filter(c => {
    if (!showOneOffs && c.repair_event_count < 2) return false;
    if (!showStale && c.review_status === 'stale') return false;
    if (!showResolved && c.review_status === 'current') {
      const d = c.current_decision || {};
      if (d.decision !== 'pending' || d.suppress_from_future_review) return false;
    }
    return true;
  });

  document.getElementById('summary').textContent =
    `Showing ${rows.length} / ${data.candidates.length} candidates`;

  const root = document.getElementById('cards');
  root.innerHTML = '';

  for (const c of rows) {
    const d = c.current_decision || {};
    const stale = c.stale_decision || null;
    const div = document.createElement('div');
    div.className = 'card';

    const examples = (c.evidence_examples || []).map(x => '• ' + esc(x)).join('\\n');
    const pns = (c.part_number_variants || []).map(esc).join(', ');
    const descs = (c.description_variants || []).map(esc).join(' | ');

    const staleHtml = stale
      ? `<div class="warn">STALE PRIOR DECISION: ${esc(stale.decision)} — evidence changed, so it is back for review.</div>`
      : '';

    div.innerHTML = `
      <h2>${esc(c.display_label)}</h2>
      ${staleHtml}
      <div class="meta">
        <span><b>Events:</b> ${c.repair_event_count}</span>
        <span><b>Mentions:</b> ${c.mention_count}</span>
        <span><b>Recorded pieces:</b> ${c.recorded_pieces}</span>
        <span><b>Qty unstated:</b> ${c.quantity_unstated_mentions}</span>
        <span><b>Kind:</b> ${esc(c.candidate_kind)}</span>
      </div>
      <div class="small">
        <b>Candidate ID:</b> ${esc(c.candidate_id)}<br>
        <b>Review ID:</b> ${esc(c.review_id)}<br>
        <b>Evidence hash:</b> ${esc(c.evidence_hash.slice(0,20))}...
      </div>

      <details open>
        <summary><b>Observed variants / evidence</b></summary>
        <div><b>PN variants:</b> ${pns || 'None'}</div>
        <div><b>Description variants:</b> ${descs || 'None'}</div>
        <pre class="examples">${examples}</pre>
      </details>

      <fieldset>
        <legend><b>Human decision</b></legend>

        <label>Decision</label>
        <select id="decision_${c.candidate_id}">
          <option value="pending">Pending / no decision</option>
          <option value="confirm">Confirm component identity</option>
          <option value="keep_separate">Keep separate / do not merge</option>
          <option value="reject">Reject as replacement-component evidence</option>
        </select>

        <label>Canonical component / label (human-entered)</label>
        <input id="canonical_${c.candidate_id}" type="text"
          value="${esc(d.canonical_label || '')}"
          placeholder="Example: IXFX24N100Q3">

        <label>
          <input id="suppress_${c.candidate_id}" type="checkbox"
            ${d.suppress_from_future_review ? 'checked' : ''}>
          Suppress from future review outputs
        </label>

        <label>Reviewer notes</label>
        <textarea id="notes_${c.candidate_id}">${esc(d.notes || '')}</textarea>

        <button onclick="saveDecision('${c.candidate_id}')">Save decision</button>
        <span id="save_${c.candidate_id}" class="status"></span>
      </fieldset>

      <details>
        <summary>Repair events (${c.repair_event_ids.length})</summary>
        <div class="small">${(c.repair_event_ids || []).map(esc).join(', ')}</div>
      </details>
    `;

    root.appendChild(div);
    document.getElementById(`decision_${c.candidate_id}`).value =
      d.decision || 'pending';
  }
}

async function saveDecision(candidateId) {
  const payload = {
    candidate_id: candidateId,
    decision: document.getElementById(`decision_${candidateId}`).value,
    canonical_label: document.getElementById(`canonical_${candidateId}`).value,
    suppress_from_future_review: document.getElementById(`suppress_${candidateId}`).checked,
    notes: document.getElementById(`notes_${candidateId}`).value
  };

  const s = document.getElementById(`save_${candidateId}`);
  s.textContent = 'Saving...';

  const r = await fetch('/api/decision', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify(payload)
  });

  const out = await r.json();
  if (!r.ok) {
    s.textContent = 'ERROR: ' + (out.error || r.status);
    return;
  }

  s.textContent = 'Saved';
  setTimeout(loadData, 350);
}

document.getElementById('showResolved').addEventListener('change', loadData);
document.getElementById('showOneOffs').addEventListener('change', loadData);
document.getElementById('showStale').addEventListener('change', loadData);
loadData();
</script>
</body>
</html>
""".replace("__TITLE__", safe_title)


class Workspace:
    def __init__(self, args):
        self.args = args
        self.source_root = Path(args.source_root)
        self.output_root = Path(args.output_root)
        self.mentions_path = self.source_root / "replacement_mentions_v1_4_3.jsonl"
        self.ledger_path = self.output_root / "human_review_decisions.jsonl"
        self.latest_path = self.output_root / "human_review_latest.json"
        self.candidates_path = self.output_root / "review_candidates.jsonl"
        self.manifest_path = self.output_root / "review_workspace_manifest.json"

        self.output_root.mkdir(parents=True, exist_ok=True)

        if not self.mentions_path.exists():
            raise RuntimeError(f"Missing source mentions: {self.mentions_path}")

        self.mentions = read_jsonl(self.mentions_path)
        self.candidates = build_candidates(self.mentions, args.family)
        self.by_id = {c["candidate_id"]: c for c in self.candidates}
        self.rebuild_outputs()

    def rebuild_outputs(self):
        latest = load_latest_decisions(self.ledger_path)
        current, stale = split_current_and_stale(self.candidates, latest)

        write_jsonl(self.candidates_path, self.candidates)

        write_json(self.latest_path, {
            "version": VERSION,
            "family": self.args.family,
            "valid_current_decisions": current,
            "stale_or_orphaned_latest_decisions": stale,
        })

        write_json(self.manifest_path, {
            "version": VERSION,
            "family": self.args.family,
            "source_mentions_path": str(self.mentions_path),
            "source_mentions_sha256": sha256_file(self.mentions_path),
            "source_mention_count": len(self.mentions),
            "candidate_count": len(self.candidates),
            "repair_event_count": len({
                str(m.get("repair_event_id"))
                for m in self.mentions
                if m.get("repair_event_id")
            }),
            "decision_ledger": str(self.ledger_path),
            "policy": {
                "semantic_grouping": False,
                "fuzzy_grouping": False,
                "automatic_approval": False,
                "source_evidence_modified": False,
                "suppression_deletes_evidence": False,
                "decision_valid_only_for_matching_evidence_hash": True,
                "qdrant": False,
            },
            "built_at_utc": now_utc(),
        })

    def data_payload(self):
        latest = load_latest_decisions(self.ledger_path)
        return {
            "version": VERSION,
            "family": self.args.family,
            "reviewer": self.args.reviewer,
            "candidates": review_state(self.candidates, latest),
        }

    def save_decision(self, payload):
        cid = str(payload.get("candidate_id") or "")
        if cid not in self.by_id:
            raise ValueError("Unknown candidate_id")

        decision = str(payload.get("decision") or "pending")
        if decision not in VALID_DECISIONS:
            raise ValueError(f"Invalid decision: {decision}")

        candidate = self.by_id[cid]
        canonical = normalized_ws(payload.get("canonical_label"))
        notes = normalized_ws(payload.get("notes"))
        suppress = bool(payload.get("suppress_from_future_review"))

        reviewed_at = now_utc()

        row = {
            "version": VERSION,
            "decision_id": stable_id(
                "hd_",
                cid,
                candidate["evidence_hash"],
                reviewed_at,
                self.args.reviewer,
                decision,
                canonical,
                suppress,
                notes,
            ),
            "candidate_id": cid,
            "review_id": candidate["review_id"],
            "evidence_hash": candidate["evidence_hash"],
            "family": self.args.family,
            "decision": decision,
            "canonical_label": canonical or None,
            "suppress_from_future_review": suppress,
            "notes": notes or None,
            "reviewer": self.args.reviewer,
            "reviewed_at_utc": reviewed_at,
            "raw_evidence_preserved": True,
            "qdrant_entry_created": False,
        }

        append_jsonl(self.ledger_path, row)
        self.rebuild_outputs()
        return row


def make_handler(workspace):
    class Handler(BaseHTTPRequestHandler):
        def send_json(self, obj, status=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path

            if path == "/":
                body = review_html(
                    f"Nova DRL Human Parts Review — {workspace.args.family}"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if path == "/api/data":
                self.send_json(workspace.data_payload())
                return

            if path == "/api/latest":
                latest = load_latest_decisions(workspace.ledger_path)
                current, stale = split_current_and_stale(
                    workspace.candidates, latest
                )
                self.send_json({"valid": current, "stale": stale})
                return

            self.send_json({"error": "not found"}, 404)

        def do_POST(self):
            path = urlparse(self.path).path
            if path != "/api/decision":
                self.send_json({"error": "not found"}, 404)
                return

            try:
                length = int(self.headers.get("Content-Length") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                row = workspace.save_decision(payload)
                self.send_json({"ok": True, "decision": row})
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)

        def log_message(self, fmt, *args):
            return

    return Handler


def main():
    ap = argparse.ArgumentParser(
        description="Nova DRL human-only parts verification workspace"
    )
    ap.add_argument("--source-root", required=True)
    ap.add_argument("--output-root", required=True)
    ap.add_argument("--family", required=True)
    ap.add_argument("--reviewer", default="Matt Purves")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    ws = Workspace(args)
    latest = load_latest_decisions(ws.ledger_path)
    current, stale = split_current_and_stale(ws.candidates, latest)

    event_count = len({
        str(m.get("repair_event_id"))
        for m in ws.mentions
        if m.get("repair_event_id")
    })

    print("# Nova DRL Human Parts Review Workspace")
    print(f"Family:                  {args.family}")
    print(f"Source mentions:         {len(ws.mentions)}")
    print(f"Repair events:           {event_count}")
    print(f"Review candidates:       {len(ws.candidates)}")
    print(f"Current decisions:       {len(current)}")
    print(f"Stale/orphan decisions:  {len(stale)}")
    print(f"Candidates:              {ws.candidates_path}")
    print(f"Decision ledger:         {ws.ledger_path}")
    print(f"Current state:           {ws.latest_path}")
    print(f"Manifest:                {ws.manifest_path}")
    print("Semantic/fuzzy grouping: OFF")
    print("Automatic approval:      OFF")
    print("Frozen evidence modified:NO")
    print("Qdrant:                  OFF")

    if args.build_only or not args.serve:
        return 0

    server = ThreadingHTTPServer(
        (args.host, args.port),
        make_handler(ws),
    )

    print()
    print(f"Review UI: http://{args.host}:{args.port}")
    print("Ctrl+C stops the server. Decisions are saved immediately.")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
