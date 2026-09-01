# Nova DRL Global Resolver Consolidation v1.5.10

## Hard rule
When an underlying defect is generic, the fix is global. Product-specific resolver patches are forbidden unless Matt explicitly identifies a true product-specific exception.

## Product identity
The resolver evaluates every observed product/model token as a possible base and selects the recurring base that explains the greatest number of repair events through generic suffix/punctuation continuation. Exact suffix strings remain metadata. Pure numeric extensions with no delimiter are not automatically merged.

## Component reference PN
Reference-PN clustering uses only observed corpus evidence: normalized punctuation, digit groups, alphabetic structure, edit similarity, recurrence, and distinct repair-event unions. No part-number mapping table is used.

## Regression gate
The v1.5.10 tests exercise multiple unrelated product suffix styles and component variants and inspect the current production resolver source to prevent known product-specific patch literals from entering resolver code.

No re-ingestion, AI call, NAS scan, or knowledge-index rebuild is required.
