#!/usr/bin/env python3
"""
Nova DRL Repair Evidence Collector v1.4
=======================================

Builds a complete, read-only evidence dossier for every repair event in one
DRL serial-number folder.

The traveler remains the primary repair anchor, but it is not treated as the
only source. Supporting reports, checklists, failure-analysis documents,
photos, configuration files, and existing Traveler Reader outputs are linked
into the same Repair Evidence Bundle.

Core hierarchy
--------------
Model -> Serial Number -> Repair Event (YYMMDD###) -> Evidence

Evidence scopes
---------------
- repair_event: exactly one valid DRL log number is present in the source path
- unit_level: no log number is present; evidence applies to the physical unit
- unresolved: conflicting or invalid log-number evidence requires review

Safety
------
- Never moves, renames, edits, or deletes DRL source files.
- Never writes to the NAS/source folder.
- Writes dossiers only beneath the local Nova output directory.
- Does not call Ollama and does not write to Qdrant.
"""

import argparse
import csv
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

VERSION = '1.4.0'

DEFAULT_OEMS = [
    'GENMARK','BROOKS','ASYST','PRI','RORZE','YASKAWA',
    'KAWASAKI','NIKON','TAZMO','HINE'
]
DEFAULT_TECHNICIANS = ['ERICH','MATT']
DEFAULT_SITES = {'MTV':'Micron Technology Virginia'}

LOG_PREFIX_RE = re.compile(r'^(?P<log>\d{9})\b', re.I)
ANY_NINE_DIGIT_RE = re.compile(r'(?<!\d)(?P<log>\d{9})(?!\d)')
TRAVELER_RE = re.compile(
    r'^(?P<log>\d{9})\s+Line\s+Card\s+(?P<kind>Original|Warranty)\b.*'
    r'\.(jpg|jpeg|png|pdf|tif|tiff)$',
    re.I,
)

PLAIN_TEXT_EXTS = {'.txt','.md','.csv','.tsv','.json','.xml','.log'}
IMAGE_EXTS = {'.jpg','.jpeg','.png','.tif','.tiff','.bmp','.gif','.webp'}
VIDEO_EXTS = {'.mp4','.mov','.avi','.mkv','.wmv','.mpg','.mpeg','.m4v'}
CONFIG_EXTS = {'.par','.prm','.cfg','.conf','.ini','.dat','.bin','.bak','.hex','.eep','.rom'}
DOC_EXTS = {'.pdf','.doc','.docx','.rtf'}
SHEET_EXTS = {'.xls','.xlsx','.xlsm','.ods'}

ROLE_AUTHORITY = {
    'traveler': 'primary_repair_anchor',
    'failure_analysis_report': 'diagnosis_evidence',
    'robot_test_report': 'final_test_evidence',
    'robot_checklist': 'procedure_completion_evidence',
    'internal_checklist_notes': 'technician_working_notes',
    'rbt_report': 'supporting_repair_report',
    'receiving_photo': 'incoming_condition_visual_evidence',
    'return_packaging_photo': 'packaging_visual_evidence',
    'configuration_evidence': 'unit_configuration_evidence',
    'parameter_file': 'unit_configuration_evidence',
    'photo': 'visual_evidence',
    'movie': 'visual_procedure_or_condition_evidence',
    'document': 'supporting_document',
    'structured_document': 'supporting_structured_document',
    'technical_file': 'technical_evidence',
    'unknown': 'unclassified_evidence',
}


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def clean_spaces(value):
    return re.sub(r'\s+', ' ', str(value)).strip()


def normalize_upper(value):
    return clean_spaces(value).upper()


def safe_name(value):
    return re.sub(r'[^A-Za-z0-9._-]+', '_', str(value)).strip('_') or 'item'


def path_id(relative_path):
    return hashlib.sha256(str(relative_path).encode('utf-8')).hexdigest()[:16]


def sha256_file(path, chunk_size=1024*1024):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def load_reference_config(config_dir):
    cfg = Path(config_dir)
    refs = {
        'oems': list(DEFAULT_OEMS),
        'technicians': list(DEFAULT_TECHNICIANS),
        'sites': dict(DEFAULT_SITES),
    }
    try:
        p = cfg/'oems.json'
        if p.exists():
            refs['oems'] = [normalize_upper(x) for x in json.loads(p.read_text(encoding='utf-8')).get('oems',[])]
    except Exception:
        pass
    try:
        p = cfg/'technicians.json'
        if p.exists():
            refs['technicians'] = [normalize_upper(x) for x in json.loads(p.read_text(encoding='utf-8')).get('technicians',[])]
    except Exception:
        pass
    try:
        p = cfg/'site_codes.json'
        if p.exists():
            refs['sites'] = {normalize_upper(k):clean_spaces(v) for k,v in json.loads(p.read_text(encoding='utf-8')).get('sites',{}).items()}
    except Exception:
        pass
    return refs


