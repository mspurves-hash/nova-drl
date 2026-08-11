#!/usr/bin/env python3
import importlib.util, tempfile, time
from pathlib import Path
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
TARGET=ROOT/'ingest'/'nova_testing_final_result_fusion_v1_5_5_2.py'
spec=importlib.util.spec_from_file_location('m1552',str(TARGET)); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
assert m.VERSION=='1.5.5.2'
rules=m.load_rules(ROOT/'config'/'testing_final_result_rules_v1_5_5_2.json')
source_data={
 'fusion_version':'1.5.4',
 'repair_identity':{'log_number':'130813004','equipment_type':'RBT','oem':'GENMARK','model':'GB8-MT','serial_number':'80010732','customer':'UTI MICRON'},
 'approved_fields':{
   'customer_complaint':{'value':'Y Axis needs to be fixed'},
   'repair_actions':[{'value':'Adjusted Y-FE from around 9000 down to around 3000 by slipping Y belt a few teeth'},{'value':'Added Flanges BERS x2 to A1 + A2 upper link'}],
   'parts_replaced':[{'part':'flanged bearings','quantity':2}]
 },
 'approved_field_count':4,'approved_repair_action_count':2,'approved_parts_replaced_count':1,'qdrant_entry_created':False
}
analyses=[
 {'analysis_id':'report2','vision_status':'ok','source':{'source_kind':'supporting_document_page','document_role':'robot_test_report','document_family':'DRL_ACCEPTANCE_TEST_REPORT','source_document':'130813004 Robot Test Report.PDF','source_path':'/mnt/drl/report.pdf','page_number':2,'image_path':'/tmp/report2.png'},'parsed_analysis':{
   'testing_items':[
     {'step_label':'Check for proper placement of vacuum filters','event_mark':'X','mark_type':'x_mark','result':'fail','semantic_role':'inspection','association_basis':'same_row','selected_result':None,'confidence':'high'},
     {'step_label':'Check if alignment and play is correct for arms.','event_mark':'X','mark_type':'x_mark','result':'fail','semantic_role':'inspection','association_basis':'same_row','selected_result':None,'confidence':'high'},
     {'step_label':'Pass/Fail','event_mark':'X','mark_type':'x_mark','result':'fail','semantic_role':'test','association_basis':'unknown','selected_result':None,'confidence':'high'},
     {'step_label':'Move the robot into the test area and hook up the cables to the controller','event_mark':'checkmark','mark_type':'checkmark','result':'completed','semantic_role':'setup','association_basis':'same_row','selected_result':None,'confidence':'high'}
   ],
   'final_result_items':[
     {'value':'Pass','basis_label':'Acceptance Test Report','event_mark':'checkmark','result':'pass','semantic_role':'final_result_field','association_basis':'selected_option','selected_result':'pass','confidence':'high'},
     {'value':'Pass/Fail','basis_label':'Pass/Fail mark','event_mark':'pass_fail_mark','result':'pass','semantic_role':'final_result_field','association_basis':'unknown','selected_result':None,'confidence':'high'}
   ],'other_event_observations':[],'printed_template_only_labels':[],'uncertain_marks':[]}},
 {'analysis_id':'finaltest','vision_status':'ok','source':{'source_kind':'traveler_event_crop','document_role':'traveler','document_family':'DRL_TRAVELER','source_document':'final_test.png','source_path':'/derived/final_test.png','page_number':None,'image_path':'/derived/final_test.png'},'parsed_analysis':{
   'testing_items':[],
   'final_result_items':[
     {'value':'No Trouble Found','basis_label':'Final Unit Test Results and Notes','event_mark':'checkmark','mark_type':'checkmark','result':'pass','semantic_role':'final_disposition','association_basis':'selected_option','selected_result':None,'confidence':'high'},
     {'value':'Passed All Tests','basis_label':'Final Unit Test Results and Notes','event_mark':'checkmark','mark_type':'checkmark','result':'pass','semantic_role':'final_disposition','association_basis':'selected_option','selected_result':None,'confidence':'high'},
     {'value':'Untestable, Inspection Only','basis_label':'Final Unit Test Results and Notes','event_mark':'4 Hours','mark_type':'handwritten_value','result':'other','semantic_role':'final_disposition','association_basis':'adjacent_label','selected_result':None,'confidence':'high'}
   ],
   'other_event_observations':[{'label':'Ttl Time Spent (Hours)','value':'4','category':'administrative','confidence':'high'}],
   'printed_template_only_labels':[],'uncertain_marks':[]}},
 {'analysis_id':'ship','vision_status':'ok','source':{'source_kind':'traveler_event_crop','document_role':'traveler','document_family':'DRL_TRAVELER','source_document':'shipping_final_ok.png','source_path':'/derived/shipping_final_ok.png','page_number':None,'image_path':'/derived/shipping_final_ok.png'},'parsed_analysis':{
   'testing_items':[],
   'final_result_items':[{'value':'Final O.K.','basis_label':'Final O.K.','event_mark':'G9123/13','mark_type':'handwritten_value','result':'pass','semantic_role':'final_disposition','association_basis':'adjacent_label','selected_result':None,'confidence':'high'}],
   'other_event_observations':[],'printed_template_only_labels':[],'uncertain_marks':[]}}
]
review=m.build_review(analyses,rules,[],source_data)
# X/check on normal checklist-style steps must become completed, not fail.
assert review['testing']['candidate_count']==2
for row in review['testing']['candidates']:
    assert row['result']=='completed'
    assert row['raw_model_result']=='fail'
    assert row['semantic_correction']=='x_or_checkmark_on_checklist_step_means_completed_not_pass_fail'
