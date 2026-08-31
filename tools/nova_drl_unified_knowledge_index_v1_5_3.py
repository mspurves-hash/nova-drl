#!/usr/bin/env python3
"""
Nova DRL Full-Corpus Unified Knowledge Search + Print v1.5.3

Engineer-facing presentation layer over the full v1.5.2 repair corpus and full DRL file index.

Key additions:
- Windows Engineer Client support for passwordless indexed queries and report transfer,
- machine-safe Base64 query modes for Windows client calls,
- server-side PDF-file generation without HTTP dependency for Windows transfer,
- clean grouped search presentation,
- strict identifier result linking (no unrelated filler for RMA/PN/order searches),
- customer PO shown separately from distributor procurement orders,
- all .picasa.ini and .picasaoriginals content hidden from normal search/report output,
- blue action hints for :pdf and :print in interactive terminals,
- clickable blue OSC-8 PDF links when the terminal supports them,
- an always-visible plain browser URL for older Windows consoles,
- LAN/IP URL preferred over hostname for workstation access,
- on-demand printable PDF reports with a local HTTP link,
- direct :print support through the server print system when available,
- no AI/LLM call for ordinary search, PDF generation, or printing.

The v1.5.3 engine rebuilds the local knowledge DB from the completed v1.5.2 full corpus. No AI/vision/NAS scan is used.
"""
from __future__ import annotations

import argparse
import base64
import functools
import http.server
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import textwrap
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

VERSION = "1.5.3"
DEFAULT_DB = "/opt/nova-drl/index/drl_knowledge_index.sqlite"
DEFAULT_REPORTS_DIR = "/opt/nova-drl/reports"
DEFAULT_REPORT_PORT = 8765
BASE_SCRIPT = Path(__file__).with_name("nova_drl_unified_knowledge_engine_v1_5_3.py")


