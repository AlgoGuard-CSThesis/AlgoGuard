"""Stage 6A.3: the stored attack probability and its versioned attack-score band."""

import math
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from config import reset_config_cache
from services import database_service as db
from services.alert_policy import (
    ATTACK_SCORE_BAND_POLICY,
    ATTACK_SCORE_BANDS,
    attack_probability,
    attack_score_band,
    attack_score_fields,
)

MIGRATION = (Path(__file__).resolve().parents[1] / "supabase" / "migrations"
             / "20261025010000_attack_score_bands.sql")


@pytest.mark.parametrize(
    ("probability", "band"),
    [(0.0, "Low"), (0.4999, "Low"), (0.5, "Medium"), (0.6999, "Medium"), (0.7, "High"),
     (0.8999, "High"), (0.9, "Very high"), (1.0, "Very high")],
)
def test_each_band_starts_at_its_threshold(probability, band):
    assert attack_score_band(probability) == band


def test_no_score_means_no_band_and_no_policy():
    assert attack_score_band(None) is None
    assert attack_score_fields(None) == {
        "attack_probability": None, "attack_score_band": None, "score_band_policy": None}


@pytest.mark.parametrize("value", [-0.0001, 1.0001, math.nan, math.inf])
def test_a_probability_outside_zero_to_one_is_refused(value):
    with pytest.raises(ValueError):
        attack_score_band(value)


def test_the_attack_probability_is_found_by_class_value():
    assert attack_probability(np.array([0.3, 0.7]), [0, 1]) == pytest.approx(0.7)
    assert attack_probability(np.array([0.7, 0.3]), [1, 0]) == pytest.approx(0.7)
    assert attack_probability(np.array([1.0]), [0]) is None


def test_a_confident_normal_verdict_is_a_low_attack_score():
    fields = attack_score_fields(attack_probability(np.array([0.97, 0.03]), [0, 1]))
    assert fields == {"attack_probability": 0.03, "attack_score_band": "Low",
                      "score_band_policy": ATTACK_SCORE_BAND_POLICY}


def test_the_band_follows_the_stored_rounded_probability():
    fields = attack_score_fields(0.4999996)
    assert (fields["attack_probability"], fields["attack_score_band"]) == (0.5, "Medium")


def test_no_band_is_named_critical():
    assert "Critical" not in {name for name, _ in ATTACK_SCORE_BANDS}


def test_the_thresholds_are_pinned_to_their_policy_version():
    """Changing a threshold needs a new policy version and a migration that accepts it."""
    assert ATTACK_SCORE_BAND_POLICY == "attack-score-bands-v1"
    assert ATTACK_SCORE_BANDS == (
        ("Very high", 0.90), ("High", 0.70), ("Medium", 0.50), ("Low", 0.0))


def test_the_cloud_database_checks_the_same_policy():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert f"score_band_policy='{ATTACK_SCORE_BAND_POLICY}'" in sql
    for name, floor in ATTACK_SCORE_BANDS[:-1]:
        assert f"attack_probability>={floor:.2f} then '{name}'" in sql


# -- SQLite application -------------------------------------------------------------


@pytest.fixture
def model_id(trained_bundle):
    with db.get_connection() as connection:
        row = connection.execute(
            "SELECT model_id FROM detection_model WHERE run_id = ? AND model_name = ?",
            (trained_bundle["run_id"], "Stacking Ensemble"),
        ).fetchone()
    return row["model_id"]


def stored_prediction(prediction_id):
    with db.get_connection() as connection:
        return dict(connection.execute(
            "SELECT * FROM prediction WHERE prediction_id = ?", (prediction_id,)).fetchone())


def test_a_stored_prediction_keeps_its_score_band_and_policy(model_id):
    _, prediction_id, alert_id = db.store_classified_flow(
        {"dur": 1.0, "proto": "tcp"}, "live_monitor", model_id, "Attack", 93.0,
        model_name="Stacking Ensemble", create_alert=True, alert_description="Fixture alert.",
        attack_probability=0.93,
    )
    row = stored_prediction(prediction_id)
    assert (row["attack_probability"], row["attack_score_band"], row["score_band_policy"]) == (
        0.93, "Very high", ATTACK_SCORE_BAND_POLICY)
    alert = next(item for item in db.list_alerts(limit=1_000_000) if item["alert_id"] == alert_id)
    assert alert["attack_score_band"] == "Very high"
    # The legacy severity is still stored for older readers, beside the score.
    assert alert["severity_level"] == "High"


def test_a_prediction_without_a_score_stays_unscored(model_id):
    _, prediction_id, _ = db.store_classified_flow(
        {"dur": 1.0, "proto": "tcp"}, "simulation", model_id, "Normal", 88.0)
    row = stored_prediction(prediction_id)
    assert (row["attack_probability"], row["attack_score_band"], row["score_band_policy"]) == (
        None, None, None)


def test_an_older_database_gains_the_columns_without_inventing_scores(tmp_path, monkeypatch):
    database = tmp_path / "older.sqlite3"
    monkeypatch.setenv("ALGOGUARD_DATABASE_PATH", str(database))
    reset_config_cache()
    try:
        db.initialize_database()
        with sqlite3.connect(database) as connection:
            # Recreate the version-4 shape: no score columns, one stored prediction.
            connection.execute("DELETE FROM schema_migration WHERE version = 5")
            for column in ("attack_probability", "attack_score_band", "score_band_policy"):
                connection.execute(f"ALTER TABLE prediction DROP COLUMN {column}")
            connection.execute(
                "INSERT INTO prediction (traffic_id, model_id, predicted_label, confidence_score,"
                " prediction_timestamp) VALUES (1, 1, 'Attack', 97.0, '2026-10-01 00:00:00')")

        db.initialize_database()

        with sqlite3.connect(database) as connection:
            row = connection.execute(
                "SELECT attack_probability, attack_score_band, score_band_policy FROM prediction"
            ).fetchone()
            versions = [v for (v,) in connection.execute("SELECT version FROM schema_migration")]
        assert row == (None, None, None)
        assert 5 in versions
    finally:
        reset_config_cache()


# -- pages ----------------------------------------------------------------------------


def test_alerts_show_the_attack_score_and_older_alerts_stay_neutral(authenticated_client, model_id):
    for probability in (0.97, None):
        db.store_classified_flow(
            {"dur": 1.0, "proto": "tcp"}, "live_monitor", model_id, "Attack", 97.0,
            model_name="Stacking Ensemble", create_alert=True,
            alert_description="Fixture alert.", attack_probability=probability,
        )
    page = authenticated_client.get("/alerts").get_data(as_text=True)
    assert "<th>Attack score</th>" in page and "<th>Severity</th>" not in page
    assert 'class="status-badge score-very-high"' in page and "Very high &middot; 97.0%" in page
    assert 'class="status-badge score-none"' in page and "Legacy severity: High" in page
    assert "does not measure impact" in page
    assert "Critical" not in page


def test_the_dashboard_names_the_attack_score(authenticated_client, model_id):
    db.store_classified_flow(
        {"dur": 1.0, "proto": "tcp"}, "live_monitor", model_id, "Attack", 75.0,
        model_name="Stacking Ensemble", create_alert=True, alert_description="Fixture alert.",
        attack_probability=0.75,
    )
    dashboard = authenticated_client.get("/").get_data(as_text=True)
    assert "High attack score &middot; 75.0%" in dashboard
