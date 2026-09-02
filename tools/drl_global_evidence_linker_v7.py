#!/usr/bin/env python3
"""Generic evidence matching/linking helpers for Nova DRL benchmark diagnostics.

IMPORTANT:
- No product-specific or RCL1A-specific part numbers live in this module.
- This module never uses expected benchmark counts to decide a match.
- It is intentionally conservative about replacement-object linkage.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable

REPL_VERB_RE = re.compile(r"(?i)\b(replac(?:e|ed|ing)|install(?:ed|ing)?|swap(?:ped|ping)?|chang(?:e|ed|ing)|rebuild|rebuilt|used|fit(?:ted)?|pulled\s+from|donor|new)\b")
LOCATION_PREP_RE = re.compile(r"(?i)\b(on|in|at|inside|onto|from|for)\b")

COMPONENT_WORDS = {
    'fuse','holder','board','assembly','mosfet','transistor','rectifier','bridge','ic','chip',
    'controller','driver','opamp','op-amp','comparator','optocoupler','motor','encoder','belt',
    'sensor','ccd','led','solenoid','bearing','connector','relay','resistor','capacitor','diode',
    'inductor','coil','screw','lead','gear','pin','pins','filter','fan','regulator','amplifier'
}
STYLE_WORDS = {'pigtail','pig-tail','cartridge','mini','standard','axial','holder','daughter','smart'}

@dataclass(frozen=True)
class Evidence:
    page: int
    source: str          # replacement | pn | pn_focus
    candidate: str       # candidate identifier / phrase
    context: str         # containing line / context
    explicit_replacement: bool

@dataclass(frozen=True)
class Match:
    score: float
    alias: str
    exactish: bool
    spec_compatible: bool
    component_compatible: bool
    reason: str


def clean(s: str) -> str:
    return re.sub(r'\s+', ' ', s or '').strip()


def compact(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', (s or '').lower())


def alnum_token(s: str) -> str:
    return re.sub(r'[^A-Za-z0-9./+-]+', '', s or '')


def ocr_fold_token(s: str) -> str:
    """Conservative OCR fold for mixed alphanumeric tokens.

    Only folds likely confusables when the token already contains a digit, which avoids turning
    normal English words into number strings.
    """
    t = compact(s)
    if not any(ch.isdigit() for ch in t):
        return t
    trans = str.maketrans({'o':'0','q':'0','i':'1','l':'1'})
    return t.translate(trans)


def digit_groups(s: str) -> tuple[str, ...]:
    return tuple(re.findall(r'\d+', (s or '').lower()))


def alpha_groups(s: str) -> tuple[str, ...]:
    return tuple(x for x in re.findall(r'[a-z]+', (s or '').lower()) if len(x) >= 2)


def pn_likeness(s: str) -> float:
    c = compact(s)
    if len(c) < 4:
        return 0.0
    has_a = any(ch.isalpha() for ch in c)
    has_d = any(ch.isdigit() for ch in c)
    if has_a and has_d:
        return min(1.0, 0.55 + min(len(c), 18) / 40.0)
    if has_d and len(c) >= 6:
        return 0.55
    return 0.2


def parse_specs(s: str) -> dict[str, set[str]]:
    text = (s or '').lower().replace('amps','a').replace('amp','a').replace('volts','v').replace('volt','v')
    amps = set(re.findall(r'(?<!\d)(\d+(?:\.\d+)?)\s*a\b', text))
    volts = set(re.findall(r'(?<!\d)(\d+(?:\.\d+)?)\s*v\b', text))
    styles = {w for w in STYLE_WORDS if w in text}
    return {'amps': amps, 'volts': volts, 'styles': styles}


def spec_compatible(target_text: str, evidence_text: str) -> bool:
    t = parse_specs(target_text); e = parse_specs(evidence_text)
    # Reject only explicit contradictions. Missing specs remain allowed.
    if t['amps'] and e['amps'] and t['amps'].isdisjoint(e['amps']):
        return False
    if t['volts'] and e['volts'] and t['volts'].isdisjoint(e['volts']):
        return False
    ts, es = t['styles'], e['styles']
    # Specific physical fuse styles should not silently cross-map.
    if 'pigtail' in ts or 'pig-tail' in ts or 'axial' in ts:
        if es & {'cartridge','mini','standard'}:
            return False
    if ts & {'cartridge','mini','standard'}:
        if es & {'pigtail','pig-tail','axial'}:
            return False
    return True


def component_words(s: str) -> set[str]:
    toks = set(re.findall(r'[a-z]+(?:-[a-z]+)?', (s or '').lower()))
    return toks & COMPONENT_WORDS


def component_compatible(target_text: str, evidence_text: str) -> bool:
    tw = component_words(target_text); ew = component_words(evidence_text)
    if not tw or not ew:
        return True
    # Some target labels contain a role and a generic 'ic'. Treat any electronics role as compatible with IC/chip.
    if (tw & {'ic','chip','controller','driver','opamp','op-amp','comparator','optocoupler','regulator','amplifier'}) and \
       (ew & {'ic','chip','controller','driver','opamp','op-amp','comparator','optocoupler','regulator','amplifier'}):
        return True
    if (tw & {'mosfet','transistor'}) and (ew & {'mosfet','transistor'}):
        return True
    if (tw & {'rectifier','bridge','diode'}) and (ew & {'rectifier','bridge','diode'}):
        return True
    return bool(tw & ew)


def match_alias(alias: str, evidence_text: str) -> Match:
    a = compact(alias); e = compact(evidence_text)
    if not a or not e:
        return Match(0.0, alias, False, True, True, 'empty')
    specs_ok = spec_compatible(alias, evidence_text)
    comp_ok = component_compatible(alias, evidence_text)
    if not specs_ok:
        return Match(0.0, alias, False, False, comp_ok, 'spec-conflict')
    if a == e:
        return Match(1.00, alias, True, specs_ok, comp_ok, 'exact')
    if len(a) >= 5 and a in e:
        return Match(0.98, alias, True, specs_ok, comp_ok, 'alias-substring')
    if len(e) >= 5 and e in a:
        return Match(0.95, alias, True, specs_ok, comp_ok, 'candidate-substring')

    af, ef = ocr_fold_token(alias), ocr_fold_token(evidence_text)
    ratio = SequenceMatcher(None, af, ef).ratio() if af and ef else 0.0
    ad, ed = digit_groups(alias), digit_groups(evidence_text)
    aa, ea = set(alpha_groups(alias)), set(alpha_groups(evidence_text))
    digit_overlap = 0.0
    if ad and ed:
        sa, se = set(ad), set(ed)
        digit_overlap = len(sa & se) / max(1, len(sa | se))
    alpha_overlap = 0.0
    if aa and ea:
        alpha_overlap = len(aa & ea) / max(1, len(aa | ea))

    # Strong PN-like OCR/spacing variant: similar shape plus shared numeric core.
    pnish = max(pn_likeness(alias), pn_likeness(evidence_text))
    if pnish >= 0.55 and ratio >= 0.78 and (digit_overlap >= 0.5 or (not ad and not ed)):
        score = 0.78 + 0.12*ratio + 0.05*digit_overlap + 0.03*alpha_overlap
        return Match(min(score,0.94), alias, False, specs_ok, comp_ok, 'fuzzy-pn')

    # Descriptor/spec match: require useful lexical overlap and no explicit spec contradiction.
    atoks = {x for x in re.findall(r'[a-z0-9]+', alias.lower()) if len(x) >= 2}
    etoks = {x for x in re.findall(r'[a-z0-9]+', evidence_text.lower()) if len(x) >= 2}
    overlap = len(atoks & etoks) / max(1, len(atoks))
    if overlap >= 0.60 and comp_ok:
        return Match(0.70 + 0.15*overlap, alias, False, specs_ok, comp_ok, 'descriptor')
    return Match(0.0, alias, False, specs_ok, comp_ok, 'no-match')


def best_family_match(families: Iterable[dict], evidence_text: str, *, threshold: float = 0.80):
    """Return (family, match) using aliases; expected counts are deliberately ignored."""
    best = None
    for fam in families:
        target_text = fam.get('reference','')
        for alias in fam.get('aliases',[]):
            m = match_alias(alias, evidence_text)
            if m.score <= 0:
                continue
            # Slightly favor specific PN-like aliases and specific spec-bearing target labels.
            specificity = 0.04*pn_likeness(alias)
            specs = parse_specs(target_text)
            if specs['amps'] or specs['volts']:
                specificity += 0.015
            total = m.score + specificity
            rec = (total, fam, m)
            if best is None or rec[0] > best[0]:
                best = rec
    if best is None or best[0] < threshold:
        return None, None
    return best[1], best[2]


def explicit_replacement_object(line: str, target_alias: str) -> bool:
    """Conservative relation check: is target_alias the replacement object, not merely a location/context?"""
    s = clean(line)
    if not REPL_VERB_RE.search(s):
        return False
    low = s.lower(); alias_low = target_alias.lower()
    # Exact/near literal phrase location when available.
    idx = low.find(alias_low)
    if idx < 0:
        # compact matching cannot give a safe span; caller may rely on PN-context evidence instead.
        return True
    verb = REPL_VERB_RE.search(low)
    if not verb:
        return False
    if idx >= verb.end():
        between = low[verb.end():idx]
        # "replaced IC on smart board" => smart board is a location, not replacement object.
        if re.search(r'\b(on|in|inside|onto|at)\b', between):
            return False
        return True
    # "smart board replaced" is still a replacement relation.
    if idx < verb.start() and len(low[idx:verb.start()]) < 60:
        return True
    return False


def heading_sections(text: str) -> dict[str,str]:
    headings = [
        'REPORTED FAILURE / CUSTOMER COMPLAINT:',
        'EXPLICIT PARTS / COMPONENTS REPLACED, INSTALLED, SWAPPED, REBUILT OR USED:',
        'PART / REFERENCE NUMBERS:',
        'OTHER TECHNICAL REPAIR / SERVICE ACTIONS:',
        'EXPLICIT TEST / OUTCOME:',
        'TRACKING / ORDER METADATA:',
    ]
    out={h:'' for h in headings}; cur=None
    for raw in (text or '').splitlines():
        line=raw.strip(); hit=None
        for h in headings:
            if line.upper().startswith(h.upper()):
                hit=h; break
        if hit:
            cur=hit; rest=line[len(hit):].strip()
            if rest: out[cur]+=rest+'\n'
        elif cur:
            out[cur]+=raw+'\n'
    return out


def parse_pn_focus(text: str, page: int) -> list[Evidence]:
    out=[]
    for raw in (text or '').splitlines():
        line=clean(raw)
        if not line or line.upper() == 'NONE VISIBLE.':
            continue
        m=re.search(r'(?i)PART\s*/?\s*REFERENCE\s*:\s*(.*?)\s*(?:\|\s*CONTEXT\s*:\s*(.*))?$', line)
        if m:
            cand=clean(m.group(1)); ctx=clean(m.group(2) or '')
            if cand and cand.upper() not in {'NONE','NONE VISIBLE'}:
                out.append(Evidence(page,'pn_focus',cand,ctx,bool(REPL_VERB_RE.search(ctx))))
    return out


def parse_high_recall(text: str, page: int) -> list[Evidence]:
    sec=heading_sections(text)
    repl_h='EXPLICIT PARTS / COMPONENTS REPLACED, INSTALLED, SWAPPED, REBUILT OR USED:'
    pn_h='PART / REFERENCE NUMBERS:'
    out=[]
    for raw in sec.get(repl_h,'').splitlines():
        line=clean(re.sub(r'^[\-*•\d.()\s]+','',raw))
        if line:
            out.append(Evidence(page,'replacement',line,line,True))
    for raw in sec.get(pn_h,'').splitlines():
        line=clean(re.sub(r'^[\-*•\d.()\s]+','',raw))
        if line:
            out.append(Evidence(page,'pn',line,line,bool(REPL_VERB_RE.search(line))))
    return out


def extract_evidence(high_text: str, pn_text: str, page: int) -> list[Evidence]:
    return parse_high_recall(high_text,page) + parse_pn_focus(pn_text,page)