def parse_serial_folder_name(folder_name, refs):
    raw = clean_spaces(folder_name)
    out = {
        'original_folder_name': raw,
        'equipment_type': None,
        'model': None,
        'oem': None,
        'serial_number': None,
        'customer': None,
        'site_code': None,
        'site_name': None,
        'technician': None,
        'parse_confidence': 'low',
        'parse_notes': [],
    }
    if ' - ' not in raw:
        out['parse_notes'].append("Missing expected ' - ' separator.")
        return out
    typ, rest = raw.split(' - ',1)
    out['equipment_type'] = normalize_upper(typ)
    tokens = rest.split()
    ups = [normalize_upper(x) for x in tokens]
    oem_idx = next((i for i,x in enumerate(ups) if x in refs['oems']), None)
    if oem_idx is None:
        out['parse_notes'].append('Known OEM not found.')
        return out
    out['model'] = ' '.join(tokens[:oem_idx])
    out['oem'] = ups[oem_idx]
    sn_idx = next((i for i in range(oem_idx+1,len(ups)) if ups[i]=='SN'), None)
    if sn_idx is None or sn_idx+1 >= len(tokens):
        out['parse_notes'].append('SN marker or serial number not found.')
        return out
    out['serial_number'] = tokens[sn_idx+1]
    tail = tokens[sn_idx+2:]
    if tail and normalize_upper(tail[-1]) in refs['technicians']:
        out['technician'] = normalize_upper(tail[-1])
        tail = tail[:-1]
    else:
        out['parse_notes'].append('Technician not confidently identified.')
    if tail and normalize_upper(tail[-1]) in refs['sites']:
        code = normalize_upper(tail[-1])
        out['site_code'] = code
        out['site_name'] = refs['sites'][code]
        tail = tail[:-1]
    else:
        out['parse_notes'].append('Site code not confidently identified.')
    if tail:
        out['customer'] = ' '.join(tail)
    else:
        out['parse_notes'].append('Customer could not be parsed.')
    strong = [out['equipment_type'],out['model'],out['oem'],out['serial_number'],out['customer']]
    if all(strong) and out['site_code'] and out['technician']:
        out['parse_confidence'] = 'high'
    elif all(strong):
        out['parse_confidence'] = 'medium'
    return out


def decode_log_number(log_number):
    s = str(log_number or '')
    out = {
        'log_number': s or None,
        'valid': False,
        'repair_date': None,
        'repair_date_display': None,
        'daily_sequence': None,
        'error': None,
    }
    if not re.fullmatch(r'\d{9}', s):
        out['error'] = 'Log number is not exactly nine digits.'
        return out
    yy, mm, dd = int(s[:2]), int(s[2:4]), int(s[4:6])
    try:
        d = date(2000+yy, mm, dd)
    except ValueError as exc:
        out['error'] = 'Invalid encoded date: {}'.format(exc)
        return out
    out.update({
        'valid': True,
        'repair_date': d.isoformat(),
        'repair_date_display': '{}/{}/{}'.format(mm,dd,2000+yy),
        'daily_sequence': s[6:],
    })
    return out


def collect_log_candidates(relative_path):
    rel = Path(relative_path)
    candidates = []
    for part in rel.parts:
        m = LOG_PREFIX_RE.match(part)
        if m:
            candidates.append(m.group('log'))
    unique = []
    for log in candidates:
        if log not in unique:
            unique.append(log)
    return unique


def classify_assignment(relative_path):
    logs = collect_log_candidates(relative_path)
    if len(logs) == 1:
        decoded = decode_log_number(logs[0])
        if decoded['valid']:
            return {'scope':'repair_event','log_number':logs[0],'reason':'Exactly one valid DRL log prefix found.'}
        return {'scope':'unresolved','log_number':logs[0],'reason':'Nine-digit prefix does not encode a valid DRL date.'}
    if len(logs) == 0:
        return {'scope':'unit_level','log_number':None,'reason':'No DRL log prefix found.'}
    return {'scope':'unresolved','log_number':None,'reason':'Conflicting log prefixes found: {}'.format(', '.join(logs))}


