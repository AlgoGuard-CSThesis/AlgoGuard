"""INT-01: list the base models and final estimator inside a deployed Stacking artifact."""
import hashlib
import sys
from pathlib import Path

import joblib
from sklearn.ensemble import StackingClassifier
from sklearn.pipeline import Pipeline

path = Path(sys.argv[1])
artifact = joblib.load(path)
pipe = artifact["pipeline"]
clf = pipe.steps[-1][1] if isinstance(pipe, Pipeline) else pipe
print("file:", path.name, "sha256:", hashlib.sha256(path.read_bytes()).hexdigest()[:16])
print("pipeline steps:", [name for name, _ in pipe.steps] if isinstance(pipe, Pipeline) else "-")
print("final step is StackingClassifier:", isinstance(clf, StackingClassifier))
print("base estimators (name -> fitted type):")
for (name, _), fitted in zip(clf.estimators, clf.estimators_):
    print(f"  {name} -> {type(fitted).__name__}")
print("final estimator:", type(clf.final_estimator_).__name__)
print("cv:", clf.cv, "| stack_method:", clf.stack_method_)