def load_base():
    if not BASE_SCRIPT.exists():
        raise FileNotFoundError(f"Required v1.5.3 full-corpus engine not found: {BASE_SCRIPT}")
    spec = importlib.util.spec_from_file_location("nova_drl_v153_engine", BASE_SCRIPT)
    if not spec or not spec.loader:
        raise RuntimeError(f"Unable to load {BASE_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


base = load_base()


DISPLAY_GROUPS: List[Tuple[str, set[str]]] = [
    ("TRACKING / PROJECT", {"rma", "customer_po"}),
    ("PROCUREMENT / REORDER", {"order"}),
    ("EQUIPMENT / PRODUCT", {"product"}),
    ("INDEXED PARTS", {"product_part"}),
    ("REPAIR HISTORY", {"event"}),
    ("PART OCCURRENCES", {"replacement"}),
    ("SOURCE FILES", {"file"}),
]


ANSI_BLUE = "\033[94m"
ANSI_RESET = "\033[0m"
OSC8_OPEN = "\033]8;;"
OSC8_CLOSE = "\033]8;;\033\\"
ST = "\033\\"


def terminal_color_enabled(stream=None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM", "").casefold() == "dumb":
        return False
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def blue(text: str, *, force: Optional[bool] = None) -> str:
    enabled = terminal_color_enabled() if force is None else force
    return f"{ANSI_BLUE}{text}{ANSI_RESET}" if enabled else text


def terminal_link(url: str, label: Optional[str] = None, *, force: Optional[bool] = None) -> str:
    """Return a blue OSC-8 hyperlink when interactive; plain URL otherwise."""
    enabled = terminal_color_enabled() if force is None else force
    label = label or url
    if not enabled:
        return url if label == url else f"{label}: {url}"
    return f"{OSC8_OPEN}{url}{ST}{ANSI_BLUE}{label}{ANSI_RESET}{OSC8_CLOSE}"


def print_action_hint() -> None:
    print()
    print(f"Actions: {blue(':pdf')} create/open printable PDF   {blue(':print')} send current report to printer")


def compact(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def alnum(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", compact(value)).upper()


def is_picasa_irrelevant(result: Dict[str, Any]) -> bool:
    if result.get("item_type") != "file":
        return False
    p = result.get("payload") or {}
    path = compact(result.get("source_path") or p.get("relative_path") or result.get("title")).replace("\\", "/").casefold()
    name = compact(p.get("filename") or Path(path).name).casefold()
    return name == ".picasa.ini" or "/.picasaoriginals/" in path or path.endswith("/.picasa.ini")


def is_customer_po_result(result: Dict[str, Any]) -> bool:
    if result.get("item_type") != "order":
        return False
    p = result.get("payload") or {}
    evidence = compact(p.get("evidence_quote"))
    # Only explicit customer-PO wording is promoted. Generic PO/order language
    # remains procurement so we do not guess tracking semantics.
    return bool(re.search(r"\b(?:cust(?:omer)?\s*)?P\.?\s*O\.?\s*[:#-]?", evidence, flags=re.I) and re.search(r"\bcust(?:omer)?\b", evidence, flags=re.I))


def display_type(result: Dict[str, Any]) -> str:
    return "customer_po" if is_customer_po_result(result) else str(result.get("item_type") or "")


def identifier_style_query(query: str) -> bool:
    q = compact(query)
    a = alnum(q)
    if not a:
        return False
    if a.startswith(("DGK", "MSR", "NWK", "DSK", "RMA")) and len(a) >= 5:
        return True
    if a.isdigit() and len(a) >= 4:
        return True
    # Typical PN/model/serial tokens: letters + digits, not a sentence.
    if len(a) >= 5 and any(c.isalpha() for c in a) and any(c.isdigit() for c in a) and len(q.split()) <= 2:
        return True
    return False


def directly_matches(result: Dict[str, Any], query: str) -> bool:
    qa = alnum(query)
    if not qa:
        return False
    text = " ".join([
        compact(result.get("primary_value")), compact(result.get("title")),
        compact(result.get("subtitle")), compact(result.get("equipment_family")),
        compact(result.get("log_number")), compact(result.get("source_path")),
        compact(result.get("search_text")),
    ])
    return qa in alnum(text)


def strict_identifier_filter(results: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    if not identifier_style_query(query):
        return results
    direct = [r for r in results if directly_matches(r, query)]
    if not direct:
        return results
    event_ids = {compact(r.get("repair_event_id")) for r in direct if compact(r.get("repair_event_id"))}
    logs = {compact(r.get("log_number")) for r in direct if compact(r.get("log_number"))}
    keep: List[Dict[str, Any]] = []
    seen = set()
    for r in results:
        key = (r.get("item_type"), r.get("item_key"))
        if key in seen:
            continue
        direct_hit = directly_matches(r, query)
        linked = bool(
            (compact(r.get("repair_event_id")) and compact(r.get("repair_event_id")) in event_ids)
            or (compact(r.get("log_number")) and compact(r.get("log_number")) in logs)
        )
        if direct_hit or linked:
            keep.append(r)
            seen.add(key)
    return keep


def row_to_result(row) -> Dict[str, Any]:
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except Exception:
        payload = {}
    return {k: row[k] for k in row.keys()} | {"payload": payload, "score": 0.0}


def linked_context_rows(conn, direct: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fetch records explicitly linked to literal identifier hits.

    FTS cannot return an event that does not itself contain an RMA/order value, so
    after a literal identifier hit we add only rows sharing that repair_event_id or
    DRL log. This is deterministic linkage, not fuzzy filler.
    """
    eids = sorted({compact(r.get("repair_event_id")) for r in direct if compact(r.get("repair_event_id"))})
    logs = sorted({compact(r.get("log_number")) for r in direct if compact(r.get("log_number"))})
    clauses = []
    params: List[Any] = []
    if eids:
        clauses.append("repair_event_id IN (%s)" % ",".join("?" * len(eids)))
        params.extend(eids)
    if logs:
        clauses.append("log_number IN (%s)" % ",".join("?" * len(logs)))
        params.extend(logs)
    if not clauses:
        return []
    rows = conn.execute("SELECT * FROM search_items WHERE " + " OR ".join(clauses), params).fetchall()
    return [row_to_result(r) for r in rows]


def search_clean(conn, query: str, *, candidate_limit: int = 800, result_limit: int = 160) -> List[Dict[str, Any]]:
    rows = base.search_db(conn, query, candidate_limit=candidate_limit, result_limit=max(result_limit, 160))
    rows = [dict(r) for r in rows if not is_picasa_irrelevant(r)]
    if identifier_style_query(query):
        direct = [r for r in rows if directly_matches(r, query)]
        if direct:
            linked = linked_context_rows(conn, direct)
            merged = direct + linked
            dedup: List[Dict[str, Any]] = []
            seen = set()
            for r in merged:
                if is_picasa_irrelevant(r):
                    continue
                key = (r.get("item_type"), r.get("item_key"))
                if key in seen:
                    continue
                seen.add(key)
                dedup.append(r)
            rows = dedup
        else:
            rows = strict_identifier_filter(rows, query)
    return rows[:result_limit]


def grouped_results(results: List[Dict[str, Any]], top: int) -> List[Tuple[str, List[Dict[str, Any]]]]:
    out: List[Tuple[str, List[Dict[str, Any]]]] = []
    for heading, types in DISPLAY_GROUPS:
        subset = [r for r in results if display_type(r) in types]
        if heading == "SOURCE FILES":
            # File metadata coverage is full DRL, but normal engineer output is a
            # concise source list rather than an Everything-style file dump.
            subset = subset[: min(top, 6)]
        else:
            subset = subset[:top]
        if subset:
            out.append((heading, subset))
    return out


def qty(value: Any) -> str:
    return str(value) if isinstance(value, int) else "unstated"


def item_lines(result: Dict[str, Any]) -> List[str]:
    t = display_type(result)
    p = result.get("payload") or {}
    family = compact(result.get("equipment_family")) or "-"
    log = compact(result.get("log_number")) or "-"
    lines: List[str] = []
    if t == "rma":
        lines.append(f"RMA {result.get('primary_value') or '-'}  |  DRL log {log}  |  {family}")
        if result.get("source_path"):
            lines.append(f"Source: {result['source_path']}")
    elif t == "customer_po":
        lines.append(f"Customer PO {result.get('primary_value') or '-'}  |  DRL log {log}  |  {family}")
        if p.get("evidence_quote"):
            lines.append(f"Evidence: {compact(p['evidence_quote'])}")
    elif t == "order":
        supplier = compact(result.get("subtitle")) or compact(p.get("supplier")) or "supplier not stated"
        lines.append(f"{supplier} order {result.get('primary_value') or '-'}  |  DRL log {log}  |  {family}")
        if p.get("description") or p.get("manufacturer_pn"):
            lines.append(f"Description: {p.get('description') or '-'}  |  Mfr PN: {p.get('manufacturer_pn') or '-'}  |  Qty: {qty(p.get('quantity'))}")
        if p.get("evidence_quote"):
            lines.append(f"Evidence: {compact(p['evidence_quote'])}")
    elif t == "product":
        lines.append(f"{result.get('title') or family}")
        lines.append(f"Indexed repair events: {p.get('repair_event_count',0)}  |  Events with parts: {p.get('events_with_parts',0)}  |  Indexed components: {p.get('indexed_component_count',0)}")
        top_parts = p.get("top_parts") or []
        if top_parts:
            lines.append("Top indexed parts: " + "; ".join(f"{x.get('display')} ({x.get('repairs')} repairs)" for x in top_parts[:8]))
    elif t == "product_part":
        lines.append(f"{result.get('title') or '-'}  |  {family}")
        lines.append(f"Repair events: {p.get('repairs',0)}  |  Recorded pieces: {p.get('pieces',0)}  |  Qty unstated: {p.get('unstated',0)}")
        variants = p.get("variants") or []
        if len(variants) > 1:
            lines.append("Observed variants: " + ", ".join(str(x) for x in variants[:8]))
    elif t == "event":
        lines.append(f"DRL log {log}  |  {family}")
        if p.get("reported_problem"):
            lines.append("Reported problem: " + compact(p["reported_problem"])[:500])
        if p.get("repair_history"):
            lines.append("Repair history: " + compact(p["repair_history"])[:700])
        if p.get("test_outcome"):
            lines.append("Test/outcome: " + compact(p["test_outcome"])[:500])
        paths = p.get("source_paths") or []
        if paths:
            lines.append("Source: " + str(paths[0]))
    elif t == "replacement":
        title = compact(result.get("title")) or "Replacement item"
        lines.append(f"{title}  |  DRL log {log}  |  {family}  |  Qty: {qty(p.get('quantity'))}")
        if p.get("text") and compact(p.get("text")) != title:
            lines.append("Recorded as: " + compact(p.get("text"))[:400])
    elif t == "file":
        line = compact(result.get("source_path") or result.get("title"))
        if log != "-":
            line += f"  |  DRL log {log}"
        lines.append(line)
    else:
        lines.append(compact(result.get("title")))
    return [x for x in lines if x]


def search_report(conn, query: str, args) -> Tuple[List[Dict[str, Any]], List[Tuple[str, List[Dict[str, Any]]]], float]:
    t0 = time.perf_counter()
    results = search_clean(conn, query, candidate_limit=args.candidate_limit, result_limit=max(args.top * 12, 160))
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return results, grouped_results(results, args.top), elapsed_ms


def render_console(conn, query: str, args, *, show_pdf_hint: bool = True) -> int:
    results, groups, elapsed_ms = search_report(conn, query, args)
    print()
    print("=" * 86)
    print(f"NOVA DRL SEARCH  |  {query}  |  {elapsed_ms:.1f} ms")
    print("=" * 86)
    print("Coverage: file/path index = full DRL share | repair/parts/tracking knowledge = FULL v1.5.2 corpus")
    if not results:
        print("No indexed matches.")
        return 0
    for heading, subset in groups:
        print(f"\n{heading}")
        print("-" * len(heading))
        for i, result in enumerate(subset, 1):
            lines = item_lines(result)
            if not lines:
                continue
            print(f"{i}. {lines[0]}")
            for line in lines[1:]:
                print(f"   {line}")
    if show_pdf_hint:
        print_action_hint()
    return 0


# ----------------------------- PDF generation -----------------------------

def pdf_escape(text: str) -> str:
    s = text.encode("latin-1", "replace").decode("latin-1")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def wrap_report_text(text: str, width: int = 96) -> List[str]:
    text = compact(text)
    if not text:
        return [""]
    return textwrap.wrap(text, width=width, break_long_words=True, break_on_hyphens=False) or [""]


def pdf_report_blocks(query: str, groups: List[Tuple[str, List[Dict[str, Any]]]], elapsed_ms: float) -> List[Tuple[str, str]]:
    blocks: List[Tuple[str, str]] = []
    blocks.append(("meta", f"Query: {query}"))
    blocks.append(("meta", f"Search time: {elapsed_ms:.1f} ms"))
    blocks.append(("meta", "Coverage: full DRL file/path index; repair/parts/RMA/PO/order knowledge from the full v1.5.2 corpus"))
    for heading, subset in groups:
        blocks.append(("heading", heading))
        for n, r in enumerate(subset, 1):
            lines = item_lines(r)
            for j, line in enumerate(lines):
                prefix = f"{n}. " if j == 0 else "   "
                blocks.append(("body", prefix + line))
        blocks.append(("spacer", ""))
    return blocks


def write_pdf(path: Path, query: str, groups: List[Tuple[str, List[Dict[str, Any]]]], elapsed_ms: float) -> None:
    """Dependency-free, print-friendly Letter PDF using built-in Helvetica fonts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    blocks = pdf_report_blocks(query, groups, elapsed_ms)
    page_width, page_height = 612.0, 792.0
    left, right, top, bottom = 42.0, 42.0, 42.0, 42.0
    max_y = page_height - top
    min_y = bottom + 20
    pages: List[List[Tuple[str, str]]] = []
    current: List[Tuple[str, str]] = []
    y = max_y - 34

    def needed(kind: str, text: str) -> Tuple[List[str], float]:
        if kind == "heading":
            lines = wrap_report_text(text, 82)
            return lines, 16.0 * len(lines) + 5
        if kind == "spacer":
            return [""], 7.0
        lines = wrap_report_text(text, 98)
        return lines, 11.5 * len(lines)

    for kind, text in blocks:
        lines, h = needed(kind, text)
        if y - h < min_y and current:
            pages.append(current)
            current = []
            y = max_y - 34
        for line in lines:
            current.append((kind, line))
        y -= h
    if current or not pages:
        pages.append(current)

    objects: List[bytes] = []

    def add_obj(data: bytes) -> int:
        objects.append(data)
        return len(objects)

    # Reserve catalog/pages objects; fill later.
    catalog_id = add_obj(b"")
    pages_id = add_obj(b"")
    font_regular = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    font_bold = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    page_ids: List[int] = []

    for page_no, page_blocks in enumerate(pages, 1):
        cmds: List[str] = []
        # Header
        cmds.append("BT /F2 14 Tf 42 758 Td (NOVA DRL Search Report) Tj ET")
        cmds.append(f"BT /F1 8 Tf 42 744 Td (Generated by Unified Knowledge Search v{VERSION}) Tj ET")
        ycur = 716.0
        for kind, text in page_blocks:
            if kind == "heading":
                cmds.append(f"BT /F2 11 Tf 42 {ycur:.1f} Td ({pdf_escape(text)}) Tj ET")
                ycur -= 16.0
            elif kind == "spacer":
                ycur -= 7.0
            elif kind == "meta":
                cmds.append(f"BT /F1 8.5 Tf 42 {ycur:.1f} Td ({pdf_escape(text)}) Tj ET")
                ycur -= 11.5
            else:
                cmds.append(f"BT /F1 9 Tf 42 {ycur:.1f} Td ({pdf_escape(text)}) Tj ET")
                ycur -= 11.5
        footer = f"Page {page_no} of {len(pages)}"
        cmds.append(f"BT /F1 8 Tf 500 24 Td ({pdf_escape(footer)}) Tj ET")
        stream = "\n".join(cmds).encode("latin-1", "replace")
        content_id = add_obj(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_obj = (
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {page_width:.0f} {page_height:.0f}] "
            f"/Resources << /Font << /F1 {font_regular} 0 R /F2 {font_bold} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        ).encode("ascii")
        page_ids.append(add_obj(page_obj))

    objects[pages_id - 1] = (f"<< /Type /Pages /Kids [{' '.join(f'{x} 0 R' for x in page_ids)}] /Count {len(page_ids)} >>").encode("ascii")
    objects[catalog_id - 1] = (f"<< /Type /Catalog /Pages {pages_id} 0 R >>").encode("ascii")

    info_id = add_obj((f"<< /Title ({pdf_escape('NOVA DRL Search - ' + query)}) /Producer (Nova DRL v{VERSION}) >>").encode("latin-1", "replace"))
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out.extend(f"{i} 0 obj\n".encode("ascii"))
        out.extend(obj)
        out.extend(b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(objects)+1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend((f"trailer\n<< /Size {len(objects)+1} /Root {catalog_id} 0 R /Info {info_id} 0 R >>\nstartxref\n{xref}\n%%EOF\n").encode("ascii"))
    path.write_bytes(out)


def slugify(query: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", compact(query)).strip("._-")
    return (s or "search")[:70]


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def report_server_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=0.35) as response:
            return b"Nova DRL printable PDF report server" in response.read(128)
    except Exception:
        return False


def start_report_server(args) -> bool:
    if report_server_ready(args.report_port):
        return True
    if port_open(args.report_port):
        # A different service owns this port; do not claim its URL as a Nova report link.
        return False
    Path(args.reports_dir).mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(Path(__file__).resolve()), "--serve-reports", "--reports-dir", args.reports_dir, "--report-port", str(args.report_port)]
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    except Exception:
        return False
    for _ in range(20):
        time.sleep(0.1)
        if report_server_ready(args.report_port):
            return True
    return False


def preferred_server_ip() -> Optional[str]:
    """Best LAN-facing IPv4 for engineer workstations; avoid loopback/container IPs when possible."""
    try:
        raw = subprocess.check_output(["ip", "-4", "route", "get", "1.1.1.1"], text=True, stderr=subprocess.DEVNULL, timeout=1)
        m = re.search(r"\bsrc\s+(\d+\.\d+\.\d+\.\d+)", raw)
        if m and not m.group(1).startswith("127."):
            return m.group(1)
    except Exception:
        pass
    try:
        raw = subprocess.check_output(["hostname", "-I"], text=True, stderr=subprocess.DEVNULL, timeout=1)
        ips = [x for x in raw.split() if ":" not in x and not x.startswith("127.")]
        if not ips:
            return None
        def rank(ip: str) -> tuple[int, str]:
            if ip.startswith("192.168."):
                return (0, ip)
            if ip.startswith("10."):
                return (1, ip)
            m = re.match(r"172\.(\d+)\.", ip)
            if m and 16 <= int(m.group(1)) <= 31:
                return (2, ip)
            return (3, ip)
        return sorted(ips, key=rank)[0]
    except Exception:
        return None


def report_urls(path: Path, port: int) -> List[str]:
    name = urllib.parse.quote(path.name)
    urls: List[str] = []
    ip = preferred_server_ip()
    if ip:
        urls.append(f"http://{ip}:{port}/{name}")
    host = socket.gethostname()
    if host:
        urls.append(f"http://{host}:{port}/{name}")
    return list(dict.fromkeys(urls))


def print_pdf_access(urls: List[str]) -> None:
    """Show both modern clickable link and old-console copy/paste URL."""
    if not urls:
        return
    preferred = urls[0]
    print()
    print("PDF READY FOR PRINTING")
    print("----------------------")
    print("Clickable link (supported terminals):")
    print("  " + terminal_link(preferred))
    print("COPY/PASTE INTO CHROME OR EDGE:")
    # Intentionally plain: no ANSI/OSC codes so classic Windows console users can copy it safely.
    print("  " + preferred)
    if len(urls) > 1:
        print("Alternate address:")
        print("  " + urls[1])


def create_pdf_for_query(conn, query: str, args) -> Path:
    _, groups, elapsed_ms = search_report(conn, query, args)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = Path(args.reports_dir) / f"NOVA_DRL_{slugify(query)}_{stamp}.pdf"
    write_pdf(path, query, groups, elapsed_ms)
    served = start_report_server(args)
    print(f"Printable PDF created: {path}")
    if served:
        urls = report_urls(path, args.report_port)
        if urls:
            print_pdf_access(urls)
        else:
            print(f"Report server is running on port {args.report_port}; use the server LAN IP with {path.name}.")
    else:
        print("PDF created, but the local report server could not be started. Use the file path above.")
    return path

def print_pdf_file(path: Path, args) -> bool:
    """Send PDF to configured/default CUPS printer. Return True on successful queueing."""
    printer = compact(getattr(args, "printer", None))
    lp = shutil.which("lp")
    lpr = shutil.which("lpr")
    if lp:
        cmd = [lp]
        if printer:
            cmd += ["-d", printer]
        cmd.append(str(path))
    elif lpr:
        cmd = [lpr]
        if printer:
            cmd += ["-P", printer]
        cmd.append(str(path))
    else:
        print("Direct print unavailable: neither 'lp' nor 'lpr' is installed/configured on this server.")
        print("Use the PDF browser URL above to print from the engineer workstation.")
        return False
    try:
        cp = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    except Exception as exc:
        print(f"Print command failed: {exc}")
        print("Use the PDF browser URL above to print from the engineer workstation.")
        return False
    msg = compact(cp.stdout or cp.stderr)
    if cp.returncode != 0:
        print(f"Print command failed ({cp.returncode}): {msg or 'unknown printer/CUPS error'}")
        print("Use the PDF browser URL above to print from the engineer workstation.")
        return False
    target = printer or "default printer"
    print(f"Queued to {target}: {path.name}")
    if msg:
        print(msg)
    return True


def create_and_print_for_query(conn, query: str, args) -> Path:
    path = create_pdf_for_query(conn, query, args)
    print_pdf_file(path, args)
    return path


class PdfOnlyHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in {"", "/"}:
            body = b"Nova DRL printable PDF report server\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if not parsed.path.casefold().endswith(".pdf"):
            self.send_error(404)
            return
        return super().do_GET()


def serve_reports(args) -> int:
    reports = Path(args.reports_dir)
    reports.mkdir(parents=True, exist_ok=True)
    handler = functools.partial(PdfOnlyHandler, directory=str(reports))
    server = http.server.ThreadingHTTPServer(("0.0.0.0", args.report_port), handler)
    print(f"Nova DRL PDF report server: {reports} -> port {args.report_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


# ----------------------------- commands -----------------------------

def command_status(args) -> int:
    # v1.5.3 presentation/print layer uses the full-corpus v1.5.3 storage engine.
    base_args = argparse.Namespace(
        file_index=args.file_index, full_root=args.full_root,
        db=args.db, top=args.top, candidate_limit=args.candidate_limit, json=False,
        self_check_warn_ms=args.self_check_warn_ms,
    )
    rc = base.command_status(base_args)
    print(f"Presentation layer:    v{VERSION} | clean grouped output + Windows Engineer Client support")
    print("Picasa display policy: hidden | .picasa.ini and .picasaoriginals")
    print(f"Printable reports:     {args.reports_dir} | HTTP port {args.report_port}")
    print(f"Terminal actions:      blue :pdf / :print | clickable + plain browser PDF URL")
    print(f"Direct printer target: {args.printer or 'system default'} | lp/lpr used when available")
    return rc


def command_plan(args) -> int:
    base_args = argparse.Namespace(
        file_index=args.file_index, full_root=args.full_root,
        db=args.db, top=args.top, candidate_limit=args.candidate_limit, json=False,
        self_check_warn_ms=args.self_check_warn_ms,
    )
    rc = base.command_plan(base_args)
    print("v1.5.3: full-corpus knowledge + Windows Engineer Client + auto-open PDF")
    print("Search/database schema:          v1.5.3 full-corpus schema with first-class Customer PO")
    print("AI/LLM calls for search/PDF/print: 0")
    return rc


def command_build(args) -> int:
    # Rebuild with the v1.5.3 full-corpus engine.
    base.VERSION = VERSION
    base_args = argparse.Namespace(
        file_index=args.file_index, full_root=args.full_root,
        db=args.db, top=args.top, candidate_limit=args.candidate_limit, json=False,
        self_check_warn_ms=args.self_check_warn_ms,
    )
    return base.command_build(base_args)


def command_self_check(args) -> int:
    db = Path(args.db)
    if not db.exists():
        print(f"ERROR: unified knowledge DB not built: {db}", file=sys.stderr)
        return 2
    conn = base.connect_ro(db)
    try:
        queries = ["RCL1A", "GB8", "DGK", "MSR", "Line Card"]
        r = conn.execute("SELECT rma_number FROM rma_refs LIMIT 1").fetchone()
        if r:
            queries.append(str(r[0]))
        po = conn.execute("SELECT customer_po FROM customer_po_refs LIMIT 1").fetchone()
        if po:
            queries.append(str(po[0]))
        p = conn.execute("SELECT manufacturer_pn FROM product_parts WHERE manufacturer_pn IS NOT NULL LIMIT 1").fetchone()
        if p:
            queries.append(str(p[0]))
        print(f"# Nova DRL Unified Knowledge Search Self-Check v{VERSION}")
        ok = True
        for q in queries:
            t0 = time.perf_counter()
            rows = search_clean(conn, q, candidate_limit=args.candidate_limit, result_limit=40)
            ms = (time.perf_counter() - t0) * 1000.0
            print(f"{q!r:30s} -> {len(rows):2d} clean results in {ms:7.2f} ms")
            if ms > args.self_check_warn_ms:
                ok = False
        print("Result:", "PASS" if ok else f"PASS WITH SPEED WARNING > {args.self_check_warn_ms:.0f} ms")
        return 0
    finally:
        conn.close()


def command_search(args, query: str) -> int:
    db = Path(args.db)
    if not db.exists():
        print(f"ERROR: unified knowledge DB not built: {db}", file=sys.stderr)
        print("Run: python3 tools/nova_drl_unified_knowledge_index_v1_5_3.py --build")
        return 2
    conn = base.connect_ro(db)
    try:
        if args.json:
            results, groups, elapsed = search_report(conn, query, args)
            print(json.dumps({"version": VERSION, "query": query, "elapsed_ms": round(elapsed,3), "results": results}, indent=2, ensure_ascii=False))
            return 0
        return render_console(conn, query, args, show_pdf_hint=not getattr(args, "no_actions", False))
    finally:
        conn.close()


def decode_b64_query(value: str) -> str:
    try:
        return base64.b64decode(value.encode("ascii"), validate=True).decode("utf-8").strip()
    except Exception as exc:
        raise ValueError(f"Invalid Base64 query: {exc}") from exc


def command_pdf_file(args, query: str) -> int:
    """Create a report file only; do not start HTTP server or invoke CUPS.

    This mode is intended for the Windows Engineer Client, which copies the PDF
    with SCP to the Windows-accessible DRL share and opens it locally.
    """
    db = Path(args.db)
    if not db.exists():
        print(f"ERROR: unified knowledge DB not built: {db}", file=sys.stderr)
        return 2
    conn = base.connect_ro(db)
    try:
        _, groups, elapsed_ms = search_report(conn, query, args)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = Path(args.reports_dir) / f"NOVA_DRL_{slugify(query)}_{stamp}.pdf"
        write_pdf(path, query, groups, elapsed_ms)
        # Deliberately machine-readable single-line marker. The Windows client
        # does not need to parse ANSI text or a browser URL.
        print(f"NOVA_DRL_REPORT_PATH={path}")
        return 0
    finally:
        conn.close()


def command_pdf(args, query: str) -> int:
    db = Path(args.db)
    if not db.exists():
        print(f"ERROR: unified knowledge DB not built: {db}", file=sys.stderr)
        return 2
    conn = base.connect_ro(db)
    try:
        create_pdf_for_query(conn, query, args)
        return 0
    finally:
        conn.close()


def command_print(args, query: str) -> int:
    db = Path(args.db)
    if not db.exists():
        print(f"ERROR: unified knowledge DB not built: {db}", file=sys.stderr)
        return 2
    conn = base.connect_ro(db)
    try:
        create_and_print_for_query(conn, query, args)
        return 0
    finally:
        conn.close()


def command_prompt(args) -> int:
    db = Path(args.db)
    if not db.exists():
        print(f"Unified knowledge DB not built: {db}")
        print("Run the unified knowledge build first.")
        return 2
    conn = base.connect_ro(db)
    last_query: Optional[str] = None
    print(f"Nova DRL Full-Corpus Unified Knowledge Search v{VERSION}")
    print("Type any full/partial PN, model, serial, RMA, DRL log, customer PO, Digi-Key/Mouser order ref, or identifying text.")
    print(f"Simple lookup is local/indexed; no AI call. Commands: :help  :status  {blue(':pdf')}  {blue(':print')}  :quit")
    try:
        while True:
            try:
                q = input("NOVA-DRL> ").strip()
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print()
                continue
            if not q:
                continue
            if q in {":q", ":quit", "quit", "exit"}:
                break
            if q == ":help":
                print("Search examples: BRD-1526990 | 1526990 | S07211 | 53434 | DGK52102 | MSR 56889 | IXFX24N100 | RCL1A")
                print(f"{blue(':pdf')}              Create a printable PDF and show clickable + plain browser URLs")
                print(f"{blue(':pdf <search>')}     Create a printable PDF for another search")
                print(f"{blue(':print')}            Create PDF and send it to the configured/default server printer")
                print(f"{blue(':print <search>')}   Print another search directly")
                print(":status           Show local index counts")
                print("Interpretive troubleshooting remains the AI layer above this fast retrieval index.")
                continue
            if q == ":status":
                c = base.db_counts(conn)
                print(f"files={c['files']:,} events={c['repair_events']:,} products={c['product_families']:,} product_parts={c['product_parts']:,} RMAs={c['rma_refs']:,} customer_POs={c.get('customer_po_refs',0):,} orders={c['procurement_refs']:,}")
                continue
            if q == ":pdf" or q.startswith(":pdf "):
                target = compact(q[5:]) if q.startswith(":pdf ") else last_query
                if not target:
                    print("No previous search. Type a search first, or use :pdf <search>.")
                    continue
                create_pdf_for_query(conn, target, args)
                continue
            if q == ":print" or q.startswith(":print "):
                target = compact(q[7:]) if q.startswith(":print ") else last_query
                if not target:
                    print("No previous search. Type a search first, or use :print <search>.")
                    continue
                create_and_print_for_query(conn, target, args)
                continue
            last_query = q
            render_console(conn, q, args)
    finally:
        conn.close()
    return 0


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=f"Nova DRL Unified Knowledge Search + Print v{VERSION}")
    p.add_argument("--file-index", default=base.DEFAULT_FILE_INDEX)
    p.add_argument("--full-root", default=base.DEFAULT_FULL_ROOT)
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--reports-dir", default=DEFAULT_REPORTS_DIR)
    p.add_argument("--report-port", type=int, default=DEFAULT_REPORT_PORT)
    p.add_argument("--printer", default=os.environ.get("NOVA_DRL_PRINTER"), help="Optional CUPS printer name for :print/--print; default uses the system printer")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--plan-only", action="store_true")
    mode.add_argument("--build", action="store_true")
    mode.add_argument("--refresh", action="store_true")
    mode.add_argument("--prompt", action="store_true")
    mode.add_argument("--self-check", action="store_true")
    mode.add_argument("--search")
    mode.add_argument("--search-b64", help="Base64 UTF-8 query for Windows Engineer Client")
    mode.add_argument("--pdf")
    mode.add_argument("--pdf-file-b64", help="Base64 UTF-8 query; create report file only and print NOVA_DRL_REPORT_PATH")
    mode.add_argument("--print", dest="print_query")
    mode.add_argument("--serve-reports", action="store_true")
    p.add_argument("--top", type=int, default=8, help="Maximum results per displayed/report section")
    p.add_argument("--candidate-limit", type=int, default=800)
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-actions", action="store_true", help="Suppress :pdf/:print action hint (used by Windows Engineer Client)")
    p.add_argument("--self-check-warn-ms", type=float, default=250.0)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = make_parser().parse_args(argv)
    if args.status:
        return command_status(args)
    if args.plan_only:
        return command_plan(args)
    if args.build or args.refresh:
        return command_build(args)
    if args.self_check:
        return command_self_check(args)
    if args.search is not None:
        return command_search(args, args.search)
    if args.search_b64 is not None:
        try:
            q = decode_b64_query(args.search_b64)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        return command_search(args, q)
    if args.pdf is not None:
        return command_pdf(args, args.pdf)
    if args.pdf_file_b64 is not None:
        try:
            q = decode_b64_query(args.pdf_file_b64)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        return command_pdf_file(args, q)
    if args.print_query is not None:
        return command_print(args, args.print_query)
    if args.serve_reports:
        return serve_reports(args)
    return command_prompt(args)


if __name__ == "__main__":
    raise SystemExit(main())
