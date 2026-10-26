"""DEF-17: live sessions score flows in a separate process, with an in-process fallback."""

import joblib
import pytest

from services import inference_worker
from services.inference_worker import InferenceWorker, InferenceWorkerError, LiveScorer
from services.scoring_service import score_rows


@pytest.fixture(scope="module")
def stacking(trained_bundle):
    model = next(item for item in trained_bundle["result"]["model_results"]
                 if item["model_id"] == "stacking")
    artifact = joblib.load(model["model_path"])
    columns = artifact["feature_columns"]
    rows = trained_bundle["frame"][columns].head(20).to_dict("records")
    return artifact["pipeline"], columns, rows


def test_the_inference_process_gives_the_same_verdicts_and_scores(stacking):
    pipeline, columns, rows = stacking
    worker = InferenceWorker(pipeline, columns)
    try:
        verdicts, elapsed_ms = worker.score(rows)
        expected, _ = score_rows(pipeline, rows, columns)
        assert verdicts == expected
        assert elapsed_ms >= 0
    finally:
        worker.close()
    assert worker._process.poll() is not None  # the child has exited
    with pytest.raises(InferenceWorkerError):
        worker.score(rows)


def test_a_session_keeps_scoring_when_the_process_cannot_start(stacking, monkeypatch):
    pipeline, columns, rows = stacking

    def refuse(*args, **kwargs):
        raise InferenceWorkerError("The inference process did not start: test")

    monkeypatch.setattr(inference_worker, "InferenceWorker", refuse)
    scorer = LiveScorer(pipeline, columns, score_rows)
    assert scorer.worker is None and "did not start" in (scorer.fallback_reason or "")
    assert scorer.score(rows)[0] == score_rows(pipeline, rows, columns)[0]


def test_a_session_keeps_scoring_when_the_process_dies(stacking):
    pipeline, columns, rows = stacking
    scorer = LiveScorer(pipeline, columns, score_rows)
    try:
        assert scorer.worker is not None
        scorer.worker._process.kill()
        scorer.worker._process.wait(timeout=10)
        verdicts, _ = scorer.score(rows)
        assert verdicts == score_rows(pipeline, rows, columns)[0]
        assert scorer.worker is None and "stopped answering" in (scorer.fallback_reason or "")
    finally:
        scorer.close()
