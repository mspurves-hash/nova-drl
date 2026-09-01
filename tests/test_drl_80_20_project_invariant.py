#!/usr/bin/env python3
"""DRL Nova HARD 80/20 production invariant regression gate.

This gate follows /bin/nova-drl to the CURRENT production presentation script.
It must remain in future releases. Corpus recurrence owns product knowledge;
Matt's expertise is a sanity/exception layer unless he explicitly requests a
specific rule be promoted into DRL Nova.
"""
from pathlib import Path
import importlib.util, json, re, sys

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / 'bin' / 'nova-drl'
POLICY = ROOT / 'config' / 'drl_nova_project_invariants.json'
launcher = LAUNCHER.read_text(encoding='utf-8')
m = re.search(r'SCRIPT="/opt/nova-drl/(tools/[^"]+\.py)"', launcher)
assert m, 'production launcher does not declare a versioned Nova DRL search script'
UI = ROOT / m.group(1)
assert UI.exists(), f'production script missing from package: {UI}'

spec = importlib.util.spec_from_file_location('nova_drl_policy_gate_current', UI)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

assert mod.DRL_80_20_HARD_INVARIANT is True
assert mod.ALLOW_EXPERT_KNOWLEDGE_OVERRIDES is False
assert mod.EXPERT_INPUT_ROLE == 'sanity_check_only_unless_explicitly_promoted'
assert mod.PRODUCT_PART_MIN_REPAIRS >= 2
assert mod.GENERIC_ROOT_CAUSE_FIXES_MUST_BE_GLOBAL is True
assert mod.ALLOW_PRODUCT_SPECIFIC_RESOLVER_PATCHES is False
for forbidden_attr in ('KIT_CONFIG','load_standard_kit_rules','qualifying_repair_event_ids','REFERENCE_PN_EXPERT_MAP'):
    assert not hasattr(mod, forbidden_attr), f'forbidden expert-override path exists: {forbidden_attr}'

policy = json.loads(POLICY.read_text(encoding='utf-8'))
assert policy['80_20_rule'] == 'HARD_INVARIANT'
assert policy['corpus_volume_owns_product_knowledge'] is True
assert policy['expert_override_enabled'] is False
assert policy['expert_reference_pn_map_enabled'] is False
assert policy['reference_pn_source'] == 'corpus_recurring_observed_variants_only'
assert policy['explicit_user_promotion_required_for_expert_rule'] is True
assert policy['generic_root_cause_fixes_must_be_global'] is True
assert policy['product_suffix_resolution_scope'] == 'global_corpus_volume_rule'
assert policy['component_variant_resolution_scope'] == 'global_corpus_volume_rule'
assert policy['product_specific_resolver_patches_forbidden_unless_explicit_exception'] is True

source = UI.read_text(encoding='utf-8').casefold()
for forbidden in (
    'human_confirmed_standard_kit',
    'drl_standard_repair_kits',
    'expert-confirmed standard repair-kit',
    'reference_pn_expert_map',
):
    assert forbidden not in source, f'forbidden expert-override path found: {forbidden}'

print(f'PASS: DRL Nova HARD 80/20 production invariant — {UI.name} — expert overrides disabled')
