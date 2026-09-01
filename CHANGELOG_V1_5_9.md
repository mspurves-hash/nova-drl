# Changelog — v1.5.9

## 80/20 Reference-PN Resolver
- Keeps v1.5.8 explicit structured-event replacement counting.
- Replaces overly-short numeric display labels with corpus-supported complete reference PNs when recurrence supports the longer identity.
- Allows strongly dominant stable numeric cores to remain the technician reference when longer variants are sparse.
- Adds product-local OCR/punctuation/prefix/suffix variant clustering.
- Preserves every raw observed variant under the reference PN.
- Suppresses text-only numeric OCR fragments unless supported by an observed raw PN in the same product.
- Keeps one-off part noise out of the normal 80/20 product view.
- Renames the technician column from PART NUMBER to REFERENCE PN.
- No expert PN mapping table, no standard-kit override, no re-ingestion, no AI call.
- Production launcher now points to v1.5.9; Windows Engineer Client remains on the stable `/usr/local/bin/nova-drl` endpoint.
