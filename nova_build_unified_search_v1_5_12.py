#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

DEFAULT_SOURCE = Path('/opt/nova-drl/tools/nova_drl_unified_knowledge_index_v1_5_11.py')
DEFAULT_TARGET = Path('/opt/nova-drl/tools/nova_drl_unified_knowledge_index_v1_5_12.py')

INSERT_BEFORE = 'def product_view_groups('
PARTS_LINE = '    parts = aggregate_product_parts(conn, resolved["families"], resolved["base_part_number"])'
OVERLAY_LINE = PARTS_LINE + '\n    parts = _apply_validated_parts_overlay(parts)'

HELPER = '''
# ---------------------------------------------------------------------------
# v1.5.12 validated Parts presentation overlay
# ---------------------------------------------------------------------------
_VALIDATED_PARTS_CACHE_DEFAULT = (
    "/opt/nova-drl/output/tier_a_validation_cache_v1_7_5/"
    "validated_global_identity_cache_v1_7_5.jsonl"
)
_VALIDATED_PARTS_CACHE_INDEX = None


def _validated_part_key(value):
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def _load_validated_parts_cache():
    global _VALIDATED_PARTS_CACHE_INDEX
    if _VALIDATED_PARTS_CACHE_INDEX is not None:
        return _VALIDATED_PARTS_CACHE_INDEX

    path = os.environ.get("NOVA_PARTS_VALIDATED_CACHE", _VALIDATED_PARTS_CACHE_DEFAULT)
    index = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue

                canonical = str(row.get("canonical_label") or "").strip()
                if not canonical:
                    continue

                labels = [row.get("display_label"), canonical]
                labels.extend(row.get("observed_variants") or [])
                for label in labels:
                    key = _validated_part_key(label)
                    if key and key not in index:
                        index[key] = {
                            "canonical_label": canonical,
                            "manufacturer": row.get("validated_manufacturer"),
                            "confidence": row.get("validation_confidence"),
                            "source_authority": row.get("source_authority"),
                            "global_identity_id": row.get("global_identity_id"),
                        }
    except OSError:
        index = {}

    _VALIDATED_PARTS_CACHE_INDEX = index
    return index


def _apply_validated_parts_overlay(parts):
    cache = _load_validated_parts_cache()
    if not cache:
        return parts

    out = []
    for row0 in parts:
        row = dict(row0)
        observed = str(row.get("primary_value") or row.get("title") or "").strip()
        hit = cache.get(_validated_part_key(observed))
        if not hit:
            out.append(row)
            continue

        canonical = str(hit.get("canonical_label") or "").strip()
        if not canonical:
            out.append(row)
            continue

        payload = dict(row.get("payload") or {})
        payload["observed_reference"] = observed
        payload["validated_canonical"] = canonical
        payload["validated_manufacturer"] = hit.get("manufacturer")
        payload["validation_confidence"] = hit.get("confidence")
        payload["validation_source_authority"] = hit.get("source_authority")
        payload["validation_global_identity_id"] = hit.get("global_identity_id")
        payload["validation_overlay"] = "tier_a_global_cache_v1_7_5"

        row["primary_value"] = canonical
        row["title"] = canonical
        row["payload"] = payload
        out.append(row)

    return out


'''


def patch_text(text: str) -> str:
    if text.count(INSERT_BEFORE) != 1:
        raise RuntimeError(
            f'Expected exactly one product_view_groups marker; found {text.count(INSERT_BEFORE)}'
        )
    if text.count(PARTS_LINE) != 1:
        raise RuntimeError(
            f'Expected exactly one Parts aggregation line; found {text.count(PARTS_LINE)}'
        )
    if '_apply_validated_parts_overlay' in text:
        raise RuntimeError('Source already contains validated Parts overlay')

    text = text.replace(INSERT_BEFORE, HELPER + INSERT_BEFORE, 1)
    text = text.replace(PARTS_LINE, OVERLAY_LINE, 1)
    text = text.replace('v1.5.11', 'v1.5.12')
    text = text.replace('V1_5_11', 'V1_5_12')
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', default=str(DEFAULT_SOURCE))
    ap.add_argument('--target', default=str(DEFAULT_TARGET))
    ap.add_argument('--plan-only', action='store_true')
    args = ap.parse_args()

    source = Path(args.source)
    target = Path(args.target)
    if not source.exists():
        raise RuntimeError(f'Missing v1.5.11 source: {source}')

    patched = patch_text(source.read_text(encoding='utf-8'))

    print('# Nova DRL Unified Search Validated-Parts Overlay Builder v1.5.12')
    print(f'Source: {source}')
    print(f'Target: {target}')
    print('Database changes: NO')
    print('Repair/Parts counts changed: NO')
    print('Fuzzy matching: NO')
    print('Validated cache optional: YES')
    print('Fallback if cache absent: v1.5.11 behavior')
    print('Qdrant: OFF')

    if args.plan_only:
        print('PLAN ONLY: target not written.')
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(patched, encoding='utf-8')
    print(f'WROTE: {target}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