def classify_role(path):
    name = path.name
    low = name.lower()
    ext = path.suffix.lower()
    m = TRAVELER_RE.match(name)
    if m:
        return {'role':'traveler','warranty':m.group('kind').lower()=='warranty','confidence':'confirmed','reason':'Matches DRL traveler naming rule.'}
    if any(x in low for x in ['failure analysis report','failure analysis','field failure report','field failure','incoming failure analysis','gold incoming failure analysis']):
        return {'role':'failure_analysis_report','warranty':False,'confidence':'high','reason':'Failure-analysis wording.'}
    if 'robot test report' in low or ('test report' in low and 'robot' in low):
        return {'role':'robot_test_report','warranty':False,'confidence':'high','reason':'Robot test-report wording.'}
    if 'robot checklist' in low:
        return {'role':'robot_checklist','warranty':False,'confidence':'high','reason':'Robot checklist wording.'}
    if 'internal checklist notes' in low or 'checklist notes' in low:
        return {'role':'internal_checklist_notes','warranty':False,'confidence':'high','reason':'Checklist-notes wording.'}
    if 'rbt rpt' in low or 'rbt report' in low or 'robot report' in low:
        return {'role':'rbt_report','warranty':False,'confidence':'medium','reason':'RBT/robot report wording.'}
    if any(x in low for x in ['receiving pic','receiving picture','receiving photo','incoming pic','incoming picture','incoming photo']):
        return {'role':'receiving_photo','warranty':False,'confidence':'high','reason':'Incoming/receiving photo wording.'}
    if any(x in low for x in ['return shipment packaging','return shipping packaging','return packaging','shipment packaging','shipping packaging']):
        return {'role':'return_packaging_photo','warranty':False,'confidence':'high','reason':'Return/shipping packaging wording.'}
    if any(x in low for x in ['floppy','parameter','params','uploadparam','upload param','configuration','config backup']):
        return {'role':'configuration_evidence','warranty':False,'confidence':'high','reason':'Configuration/floppy/parameter wording.'}
    if ext in CONFIG_EXTS:
        return {'role':'parameter_file','warranty':False,'confidence':'medium','reason':'Configuration/firmware extension.'}
    if ext in IMAGE_EXTS:
        return {'role':'photo','warranty':False,'confidence':'medium','reason':'Image file; exact role uncertain.'}
    if ext in VIDEO_EXTS:
        return {'role':'movie','warranty':False,'confidence':'high','reason':'Video extension.'}
    if ext in SHEET_EXTS:
        return {'role':'structured_document','warranty':False,'confidence':'low','reason':'Spreadsheet file; exact role uncertain.'}
    if ext in DOC_EXTS or ext in PLAIN_TEXT_EXTS:
        return {'role':'document','warranty':False,'confidence':'low','reason':'Readable document; exact role uncertain.'}
    return {'role':'technical_file' if ext else 'unknown','warranty':False,'confidence':'low','reason':'No confirmed role rule matched.'}


def decode_bytes(raw):
    for enc in ['utf-8-sig','utf-8','cp1252','latin-1']:
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode('utf-8', errors='replace')


def run_command(args, timeout=180):
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        return p.returncode, decode_bytes(p.stdout), decode_bytes(p.stderr)
    except Exception as exc:
        return 999, '', str(exc)


def extract_docx_text(path):
    parts = []
    try:
        with zipfile.ZipFile(path) as z:
            names = ['word/document.xml'] + sorted(n for n in z.namelist() if n.startswith('word/header') or n.startswith('word/footer'))
            for name in names:
                if name not in z.namelist():
                    continue
                root = ET.fromstring(z.read(name))
                for elem in root.iter():
                    tag = elem.tag.rsplit('}',1)[-1]
                    if tag == 't' and elem.text:
                        parts.append(elem.text)
                    elif tag in ('tab','br'):
                        parts.append('\t' if tag=='tab' else '\n')
                    elif tag == 'p':
                        parts.append('\n')
        text = ''.join(parts)
        return text
    except Exception as exc:
        raise RuntimeError('DOCX extraction failed: {}'.format(exc))


def extract_xlsx_text(path):
    try:
        with zipfile.ZipFile(path) as z:
            shared = []
            if 'xl/sharedStrings.xml' in z.namelist():
                root = ET.fromstring(z.read('xl/sharedStrings.xml'))
                for si in root:
                    texts = [e.text or '' for e in si.iter() if e.tag.rsplit('}',1)[-1]=='t']
                    shared.append(''.join(texts))
            lines = []
            sheets = sorted(n for n in z.namelist() if re.fullmatch(r'xl/worksheets/sheet\d+\.xml', n))
            for sheet in sheets:
                lines.append('# {}'.format(Path(sheet).stem))
                root = ET.fromstring(z.read(sheet))
                current_row = []
                last_row = None
                for c in (e for e in root.iter() if e.tag.rsplit('}',1)[-1]=='c'):
                    ref = c.attrib.get('r','')
                    row_num_match = re.search(r'(\d+)$', ref)
                    row_num = int(row_num_match.group(1)) if row_num_match else last_row
                    if last_row is not None and row_num != last_row:
                        lines.append('\t'.join(current_row))
                        current_row = []
                    last_row = row_num
                    ctype = c.attrib.get('t')
                    value = ''
                    v = next((x for x in c if x.tag.rsplit('}',1)[-1]=='v'), None)
                    if ctype == 'inlineStr':
                        value = ''.join(x.text or '' for x in c.iter() if x.tag.rsplit('}',1)[-1]=='t')
                    elif v is not None and v.text is not None:
                        if ctype == 's':
                            try:
                                value = shared[int(v.text)]
                            except Exception:
                                value = v.text
                        else:
                            value = v.text
                    current_row.append(value)
                if current_row:
                    lines.append('\t'.join(current_row))
            return '\n'.join(lines)
    except Exception as exc:
        raise RuntimeError('XLSX extraction failed: {}'.format(exc))


def strip_rtf(text):
    text = re.sub(r'\\par[d]?', '\n', text)
    text = re.sub(r'\\[a-zA-Z]+-?\d*\s?', '', text)
    text = text.replace('{','').replace('}','')
    text = re.sub(r"\\'[0-9a-fA-F]{2}", '', text)
    return text


