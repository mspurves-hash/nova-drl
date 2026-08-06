# Nova DRL

Nova DRL is a private engineering knowledge system for Direct Repair Labs.

Its purpose is to preserve and make accessible decades of repair history, technical notes, engineering data, test procedures, schematics, photos, movies, parameter files, and parts history.

## Guiding Principles

1. Preserve the original DRL archive.
2. Never modify source documents during ingestion.
3. Every answer must cite its DRL evidence.
4. Distinguish repair history from engineering knowledge.
5. Report uncertainty rather than inventing an answer.
6. Every completed repair should make Nova more useful.

## Initial Pilot

- Type: RBT
- OEM: Genmark
- Model: GB8 / GB8-MT

## Core Knowledge Sources

### Traveler Database
Records what happened during actual repairs.

### Operations Check List
Records what DRL knows about a model, using the hierarchy `Type / OEM / Model`.

## Current Infrastructure

- Ubuntu
- Docker
- Ollama
- Qwen 2.5 32B
- Qdrant
- Git
