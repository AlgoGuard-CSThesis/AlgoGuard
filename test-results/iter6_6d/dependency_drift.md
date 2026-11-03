# Pilot finding 10: dependency drift on a fresh install (Stage 6D.1 / 6D.3)

On 2026-11-04 the first clean install of the analyst package (build `0.6.0+03ed96f`, before
`requirements.txt` was pinned) resolved the `>=` ranges against the package index. Five of the
fifteen non-model runtime packages came out newer than the environment every check had run with:

| Package | Tested environment | Fresh install with `>=` |
|---|---|---|
| click | 8.4.2 | 8.5.0 |
| cryptography | 50.0.1 | 50.0.2 |
| MarkupSafe | 3.0.3 | 3.0.4 |
| pycparser | 3.0 | 3.12 |
| Werkzeug | 3.1.8 | 3.1.9 |
| blinker, cffi, colorama, Flask, itsdangerous, Jinja2, psutil, PyJWT, python-dotenv, scapy | same | same |

Commit `d0f89d4` pins all fifteen exactly in `requirements.txt` (the model packages were already
pinned in `requirements-model.lock`). The repeated clean install (build `0.6.0+d0f89d4`,
`clean_install.json`) installed exactly the 24 pinned versions, plus one package nobody had
pinned: `narwhals`, which scikit-learn needs. It came out as 2.28.0 against 2.23.0 in the tested
environment, the same drift. It is now pinned to 2.23.0 in `requirements.txt` rather than in
`requirements-model.lock`, because the model lock's checksum is part of every published model's
compatibility record, and changing it would make the published model 60 refuse to load. The final
QA run repeated the clean install (build `0.6.0+24b6eab`): all 25 installed packages match the
pins exactly, and nothing unpinned was installed.