def extract_text(path, max_mb=25):
    path = Path(path)
    ext = path.suffix.lower()
    try:
        size = path.stat().st_size
    except OSError as exc:
        return {'status':'error','method':None,'text':'','warning':str(exc)}
    if size > max_mb*1024*1024:
        return {'status':'too_large','method':None,'text':'','warning':'File exceeds {} MB extraction limit.'.format(max_mb)}
    try:
        if ext in PLAIN_TEXT_EXTS:
            return {'status':'ok','method':'plain_text','text':decode_bytes(path.read_bytes()),'warning':None}
        if ext == '.rtf':
            return {'status':'ok','method':'rtf_basic','text':strip_rtf(decode_bytes(path.read_bytes())),'warning':'Basic RTF cleanup; verify formatting.'}
        if ext == '.pdf':
            if shutil.which('pdftotext'):
                code,out,err = run_command(['pdftotext','-layout',str(path),'-'])
                if code == 0 and out.strip():
                    return {'status':'ok','method':'pdftotext','text':out,'warning':None}
                if code == 0:
                    return {'status':'no_text_layer','method':'pdftotext','text':'','warning':'PDF may be scanned or image-only.'}
                return {'status':'error','method':'pdftotext','text':'','warning':err.strip() or 'pdftotext failed.'}
            try:
                from pypdf import PdfReader
                text = '\n'.join((p.extract_text() or '') for p in PdfReader(str(path)).pages)
                return {'status':'ok' if text.strip() else 'no_text_layer','method':'pypdf','text':text,'warning':None if text.strip() else 'PDF may be scanned.'}
            except Exception:
                return {'status':'dependency_missing','method':None,'text':'','warning':'Install poppler-utils for pdftotext.'}
        if ext == '.docx':
            return {'status':'ok','method':'docx_xml','text':extract_docx_text(path),'warning':None}
        if ext in ('.xlsx','.xlsm'):
            return {'status':'ok','method':'xlsx_xml','text':extract_xlsx_text(path),'warning':None}
        if ext == '.doc':
            if shutil.which('antiword'):
                code,out,err = run_command(['antiword',str(path)])
                return {'status':'ok' if code==0 and out.strip() else 'error','method':'antiword','text':out if code==0 else '','warning':None if code==0 else err.strip()}
            return {'status':'dependency_missing','method':None,'text':'','warning':'Legacy .doc requires antiword.'}
        if ext == '.xls':
            return {'status':'dependency_missing','method':None,'text':'','warning':'Legacy .xls extraction not configured in v1.4.'}
        if ext in IMAGE_EXTS:
            return {'status':'not_attempted_image','method':None,'text':'','warning':'Images are inventoried; Traveler Reader handles traveler OCR.'}
        if ext in VIDEO_EXTS:
            return {'status':'not_attempted_video','method':None,'text':'','warning':'Videos are inventoried but not transcribed in v1.4.'}
        return {'status':'unsupported','method':None,'text':'','warning':'No text extractor for {}'.format(ext or 'extensionless file')}
    except Exception as exc:
        return {'status':'error','method':None,'text':'','warning':str(exc)}


def extraction_preview(text, limit=1200):
    text = str(text or '').replace('\x00','')
    return text[:limit]


def write_extracted_text(base_dir, evidence_id, extraction):
    text = extraction.get('text','')
    if extraction.get('status') != 'ok' or not text.strip():
        return None
    out_dir = Path(base_dir)/'extracted_text'
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir/'{}.txt'.format(evidence_id)
    path.write_text(text, encoding='utf-8')
    return str(path)


def derived_role(path):
    low = path.name.lower()
    full = str(path).lower()
    if 'repair_entries' in low:
        return 'repair_entry_extraction'
    if 'vision_transcription' in low or 'vision_extraction' in full:
        return 'vision_transcription'
    if 'traveler_regions' in low:
        return 'region_ocr'
    if 'traveler_raw' in low:
        return 'whole_page_ocr'
    if 'anchor_detection_debug' in low:
        return 'segmentation_debug_image'
    if path.suffix.lower() in IMAGE_EXTS:
        return 'traveler_crop_image'
    if path.suffix.lower() == '.json':
        return 'derived_metadata'
    if path.suffix.lower() == '.txt':
        return 'derived_text'
    return 'derived_artifact'


