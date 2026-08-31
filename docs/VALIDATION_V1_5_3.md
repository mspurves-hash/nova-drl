# Validation — v1.5.3

Synthetic regression validates:
- full-corpus source loading;
- full file-index loading;
- product/model partial lookup (`1526990`);
- RMA lookup;
- first-class Customer PO lookup;
- Mouser order lookup (`MSR56889`);
- manufacturer PN lookup;
- product-specific part aggregation;
- procurement-only order token excluded from manufacturer-PN product knowledge;
- atomic SQLite build path.

No OCR, vision, LLM, Qdrant, or NAS scan is required for this build.
