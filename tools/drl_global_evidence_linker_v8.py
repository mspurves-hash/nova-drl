#!/usr/bin/env python3
"""Generic evidence matching/linking helpers for Nova DRL v8 section-authority diagnostics.

IMPORTANT:
- No product-specific or RCL1A-specific part numbers live in this module.
- This module never uses expected benchmark counts to decide a match.
- It treats a validated replacement-section role as stronger evidence than a nearby verb.
- It preserves ambiguous evidence instead of forcing it into a specific family.
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




def target_requires_discriminator(target_text: str) -> bool:
    sp = parse_specs(target_text)
    return bool(sp['amps'] or sp['volts'] or sp['styles'])


def evidence_has_target_discriminator(target_text: str, alias: str, evidence_text: str, match: Match) -> bool:
    """Require a family-specific clue when the target has specs/style and the evidence is generic.

    Strong PN-like alias matches remain valid even when a voltage/style is omitted in handwriting.
    Generic descriptors such as "15 amp fuse" are preserved but not forced into 250 V vs 600 V.
    """
    if match.exactish:
        return True
    if match.reason == 'fuzzy-pn' and pn_likeness(alias) >= 0.55:
        return True
    t = parse_specs(target_text); e = parse_specs(evidence_text)
    if t['volts'] and e['volts'] and not t['volts'].isdisjoint(e['volts']):
        return True
    if t['styles'] and e['styles'] and not t['styles'].isdisjoint(e['styles']):
        return True
    # An alias with a distinctive alphabetic PN-like stem can itself discriminate the family.
    ac = compact(alias)
    if any(ch.isalpha() for ch in ac) and any(ch.isdigit() for ch in ac) and len(ac) >= 5:
        ratio = SequenceMatcher(None, ocr_fold_token(alias), ocr_fold_token(evidence_text)).ratio()
        if ratio >= 0.78:
            return True
    return not target_requires_discriminator(target_text)


def family_candidates(families: Iterable[dict], evidence_text: str, *, threshold: float = 0.80):
    """Return scored family candidates without using benchmark expected counts."""
    out=[]
    for fam in families:
        target_text=fam.get('reference','')
        fam_best=None
        for alias in fam.get('aliases',[]):
            m=match_alias(alias,evidence_text)
            if m.score <= 0:
                continue
            if not evidence_has_target_discriminator(target_text, alias, evidence_text, m):
                continue
            specificity=0.04*pn_likeness(alias)
            specs=parse_specs(target_text)
            if specs['amps'] or specs['volts'] or specs['styles']:
                specificity += 0.015
            total=m.score+specificity
            rec=(total,m)
            if fam_best is None or rec[0]>fam_best[0]:
                fam_best=rec
        if fam_best and fam_best[0] >= threshold:
            out.append((fam_best[0],fam,fam_best[1]))
    out.sort(key=lambda x:x[0],reverse=True)
    return out


def best_family_match_v8(families: Iterable[dict], evidence_text: str, *, threshold: float = 0.80, ambiguity_margin: float = 0.035):
    """Best family match with abstention for unresolved same-class ambiguity."""
    cand=family_candidates(families,evidence_text,threshold=threshold)
    if not cand:
        return None,None,'no-match'
    top=cand[0]
    if len(cand)>1 and top[0]-cand[1][0] <= ambiguity_margin:
        a,b=top[1],cand[1][1]
        aw=component_words(a.get('reference','')); bw=component_words(b.get('reference',''))
        # If two same-class families remain nearly tied, preserve evidence as ambiguous rather than guessing.
        if aw and bw and (aw & bw):
            return None,None,'ambiguous-family'
    return top[1],top[2],'matched'


SPECIFIC_ELECTRONIC_WORDS = {
    'chip','ic','mosfet','transistor','diode','resistor','capacitor','fuse','rectifier',
    'driver','controller','opamp','op-amp','comparator','optocoupler','regulator','inductor','coil'
}


def broad_assembly_location_guard(target_text: str, evidence_text: str) -> bool:
    """True when a board/assembly appears to be a location for a more specific replaced component."""
    tw=component_words(target_text)
    if not (tw & {'board','assembly'}):
        return False
    low=clean(evidence_text).lower()
    ew=component_words(low)
    if not (ew & SPECIFIC_ELECTRONIC_WORDS):
        return False
    # Explicitly targeted board/assembly replacement wins over the guard.
    vm=re.search(r'(?i)\b(replac(?:e|ed|ing)|install(?:ed|ing)?|swap(?:ped|ping)?|chang(?:e|ed|ing)|new)\b', low)
    bm=re.search(r'(?i)\b(board|assembly)\b', low)
    if vm and bm and bm.start() > vm.end() and bm.start()-vm.end() <= 45:
        between=low[vm.end():bm.start()]
        if not re.search(r'(?i)\b(on|in|inside|onto|at)\b', between):
            return False
    if re.search(r'(?i)\b(board|assembly)\b.{0,30}\b(replac(?:e|ed|ing)|swap(?:ped|ping)?|chang(?:e|ed|ing))\b', low):
        return False
    return True


def authoritative_replacement_link(ev: Evidence, family: dict, match: Match) -> tuple[bool,str]:
    """Field role is authoritative: evidence emitted under the explicit replacement section is replacement evidence.

    We only suppress a broad board/assembly family when the phrase more clearly names a component on/in that assembly.
    PN-focused evidence requires an explicit local replacement relation unless it is fused later with same-page replacement evidence.
    """
    target=family.get('reference','')
    text=clean(ev.candidate if (not ev.context or ev.context==ev.candidate) else (ev.candidate+' '+ev.context))
    if ev.source == 'replacement':
        if broad_assembly_location_guard(target,text):
            return False,'assembly-location-guard'
        return True,'explicit-replacement-section'
    if ev.explicit_replacement:
        if broad_assembly_location_guard(target,text):
            return False,'assembly-location-guard'
        return True,'explicit-local-replacement'
    return False,'reference-only'


def evidence_component_signature(ev: Evidence) -> set[str]:
    return component_words((ev.candidate or '')+' '+(ev.context or ''))


def compatible_component_signature(a:set[str], b:set[str]) -> bool:
    if not a or not b:
        return False
    electronic={'ic','chip','controller','driver','opamp','op-amp','comparator','optocoupler','regulator','amplifier'}
    power={'mosfet','transistor'}
    rect={'rectifier','bridge','diode'}
    if a & b:
        return True
    if (a & electronic) and (b & electronic): return True
    if (a & power) and (b & power): return True
    if (a & rect) and (b & rect): return True
    return False

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
