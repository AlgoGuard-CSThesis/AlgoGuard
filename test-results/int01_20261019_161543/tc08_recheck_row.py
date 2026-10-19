"""TC-08 re-check: score the TC-01 Normal row offline with model A, exactly and with sinpkt typed
as 133.42319, to explain a different confidence after the cache restore."""
import sys

import joblib
import pandas as pd

artifact = joblib.load(sys.argv[1])
pipe = artifact["pipeline"]
row = {"dur": 2.001348, "proto": "tcp", "service": "-", "state": "FIN", "spkts": 16, "dpkts": 14,
       "sbytes": 862, "dbytes": 802, "rate": 14.490233, "sttl": 254, "dttl": 252,
       "sload": 3233.820312, "dload": 2977.99292, "sinpkt": 133.423196, "dinpkt": 146.368077}
variants = {"exact TC-01 row": row, "sinpkt 133.42319": {**row, "sinpkt": 133.42319}}
attack = list(pipe.classes_).index(1)
for name, values in variants.items():
    frame = pd.DataFrame([values])[artifact["feature_columns"]]
    p_attack = float(pipe.predict_proba(frame)[0][attack])
    verdict = "Attack" if p_attack >= 0.5 else "Normal"
    conf = p_attack if verdict == "Attack" else 1 - p_attack
    print(f"{name:18} -> {verdict} {conf * 100:.2f}%")
