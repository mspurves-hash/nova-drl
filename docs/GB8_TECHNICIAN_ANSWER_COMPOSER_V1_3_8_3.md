# Nova DRL — GB8 Technician Answer Composer v1.3.8.3

## Purpose

Turn the evidence selected by v1.3.8.2 Hybrid Technician Search into a short, readable technician answer.

This version does **not** allow the generative model to decide what evidence exists. Retrieval remains owned by v1.3.8.2, which combines the existing Qdrant semantic index and deterministic Python search with RRF.

## Flow

```text
Technician question
    ↓
v1.3.8.2 Hybrid Technician Search
    ↓
Top recurring groups + Traveler evidence
    ↓
Qwen2.5 14B Q6 answer composition
    ↓
Python group-ID validation
    ↓
Readable provisional technician answer
```

## Model role

Default composer model:

```text
qwen25-drl:14b-q6-16k
```

The 14B model may:

- summarize supplied historical patterns,
- explain relationships already visible in retrieved evidence,
- suggest historically motivated first checks,
- mention uncertainty when evidence is mixed.

It may **not**:

- search Qdrant or raw Travelers itself,
- invent evidence,
- alter recurrence counts,
- introduce an un-retrieved recurring-group ID,
- approve a fact,
- write to Qdrant,
- convert provisional history into an approved SOP/BOM.

## Source-ID validation

Every model finding/check must return `support_group_ids`.

Python removes any support ID that is not in the current hybrid retrieval set. An item with no valid support IDs is dropped entirely.

## Failure behavior

The model uses JSON mode and may retry once after malformed JSON. If no usable supported answer survives validation, Python returns a deterministic summary of the hybrid results instead of failing the technician query.

## Output sections

- `ANSWER`
- `HISTORICAL FINDINGS`
- `SUGGESTED FIRST CHECKS — HISTORICALLY MOTIVATED`
- `CAUTION`
- `REPRESENTATIVE TRAVELER EVIDENCE`
- `RETRIEVAL / COMPOSITION POLICY`

Use `--show-retrieval` when the technician or developer wants the full v1.3.8.2 hybrid ranking appended beneath the composed answer.

## Non-search modes

Serial-number, log-number, stocking, and service-area queries continue to delegate directly to the deterministic v1.3.8.0 behavior. They do not need a generative model to answer.

## Frozen policy

- Original Travelers: unchanged.
- v1.3.6.1 evidence: unchanged.
- v1.3.7.3 recurring groups: authoritative for this layer.
- v1.3.8.1 Qdrant collection: read-only/disposable search index.
- v1.3.8.2 hybrid retrieval: unchanged evidence selector.
- Accepted facts: 0.
- Qdrant writes from v1.3.8.3: 0.
- Operating philosophy: FAST PROVISIONAL 80/20.
