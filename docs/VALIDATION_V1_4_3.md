# Validation v1.4.3

## Synthetic tests
`tests/test_rcl1a_indexed_focused_recovery_v1_4_3.py` validates:

- runtime contains no prior hosted benchmark answer anchors,
- Everything-style index token matching can span folder + filename,
- `.picasaoriginals` backup exclusion,
- combined `All Line Cards.pdf` exclusion,
- unrelated `LINE` document exclusion,
- individual Line Card image/PDF selection,
- real share path and detected log are preserved,
- source/index share-root binding is enforced,
- extraction quote binding and quantity policy,
- same-log multi-source repair-event grouping,
- frequency counting by repair event rather than image,
- index-first synthetic end-to-end analysis,
- no Qdrant execution path.

All tests pass in the packaged build.

## Live DRL index snapshot used only for expected validation
The v1.4.2 live search supplied on 2026-08-18 returned 173 raw `RCL1A LINE` matches. Applying the v1.4.3 selector rules to that listing yields an expected 161 source documents: 160 JPG images and one individual Line Card PDF. The exclusions are 10 `.picasaoriginals` backup images, one unrelated message file, and the manually combined benchmark PDF.

Among those selected documents, the supplied index listing shows 160 documents with detected 9-digit logs representing 159 distinct logged repair events, plus one legacy filename without a detected 9-digit log. These numbers are **not embedded as runtime acceptance criteria**; `--status` and `--plan-only` calculate the current live values from the index.
