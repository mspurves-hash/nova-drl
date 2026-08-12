# Validation — Whole Traveler Evidence Reader v1.3.5.0

Initial validation target: GB8-MT serial 80010732, log 150622005.

## First checkpoint: detect-only

The reader must:

- resolve the original `150622005 Line Card Original` source;
- create one complete whole-page evidence image;
- use no crops/relevance boxes;
- perform no vision in detect-only mode;
- accept zero repair facts;
- modify no DRL source files;
- create zero Qdrant entries.

## Second checkpoint: transcription

After human confirmation that the whole page is intact, run without `--detect-only` and review the raw whole-page transcription. Machine text remains evidence only.

## Regression intent

After 150622005 is satisfactory, run the same whole-page reader on 130813004 and 130130006. Existing v1.3.4.4.3/v1.5.x human-approved records remain frozen and are not rewritten by this experiment.
