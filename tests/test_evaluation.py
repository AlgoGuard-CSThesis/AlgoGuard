import pytest

from services.evaluation_service import (
    RANKING_METRICS,
    identify_best_model,
    normalize_model_results,
    rank_model_results,
)


def make_result(name, value=50.0, **overrides):
    result = {
        "model_name": name,
        "status": "completed",
        "accuracy": value,
        "precision": value,
        "recall": value,
        "f1_score": value,
        "roc_auc": value,
        "false_positive_rate": value,
        "cpu_usage": value,
        "ram_usage": value,
        "model_size": value,
    }
    result.update(overrides)
    return result


def test_higher_is_better_normalization():
    low = make_result("Low", accuracy=70)
    high = make_result("High", accuracy=90)
    normalize_model_results([low, high])
    assert low["normalized_metrics"]["accuracy"] == 0
    assert high["normalized_metrics"]["accuracy"] == 1


def test_lower_is_better_normalization_is_reversed():
    low = make_result("Low resource", ram_usage=10)
    high = make_result("High resource", ram_usage=30)
    normalize_model_results([low, high])
    assert low["normalized_metrics"]["ram_usage"] == 1
    assert high["normalized_metrics"]["ram_usage"] == 0


def test_equal_values_receive_equal_score_without_division_by_zero():
    results = [make_result("A"), make_result("B")]
    normalize_model_results(results)
    assert all(
        score == 1.0 for result in results for score in result["normalized_metrics"].values()
    )


def test_invalid_metric_is_excluded_from_winner_selection():
    valid = make_result("Valid", value=60)
    invalid = make_result("Invalid", value=99, roc_auc=None)
    normalize_model_results([valid, invalid])
    assert invalid["status"] == "incomplete"
    assert invalid["overall_score"] is None
    best = identify_best_model([valid, invalid])
    assert best is not None and best["model_name"] == "Valid"


def test_overall_score_is_average_of_exactly_nine_metrics():
    results = [
        make_result("A", accuracy=20, cpu_usage=8),
        make_result("B", accuracy=80, cpu_usage=2),
    ]
    normalize_model_results(results)
    for result in results:
        assert tuple(result["normalized_metrics"]) == RANKING_METRICS
        expected = sum(result["normalized_metrics"].values()) / 9
        assert result["overall_score"] == pytest.approx(expected)


def test_tie_breaking_is_deterministic():
    results = [
        make_result("Zulu", f1_score=80, recall=85),
        make_result("Alpha", f1_score=80, recall=85),
        make_result("Lower F1", f1_score=79, recall=99),
    ]
    for result in results:
        result["overall_score"] = 0.75
    ranked = rank_model_results(results)
    assert [result["model_name"] for result in ranked] == ["Alpha", "Zulu", "Lower F1"]


def test_recommended_winner_is_driven_by_metrics():
    first = make_result(
        "Random Forest", accuracy=10, precision=10, recall=10, f1_score=10, roc_auc=10
    )
    second = make_result(
        "Naive Bayes", accuracy=90, precision=90, recall=90, f1_score=90, roc_auc=90
    )
    normalize_model_results([first, second])
    best = identify_best_model([first, second])
    assert best is not None and best["model_name"] == "Naive Bayes"
