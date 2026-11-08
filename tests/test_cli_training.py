import pytest

import train


def test_parser_accepts_the_documented_flags():
    args = train.build_parser().parse_args(["data.csv", "--deploy", "--admin", "analyst1"])
    assert args.csv_path == "data.csv"
    assert args.deploy is True
    assert args.admin == "analyst1"


def test_admin_defaults_to_the_seeded_env_username(monkeypatch):
    # The fresh-clone docs say: set ALGOGUARD_ADMIN_USERNAME, then run
    # `train.py <csv> --deploy` with no --admin. The default must follow the env var.
    monkeypatch.setenv("ALGOGUARD_ADMIN_USERNAME", "qaadmin")
    assert train.build_parser().parse_args(["data.csv"]).admin == "qaadmin"

    monkeypatch.setenv("ALGOGUARD_ADMIN_USERNAME", "   ")
    assert train.build_parser().parse_args(["data.csv"]).admin == "admin"

    monkeypatch.delenv("ALGOGUARD_ADMIN_USERNAME", raising=False)
    assert train.build_parser().parse_args(["data.csv"]).admin == "admin"

    # An explicit flag still wins over the environment.
    monkeypatch.setenv("ALGOGUARD_ADMIN_USERNAME", "qaadmin")
    assert train.build_parser().parse_args(["data.csv", "--admin", "other"]).admin == "other"


def test_help_exits_cleanly():
    with pytest.raises(SystemExit) as exit_info:
        train.build_parser().parse_args(["--help"])
    assert exit_info.value.code == 0


def test_missing_csv_exits_non_zero(capsys):
    assert train.main(["definitely-not-a-real-dataset.csv"]) == 1
    assert "CSV file not found" in capsys.readouterr().err


def test_unknown_admin_exits_non_zero(tmp_path, binary_dataframe, capsys):
    csv_path = tmp_path / "traffic.csv"
    binary_dataframe.to_csv(csv_path, index=False)

    assert train.main([str(csv_path), "--admin", "nobody-here"]) == 1
    assert "was not found" in capsys.readouterr().err


def test_save_report_writes_the_run_csv(tmp_path):
    report_name = train.save_report(
        7,
        [
            {
                "rank": 1,
                "model_name": "Random Forest",
                "accuracy": 91.5,
                "normalized_metrics": {"accuracy": 0.915},
            }
        ],
        report_folder=str(tmp_path),
    )
    written = tmp_path / report_name
    assert report_name == "training_run_7_model_results.csv"
    assert written.exists()
    assert "Random Forest" in written.read_text()


def test_live_schema_note_is_silent_for_the_15_flow_features():
    from services.flow_tracker_service import FLOW_FEATURE_COLUMNS

    assert train.live_schema_note(list(reversed(FLOW_FEATURE_COLUMNS))) is None


def test_live_schema_note_names_missing_and_extra_columns():
    """DEF-08: say up front that Live Monitor and publishing will refuse such a model."""
    note = train.live_schema_note(["dur", "proto", "custom_score"])

    assert note is not None
    assert "missing service, state" in note
    assert "extra custom_score" in note
    assert "Live Monitor packet capture and cloud publishing will refuse it" in note


def test_deploy_run_needs_no_csv_and_rejects_one(capsys):
    with pytest.raises(SystemExit):
        train.main(["data.csv", "--deploy-run", "1"])
    with pytest.raises(SystemExit):
        train.main([])  # neither a CSV nor --deploy-run


def test_deploy_run_deploys_an_already_trained_run(trained_bundle, monkeypatch):
    """An evaluated run is deployed as it is, without training it again (DEF-19)."""
    deployed = []
    monkeypatch.setattr(train, "_deploy_stacking",
                        lambda run_id, admin_id: deployed.append(run_id) or True)
    monkeypatch.setattr(train, "create_training_run",
                        lambda *args: pytest.fail("--deploy-run must not train"))
    assert train.main(["--deploy-run", str(trained_bundle["run_id"])]) == 0
    assert deployed == [trained_bundle["run_id"]]