def inventory_derived_artifacts(serial_folder_name, log_number, traveler_output_root, inventory_only=False):
    if not traveler_output_root:
        return []
    root = Path(traveler_output_root).expanduser().resolve()/safe_name(serial_folder_name)/str(log_number)
    if not root.exists() or not root.is_dir():
        return []
    rows = []
    for p in sorted(root.rglob('*')):
        if p.is_dir() or p.is_symlink():
            continue
        extraction = {'status':'inventory_only','method':None,'warning':None,'char_count':0,'text_preview':'','text_path':None}
        if not inventory_only and p.suffix.lower() in (PLAIN_TEXT_EXTS | {'.txt','.json','.md'}):
            ext = extract_text(p)
            extraction.update({
                'status':ext['status'],
                'method':ext.get('method'),
                'warning':ext.get('warning'),
                'char_count':len(ext.get('text','')),
                'text_preview':extraction_preview(ext.get('text','')),
            })
        rows.append({
            'artifact_id': path_id(p.relative_to(root)),
            'role': derived_role(p),
            'path': str(p),
            'relative_path': str(p.relative_to(root)),
            'extension': p.suffix.lower(),
            'size_bytes': p.stat().st_size,
            'extraction': extraction,
            'original_evidence': False,
        })
    return rows


def make_original_record(path, source_root, output_scope_dir, inventory_only=False, hash_files=False, max_text_mb=25):
    rel = path.relative_to(source_root)
    assignment = classify_assignment(rel)
    role = classify_role(path)
    stat = path.stat()
    evidence_id = path_id(rel)
    extraction = {'status':'inventory_only','method':None,'warning':None,'char_count':0,'text_preview':'','text_path':None}
    if not inventory_only:
        ext_result = extract_text(path, max_mb=max_text_mb)
        extraction = {
            'status': ext_result.get('status'),
            'method': ext_result.get('method'),
            'warning': ext_result.get('warning'),
            'char_count': len(ext_result.get('text','')),
            'text_preview': extraction_preview(ext_result.get('text','')),
            'text_path': None,
        }
        text_path = write_extracted_text(output_scope_dir, evidence_id, ext_result)
        extraction['text_path'] = text_path
    mime_type = mimetypes.guess_type(path.name)[0]
    return {
        'evidence_id': evidence_id,
        'source_path': str(path),
        'relative_path': str(rel),
        'filename': path.name,
        'extension': path.suffix.lower(),
        'mime_type': mime_type,
        'size_bytes': stat.st_size,
        'modified_time_utc': datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        'content_sha256': sha256_file(path) if hash_files else None,
        'assignment_scope': assignment['scope'],
        'assignment_reason': assignment['reason'],
        'log_number': assignment['log_number'],
        'role': role['role'],
        'role_confidence': role['confidence'],
        'role_reason': role['reason'],
        'authority': ROLE_AUTHORITY.get(role['role'],'unclassified_evidence'),
        'warranty': bool(role.get('warranty')),
        'extraction': extraction,
        'original_evidence': True,
    }


def evidence_completeness(event):
    roles = Counter(x['role'] for x in event['evidence_files'])
    score = 0
    reasons = []
    if roles['traveler']:
        score += 3
        reasons.append('Traveler present.')
    else:
        reasons.append('Traveler missing.')
    if roles['failure_analysis_report']:
        score += 2
        reasons.append('Failure-analysis evidence present.')
    if roles['robot_test_report'] or roles['robot_checklist']:
        score += 2
        reasons.append('Test/checklist evidence present.')
    if roles['receiving_photo']:
        score += 1
        reasons.append('Incoming-condition photos present.')
    if event.get('derived_traveler_artifacts'):
        score += 1
        reasons.append('Traveler Reader artifacts available.')
    if score >= 6:
        rating = 'high'
    elif score >= 3:
        rating = 'medium'
    else:
        rating = 'low'
    return {'rating':rating,'score':score,'reasons':reasons}


def bundle_gaps(event):
    roles = Counter(x['role'] for x in event['evidence_files'])
    gaps = []
    if not roles['traveler']:
        gaps.append('No traveler identified for this repair event.')
    if not (roles['robot_test_report'] or roles['robot_checklist'] or roles['rbt_report']):
        gaps.append('No supporting test/checklist/RBT report identified.')
    if not roles['failure_analysis_report']:
        gaps.append('No failure-analysis report identified.')
    if not event.get('derived_traveler_artifacts'):
        gaps.append('No local Traveler Reader artifact identified.')
    return gaps


