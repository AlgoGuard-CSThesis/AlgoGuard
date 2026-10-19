"""TC-05 (SYS-02), server side: send the malformed Manual Prediction / /predict inputs through the
validation and prediction function both apps use (services.simulation_service.predict_with_artifact,
called from app.py /simulation and /predict and from cloud_app.py:416), with the deployed model A.

Inputs start from the TC-01 Normal row and change one field, as the browser form would send them
(strings). Nothing is stored: this calls the function directly, outside the web routes.
"""
import json
import sys
from pathlib import Path

import joblib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from services.simulation_service import SimulationServiceError, predict_with_artifact  # noqa: E402

artifact = joblib.load(ROOT / "saved_models/run_3/stacking.joblib")  # = deployment #1 (9c9f0fb1...)
normal = {"dur": "2.001348", "proto": "tcp", "service": "-", "state": "FIN", "spkts": "16",
          "dpkts": "14", "sbytes": "862", "dbytes": "802", "rate": "14.490233", "sttl": "254",
          "dttl": "252", "sload": "3233.820312", "dload": "2977.99292", "sinpkt": "133.423196",
          "dinpkt": "146.368077"}
cases = {
    "control: TC-01 Normal row": dict(normal),
    "a. missing field (dur absent)": {k: v for k, v in normal.items() if k != "dur"},
    "a2. empty field (dur = '')": {**normal, "dur": ""},
    "b. text in a numeric field (spkts = 'abc')": {**normal, "spkts": "abc"},
    "c. out-of-range number (sbytes = '1e308')": {**normal, "sbytes": "1e308"},
    "d. negative byte count (sbytes = '-5')": {**normal, "sbytes": "-5"},
}
results = []
for name, payload in cases.items():
    try:
        out = predict_with_artifact(artifact, payload)
        results.append({"case": name, "outcome": "ACCEPTED", "prediction": out["prediction"],
                        "confidence": out["confidence"]})
    except SimulationServiceError as error:
        results.append({"case": name, "outcome": "REJECTED", "message": str(error)})
print(json.dumps(results, indent=2))