# Setup step routed away; ambiguous pass/fail test rejected.
assert any(x['label'].startswith('Move the robot into the test area') for x in review['hardening']['routed_observations'])
assert any(x['reason']=='pass_fail_result_not_unambiguously_selected' for x in review['hardening']['testing_rejections'])
# Document title and generic Pass/Fail are not final results.
reasons={x['reason'] for x in review['hardening']['final_rejections']}
assert 'document_title_used_as_result_basis' in reasons
assert 'unresolved_pass_fail_choice' in reasons
assert 'traveler_disposition_mark_looks_like_unrelated_numeric_field' in reasons
# Known traveler fields remain reviewable with canonical semantics.
vals={x['value']:x for x in review['final_result']['candidates']}
assert vals['No Trouble Found']['result']=='no_trouble_found'
assert vals['Passed All Tests']['result']=='pass'
assert vals['Final O.K.']['result']=='final_ok'
assert 'Untestable, Inspection Only' not in vals
# NTF and Passed All Tests conflict with each other; NTF also conflicts with repairs/parts.
assert 'conflicts_with_approved_repair_actions' in vals['No Trouble Found']['conflict_flags']
assert 'conflicts_with_approved_parts_replaced' in vals['No Trouble Found']['conflict_flags']
assert 'mutually_exclusive_final_options_detected_same_source' in vals['No Trouble Found']['conflict_flags']
assert 'mutually_exclusive_final_options_detected_same_source' in vals['Passed All Tests']['conflict_flags']
# Cache: exact second run reuses, changed image invalidates.
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp); img=root/'p.png'; Image.new('RGB',(20,20),'white').save(img)
    source={'source_kind':'supporting_document_page','document_role':'robot_checklist','document_family':'DRL_INTERNAL_CHECKLIST','source_document':'x.pdf','source_path':'/mnt/drl/x.pdf','page_number':1,'image_path':str(img),'template_ocr_text':''}
    a=m.analyze_sources([source],root/'out','minicpm-v:latest',10,2200,refresh=False,no_vision=True); assert a[0]['cache_status']=='created'
    b=m.analyze_sources([source],root/'out','minicpm-v:latest',10,2200,refresh=False,no_vision=True); assert b[0]['cache_status']=='reused'
    time.sleep(0.01); Image.new('RGB',(21,20),'white').save(img)
    c=m.analyze_sources([source],root/'out','minicpm-v:latest',10,2200,refresh=False,no_vision=True); assert c[0]['cache_status']=='invalidated'
print('PASS: Nova DRL Testing / Final Result Fusion v1.5.5.2 tests')