def collect_evidence(serial_folder, output_root, refs, traveler_output_root=None, inventory_only=False, hash_files=False, max_text_mb=25):
    source = Path(serial_folder).expanduser().resolve()
    if not source.exists() or not source.is_dir():
        raise ValueError('Serial folder does not exist: {}'.format(source))
    output_root = Path(output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    meta = parse_serial_folder_name(source.name, refs)

    events = {}
    unit_files = []
    unresolved_files = []
    original_file_count = 0

    # Meaningful top-level unprefixed directories are retained as unit-level items.
    unit_directories = []
    for child in sorted(source.iterdir(), key=lambda p:p.name.lower()):
        if child.is_dir() and not child.is_symlink() and not collect_log_candidates(Path(child.name)):
            unit_directories.append({
                'item_type':'directory',
                'source_path':str(child),
                'relative_path':str(child.relative_to(source)),
                'role':'unit_configuration_archive' if any(x in child.name.lower() for x in ['floppy','parameter','config']) else 'unit_level_folder',
                'assignment_scope':'unit_level',
                'original_evidence':True,
            })

    for path in sorted(source.rglob('*')):
        if path.is_dir() or path.is_symlink():
            continue
        original_file_count += 1
        assignment = classify_assignment(path.relative_to(source))
        if assignment['scope'] == 'repair_event':
            log = assignment['log_number']
            event_dir = output_root/'events'/log
            record = make_original_record(path, source, event_dir, inventory_only, hash_files, max_text_mb)
            if log not in events:
                events[log] = {
                    **decode_log_number(log),
                    'warranty':False,
                    'missing_traveler':True,
                    'evidence_files':[],
                    'derived_traveler_artifacts':[],
                }
            events[log]['evidence_files'].append(record)
            if record['role'] == 'traveler':
                events[log]['missing_traveler'] = False
            if record['warranty']:
                events[log]['warranty'] = True
        elif assignment['scope'] == 'unit_level':
            unit_dir = output_root/'unit_level'
            unit_files.append(make_original_record(path, source, unit_dir, inventory_only, hash_files, max_text_mb))
        else:
            unresolved_dir = output_root/'unresolved'
            unresolved_files.append(make_original_record(path, source, unresolved_dir, inventory_only, hash_files, max_text_mb))

    event_list = []
    for log,event in events.items():
        event['evidence_files'].sort(key=lambda x:x['relative_path'].lower())
        event['derived_traveler_artifacts'] = inventory_derived_artifacts(
            source.name, log, traveler_output_root, inventory_only=inventory_only
        )
        role_counts = Counter(x['role'] for x in event['evidence_files'])
        extraction_counts = Counter(x['extraction']['status'] for x in event['evidence_files'])
        event['evidence_counts_by_role'] = dict(sorted(role_counts.items()))
        event['extraction_counts'] = dict(sorted(extraction_counts.items()))
        event['evidence_completeness'] = evidence_completeness(event)
        event['evidence_gaps'] = bundle_gaps(event)
        event['accepted_as_repair_summary'] = False
        event['collector_note'] = 'Evidence collected only; no final repair conclusion generated.'
        event_list.append(event)

    event_list.sort(key=lambda e:(e.get('repair_date') or '9999-99-99', e.get('daily_sequence') or '999', e.get('log_number') or ''))

    assigned_event_files = sum(len(e['evidence_files']) for e in event_list)
    unit_level_items = unit_directories + unit_files
    accounted = assigned_event_files + len(unit_files) + len(unresolved_files)

    summary = {
        'collector_version':VERSION,
        'collected_at_utc':now_utc(),
        'source_serial_folder':str(source),
        'serial_metadata':meta,
        'inventory_only':bool(inventory_only),
        'repair_event_count':len(event_list),
        'original_source_file_count':original_file_count,
        'event_assigned_file_count':assigned_event_files,
        'unit_level_file_count':len(unit_files),
        'unit_level_directory_count':len(unit_directories),
        'unit_level_item_count':len(unit_level_items),
        'unresolved_file_count':len(unresolved_files),
        'accounted_original_file_count':accounted,
        'unaccounted_original_file_count':original_file_count-accounted,
        'warranty_event_count':sum(1 for e in event_list if e['warranty']),
        'missing_traveler_event_count':sum(1 for e in event_list if e['missing_traveler']),
        'first_repair_date':min((e['repair_date'] for e in event_list if e.get('repair_date')), default=None),
        'most_recent_repair_date':max((e['repair_date'] for e in event_list if e.get('repair_date')), default=None),
        'traveler_output_root':str(Path(traveler_output_root).resolve()) if traveler_output_root else None,
        'repair_events':event_list,
        'unit_level_evidence':unit_level_items,
        'unresolved_evidence':unresolved_files,
    }
    return summary


def render_event_text(serial_meta, event):
    lines = [
        'NOVA DRL REPAIR EVIDENCE COLLECTOR v{}'.format(VERSION),
        '='*78,
        'REPAIR EVENT {}'.format(event['log_number']),
        'Date: {}'.format(event.get('repair_date_display') or event.get('repair_date') or 'Unknown'),
        'Sequence: {}'.format(event.get('daily_sequence') or 'Unknown'),
        'Model: {}'.format(serial_meta.get('model') or 'Unknown'),
        'Serial: {}'.format(serial_meta.get('serial_number') or 'Unknown'),
        'Customer: {}'.format(serial_meta.get('customer') or 'Unknown'),
        'Warranty event: {}'.format('YES' if event['warranty'] else 'NO'),
        'Traveler missing: {}'.format('YES' if event['missing_traveler'] else 'NO'),
        'Evidence completeness: {} ({})'.format(event['evidence_completeness']['rating'], event['evidence_completeness']['score']),
        '',
        'ORIGINAL EVIDENCE',
    ]
    for rec in event['evidence_files']:
        lines += [
            '',
            '[{}] {}'.format(rec['role'], rec['relative_path']),
            '  Authority: {}'.format(rec['authority']),
            '  Classification: {} ({})'.format(rec['role_confidence'], rec['role_reason']),
            '  Extraction: {}{}'.format(rec['extraction']['status'], ' via '+rec['extraction']['method'] if rec['extraction'].get('method') else ''),
            '  Extracted text: {}'.format(rec['extraction'].get('text_path') or 'None'),
        ]
        if rec['extraction'].get('text_preview'):
            lines += ['  Preview:', textwrap_indent(rec['extraction']['text_preview'], '    ')]
    lines += ['', 'DERIVED TRAVELER READER ARTIFACTS']
    if event['derived_traveler_artifacts']:
        for art in event['derived_traveler_artifacts']:
            lines.append('  [{}] {}'.format(art['role'], art['path']))
    else:
        lines.append('  None')
    lines += ['', 'EVIDENCE GAPS']
    if event['evidence_gaps']:
        for gap in event['evidence_gaps']:
            lines.append('  - {}'.format(gap))
    else:
        lines.append('  None identified')
    lines += ['', 'STATUS', '  Evidence collected only.', '  Accepted as final repair summary: NO', '  No Qdrant entry created.']
    return '\n'.join(lines)+'\n'


def textwrap_indent(text, prefix):
    return '\n'.join(prefix+line for line in str(text).splitlines())


def write_outputs(summary, output_root):
    out = Path(output_root)
    out.mkdir(parents=True, exist_ok=True)
    meta = summary['serial_metadata']

    for event in summary['repair_events']:
        event_dir = out/'events'/event['log_number']
        event_dir.mkdir(parents=True, exist_ok=True)
        bundle = {
            'collector_version':VERSION,
            'scope':'repair_event',
            'serial_metadata':meta,
            'repair_event':event,
        }
        (event_dir/'repair_evidence_bundle.json').write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding='utf-8')
        (event_dir/'repair_evidence_bundle.txt').write_text(render_event_text(meta,event), encoding='utf-8')

    unit_dir = out/'unit_level'
    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir/'unit_evidence_bundle.json').write_text(json.dumps({
        'collector_version':VERSION,
        'scope':'unit_level',
        'serial_metadata':meta,
        'evidence':summary['unit_level_evidence'],
    }, indent=2, ensure_ascii=False), encoding='utf-8')

    unresolved_dir = out/'unresolved'
    unresolved_dir.mkdir(parents=True, exist_ok=True)
    (unresolved_dir/'unresolved_evidence.json').write_text(json.dumps({
        'collector_version':VERSION,
        'scope':'unresolved',
        'serial_metadata':meta,
        'evidence':summary['unresolved_evidence'],
    }, indent=2, ensure_ascii=False), encoding='utf-8')

    # CSV index of original source files only.
    index_rows = []
    for event in summary['repair_events']:
        for rec in event['evidence_files']:
            index_rows.append(rec)
    for rec in summary['unit_level_evidence']:
        if rec.get('item_type') == 'directory':
            continue
        index_rows.append(rec)
    index_rows.extend(summary['unresolved_evidence'])
    fields = [
        'evidence_id','assignment_scope','log_number','role','authority','warranty',
        'relative_path','source_path','extension','mime_type','size_bytes',
        'role_confidence','extraction_status','extraction_method','extracted_text_path'
    ]
    with (out/'evidence_index.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for rec in index_rows:
            w.writerow({
                'evidence_id':rec.get('evidence_id'),
                'assignment_scope':rec.get('assignment_scope'),
                'log_number':rec.get('log_number'),
                'role':rec.get('role'),
                'authority':rec.get('authority'),
                'warranty':rec.get('warranty'),
                'relative_path':rec.get('relative_path'),
                'source_path':rec.get('source_path'),
                'extension':rec.get('extension'),
                'mime_type':rec.get('mime_type'),
                'size_bytes':rec.get('size_bytes'),
                'role_confidence':rec.get('role_confidence'),
                'extraction_status':rec.get('extraction',{}).get('status'),
                'extraction_method':rec.get('extraction',{}).get('method'),
                'extracted_text_path':rec.get('extraction',{}).get('text_path'),
            })

    (out/'serial_evidence_summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
    lines = [
        'NOVA DRL REPAIR EVIDENCE COLLECTOR v{}'.format(VERSION),
        '='*78,
        'Source: {}'.format(summary['source_serial_folder']),
        'Model: {}'.format(meta.get('model') or 'Unknown'),
        'Serial: {}'.format(meta.get('serial_number') or 'Unknown'),
        'Customer: {}'.format(meta.get('customer') or 'Unknown'),
        '',
        'Repair events: {}'.format(summary['repair_event_count']),
        'Original source files: {}'.format(summary['original_source_file_count']),
        'Event-assigned files: {}'.format(summary['event_assigned_file_count']),
        'Unit-level files: {}'.format(summary['unit_level_file_count']),
        'Unit-level directories: {}'.format(summary['unit_level_directory_count']),
        'Unit-level items: {}'.format(summary['unit_level_item_count']),
        'Unresolved files: {}'.format(summary['unresolved_file_count']),
        'Unaccounted files: {}'.format(summary['unaccounted_original_file_count']),
        'Warranty events: {}'.format(summary['warranty_event_count']),
        'Events missing traveler: {}'.format(summary['missing_traveler_event_count']),
        'First repair: {}'.format(summary['first_repair_date'] or 'Unknown'),
        'Most recent repair: {}'.format(summary['most_recent_repair_date'] or 'Unknown'),
        'Inventory only: {}'.format('YES' if summary['inventory_only'] else 'NO'),
        '',
        'REPAIR EVENTS',
    ]
    for event in summary['repair_events']:
        lines.append('{}  {}  files={}  traveler={}  warranty={}  completeness={}'.format(
            event['repair_date_display'] or 'Invalid', event['log_number'], len(event['evidence_files']),
            'NO' if event['missing_traveler'] else 'YES', 'YES' if event['warranty'] else 'NO',
            event['evidence_completeness']['rating']))
    lines += ['', 'FILE ACCOUNTING', '  Every original source file must be assigned to an event, unit level, or unresolved.',
              '  Status: {}'.format('OK' if summary['unaccounted_original_file_count']==0 else 'REVIEW REQUIRED')]
    (out/'serial_evidence_summary.txt').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return out


def validate_expectations(summary, args):
    checks = []
    mapping = [
        ('repair events', args.expect_events, summary['repair_event_count']),
        ('source files', args.expect_files, summary['original_source_file_count']),
        ('warranty events', args.expect_warranty_events, summary['warranty_event_count']),
        ('missing-traveler events', args.expect_missing_travelers, summary['missing_traveler_event_count']),
        ('unit-level items', args.expect_unit_items, summary['unit_level_item_count']),
    ]
    for label, expected, actual in mapping:
        if expected is not None:
            checks.append({'label':label,'expected':expected,'actual':actual,'pass':expected==actual})
    return checks


def main():
    ap = argparse.ArgumentParser(description='Nova DRL Repair Evidence Collector v1.4')
    ap.add_argument('serial_folder')
    ap.add_argument('--output-root')
    ap.add_argument('--traveler-output-root', default='/opt/nova-drl/output/traveler_reader_v1_3_1')
    ap.add_argument('--config')
    ap.add_argument('--inventory-only', action='store_true')
    ap.add_argument('--hash', action='store_true')
    ap.add_argument('--max-text-mb', type=int, default=25)
    ap.add_argument('--expect-events', type=int)
    ap.add_argument('--expect-files', type=int)
    ap.add_argument('--expect-warranty-events', type=int)
    ap.add_argument('--expect-missing-travelers', type=int)
    ap.add_argument('--expect-unit-items', type=int)
    args = ap.parse_args()

    source = Path(args.serial_folder).expanduser().resolve()
    if not source.exists() or not source.is_dir():
        print('ERROR: Serial folder not found: {}'.format(source), file=sys.stderr)
        return 2
    config_dir = Path(args.config).resolve() if args.config else Path(__file__).resolve().parent.parent/'config'
    refs = load_reference_config(config_dir)
    safe = safe_name(source.name)
    out = Path(args.output_root).resolve() if args.output_root else Path.cwd()/'output'/'repair_evidence_collector_v1_4'/safe

    try:
        summary = collect_evidence(
            source, out, refs,
            traveler_output_root=args.traveler_output_root,
            inventory_only=args.inventory_only,
            hash_files=args.hash,
            max_text_mb=args.max_text_mb,
        )
        checks = validate_expectations(summary,args)
        summary['expectation_checks'] = checks
        write_outputs(summary,out)
    except Exception as exc:
        print('ERROR: {}'.format(exc), file=sys.stderr)
        return 2

    print('\nNova DRL Repair Evidence Collector v{}'.format(VERSION))
    print('='*62)
    print('Model:                    {}'.format(summary['serial_metadata'].get('model')))
    print('Serial:                   {}'.format(summary['serial_metadata'].get('serial_number')))
    print('Repair events:            {}'.format(summary['repair_event_count']))
    print('Original source files:    {}'.format(summary['original_source_file_count']))
    print('Event-assigned files:     {}'.format(summary['event_assigned_file_count']))
    print('Unit-level items:         {}'.format(summary['unit_level_item_count']))
    print('Unresolved files:         {}'.format(summary['unresolved_file_count']))
    print('Unaccounted files:        {}'.format(summary['unaccounted_original_file_count']))
    print('Warranty events:          {}'.format(summary['warranty_event_count']))
    print('Events missing traveler:  {}'.format(summary['missing_traveler_event_count']))
    print('Inventory only:           {}'.format('YES' if summary['inventory_only'] else 'NO'))
    if checks:
        print('\nEXPECTED PILOT COUNTS')
        for check in checks:
            print('  {:28} expected={} actual={} {}'.format(check['label']+':',check['expected'],check['actual'],'PASS' if check['pass'] else 'FAIL'))
    print('\nReports: {}'.format(out))
    print('READ-ONLY COMPLETE: No DRL source files were changed.')
    print('NO QDRANT ENTRY CREATED.')
    if summary['unaccounted_original_file_count'] != 0 or any(not x['pass'] for x in checks):
        return 4
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
