# Submission memory (pinned)

`<key>.json` files: the intake profile and red-team suite remembered for a set of documents,
keyed by SHA-256 over the files' SHA-256s (see `agents/memory.py`). Re-uploading the same
documents on the web app, from any device, reuses this profile and reruns exactly these tests.

Pin a memory downloaded from the web app: `python -m agents.memory pin memory-<key>.json`, then commit.
