#!/usr/bin/env python3
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILDER = HERE / 'nova_build_unified_search_v1_5_12.py'


def main():
    with tempfile.TemporaryDirectory() as td0:
        td = Path(td0)
        src = td / 'v1_5_11.py'
        dst = td / 'v1_5_12.py'
        cache = td / 'cache.jsonl'

        src.write_text(
            "import json, os, re\n"
            "VERSION='v1.5.11'\n"
            "def aggregate_product_parts(conn, families, base_part_number=None):\n"
            "    return conn\n\n"
            "def product_view_groups(conn, query, generic_groups):\n"
            "    resolved={'families':['F'],'base_part_number':'X'}\n"
            "    parts = aggregate_product_parts(conn, resolved[\"families\"], resolved[\"base_part_number\"])\n"
            "    return parts\n",
            encoding='utf-8',
        )

        run = subprocess.run(
            [sys.executable, str(BUILDER), '--source', str(src), '--target', str(dst)],
            capture_output=True, text=True,
        )
        assert run.returncode == 0, run.stdout + '\n' + run.stderr
        assert dst.exists()

        cache.write_text(
            json.dumps({
                'global_identity_id':'g1',
                'display_label':'Lm324n',
                'observed_variants':['Lm324n','LM 324n'],
                'canonical_label':'LM324N',
                'validated_manufacturer':'Texas Instruments',
                'validation_confidence':'high',
                'source_authority':'primary_manufacturer',
            }) + '\n',
            encoding='utf-8',
        )

        os.environ['NOVA_PARTS_VALIDATED_CACHE'] = str(cache)
        spec = importlib.util.spec_from_file_location('patched', dst)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        original = [{
            'primary_value':'LM 324n',
            'title':'LM 324n',
            'payload':{'repairs':12,'event_ids':['e1','e2']},
        }]
        over = mod._apply_validated_parts_overlay(original)
        assert over[0]['primary_value'] == 'LM324N'
        assert over[0]['payload']['repairs'] == 12
        assert over[0]['payload']['event_ids'] == ['e1','e2']
        assert over[0]['payload']['observed_reference'] == 'LM 324n'
        assert over[0]['payload']['validated_manufacturer'] == 'Texas Instruments'

        other = [{'primary_value':'ABC123','title':'ABC123','payload':{'repairs':3}}]
        assert mod._apply_validated_parts_overlay(other)[0]['primary_value'] == 'ABC123'

        plan_target = td / 'plan.py'
        run2 = subprocess.run(
            [sys.executable, str(BUILDER), '--source', str(src),
             '--target', str(plan_target), '--plan-only'],
            capture_output=True, text=True,
        )
        assert run2.returncode == 0 and not plan_target.exists()

    print('PASS: Nova DRL Unified Search v1.5.12 validated-parts overlay tests')


if __name__ == '__main__':
    main()
