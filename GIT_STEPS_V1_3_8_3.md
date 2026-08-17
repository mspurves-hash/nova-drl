# Nova DRL v1.3.8.3 — Git / Install Steps

1. Extract this FLAT ZIP into the Windows Nova-DRL Git working directory.
2. Commit and push with GitHub Desktop.
3. On the Ubuntu Nova server:

```bash
cd /opt/nova-drl && git pull
```

4. Run the v1.3.8.3 deterministic tests:

```bash
python3 tests/test_gb8_technician_answer_composer_v1_3_8_3.py
```

Expected:

```text
PASS: Nova DRL GB8 Technician Answer Composer v1.3.8.3 tests
```

5. Verify frozen sources only (no network/model calls):

```bash
python3 analysis/nova_gb8_technician_answer_composer_v1_3_8_3.py --self-check
```

6. Verify live Qdrant/Ollama/composer-model status:

```bash
python3 analysis/nova_gb8_technician_answer_composer_v1_3_8_3.py --status
```

7. First live answer:

```bash
python3 analysis/nova_gb8_technician_answer_composer_v1_3_8_3.py "Y axis drifting"
```

8. Interactive mode:

```bash
python3 analysis/nova_gb8_technician_answer_composer_v1_3_8_3.py --interactive
```

Inside interactive mode, type only the question, for example:

```text
nova> Y axis drifting
```

Do not type the full `python3 ...` shell command at the `nova>` prompt.
