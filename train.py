"""Offline training and deployment tool for AlgoGuard.

The web application is inference-only: it detects anomalies with the model that
is already deployed. Retraining and redeployment happen here, from a terminal.

Usage:
    python train.py datasets/algoguard_big.csv
    python train.py datasets/algoguard_big.csv --deploy
    python train.py my_traffic.csv --deploy --admin analyst1
"""

import argparse
import os
import sys

import pandas as pd

from config import get_config, load_config
from services.database_service import (
    create_training_run,
    get_admin_by_username,
    initialize_database,
    insert_report,
    list_model_results,
    log_system_event,
    save_training_results,
    update_training_run,
    utc_now,
)
from services.deployment_service import (
    DeploymentError,
    deploy_model,
    stacking_deployment_eligibility,
)
from services.model_registry import STACKING_MODEL_NAME
from services.preprocessing_service import prepare_dataset
from services.training_service import train_and_compare_models


def save_report(run_id, model_results, report_folder=None):
    """Write the per-run model comparison CSV that the web app used to produce."""
    if report_folder is None:
        report_folder = get_config().report_folder
    rows = []
    for result in model_results:
        normalized = result.get("normalized_metrics") or {}
        rows.append(
            {
                "rank": result.get("rank"),
                "model_name": result.get("model_name"),
                "model_type": result.get("model_type"),
                "status": result.get("status"),
                "accuracy": result.get("accuracy"),
                "precision": result.get("precision"),
                "recall": result.get("recall"),
                "f1_score": result.get("f1_score"),
                "roc_auc": result.get("roc_auc"),
                "false_positive_rate": result.get("false_positive_rate"),
                "cpu_time_seconds": result.get("cpu_usage"),
                "peak_ram_increase_mb": result.get("ram_usage"),
                "model_size_mb": result.get("model_size"),
                **{f"normalized_{key}": value for key, value in normalized.items()},
                "overall_score": result.get("overall_score"),
                "error_message": result.get("error_message"),
            }
        )
    os.makedirs(report_folder, exist_ok=True)
    report_name = f"training_run_{run_id}_model_results.csv"
    pd.DataFrame(rows).to_csv(os.path.join(report_folder, report_name), index=False)
    return report_name


def _format_metric(value, suffix="%"):
    if not isinstance(value, (int, float)):
        return "-"
    return f"{float(value):.2f}{suffix}"


def _print_ranked_table(model_results):
    print("\nModel comparison")
    print("-" * 78)
    print(f"{'#':<3} {'Model':<22} {'Accuracy':>10} {'F1':>10} {'ROC-AUC':>10} {'Score':>10}")
    print("-" * 78)
    # Ranked individual models first (1, 2, 3 ...), then unranked rows such as Stacking.
    ordered = sorted(
        model_results,
        key=lambda item: (item.get("rank") is None, item.get("rank") or 0),
    )
    for result in ordered:
        rank = result.get("rank")
        print(
            f"{rank if rank else '-':<3} "
            f"{str(result.get('model_name'))[:22]:<22} "
            f"{_format_metric(result.get('accuracy')):>10} "
            f"{_format_metric(result.get('f1_score')):>10} "
            f"{_format_metric(result.get('roc_auc')):>10} "
            f"{_format_metric(result.get('overall_score'), ''):>10}"
        )
    print("-" * 78)


def _console_progress(completed, total, model_name, status):
    if status == "started":
        print(f"  [{completed + 1}/{total}] Training {model_name} ...", flush=True)
    else:
        print(f"  [{completed}/{total}] Finished {model_name}", flush=True)


def _default_admin_username():
    """Match the account seeded on a fresh database.

    A new database is seeded with ``ALGOGUARD_ADMIN_USERNAME`` (or ``admin``), so the
    documented first run ``train.py <csv> --deploy`` must default to that same name.
    """
    return load_config().admin_username


def _resolve_admin_id(username):
    admin = get_admin_by_username(username)
    if not admin:
        print(
            f"Admin account '{username}' was not found. "
            "Pass --admin <username> with an existing account.",
            file=sys.stderr,
        )
        return None
    return admin["admin_id"]


def _deploy_stacking(run_id, admin_id):
    """Apply the same quality gate the web app enforced, then deploy."""
    stacking = next(
        (
            model
            for model in list_model_results(run_id)
            if model.get("model_name") == STACKING_MODEL_NAME
        ),
        None,
    )
    eligible, reason = stacking_deployment_eligibility(stacking)
    if not eligible:
        print(f"\nDeployment refused by the quality gate: {reason}", file=sys.stderr)
        log_system_event(
            admin_id,
            "Deployment",
            "deployment_failed",
            "Failed",
            message=reason,
            run_id=run_id,
            model_name=STACKING_MODEL_NAME,
        )
        return False

    log_system_event(
        admin_id,
        "Deployment",
        "deployment_started",
        "Started",
        message=f"Deployment started for {STACKING_MODEL_NAME}.",
        run_id=run_id,
        model_name=STACKING_MODEL_NAME,
    )
    try:
        deployed = deploy_model(stacking["model_id"], admin_id)
    except DeploymentError as error:
        print(f"\nDeployment failed: {error}", file=sys.stderr)
        log_system_event(
            admin_id,
            "Deployment",
            "deployment_failed",
            "Failed",
            message=str(error),
            run_id=run_id,
            model_name=STACKING_MODEL_NAME,
        )
        return False

    previous = deployed["deployment"].get("previous")
    if previous:
        log_system_event(
            admin_id,
            "Deployment",
            "previous_deployed_model_replaced",
            "Success",
            message=f"Previous deployment {previous['deployment_id']} was replaced.",
            run_id=run_id,
            model_name=STACKING_MODEL_NAME,
        )
    log_system_event(
        admin_id,
        "Deployment",
        "model_deployed",
        "Success",
        message=f"{STACKING_MODEL_NAME} is now the active model.",
        run_id=run_id,
        model_name=STACKING_MODEL_NAME,
    )
    print(f"\n{STACKING_MODEL_NAME} is now the active model.")
    print(f"Artifact: {deployed['artifact_path']}")
    return True


def build_parser():
    parser = argparse.ArgumentParser(
        prog="train.py",
        description=(
            "Train the AlgoGuard model comparison offline and optionally deploy "
            "the Stacking Ensemble for the web application to use."
        ),
    )
    parser.add_argument("csv_path", help="Path to a labelled network traffic CSV.")
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="Deploy the Stacking Ensemble if it passes the quality gate.",
    )
    parser.add_argument(
        "--admin",
        default=_default_admin_username(),
        help=(
            "Username recorded as the owner of this run "
            "(default: ALGOGUARD_ADMIN_USERNAME if set, otherwise admin)."
        ),
    )
    parser.add_argument(
        "--models-dir",
        default=str(get_config().saved_model_folder),
        help="Directory that receives the per-run model artifacts.",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    csv_path = os.path.abspath(args.csv_path)
    if not os.path.isfile(csv_path):
        print(f"CSV file not found: {csv_path}", file=sys.stderr)
        return 1

    initialize_database()
    admin_id = _resolve_admin_id(args.admin)
    if admin_id is None:
        return 1

    filename = os.path.basename(csv_path)
    run_id = create_training_run(admin_id, filename, filename)
    print(f"Training run #{run_id} created for {filename}.")

    def event_logger(module, action, status, model_name=None, message=None):
        log_system_event(
            admin_id,
            module,
            action,
            status,
            message=message,
            run_id=run_id,
            dataset_filename=filename,
            model_name=model_name,
        )

    update_training_run(run_id, validation_status="in_progress")
    print("Validating and preprocessing the dataset ...")
    try:
        prepared = prepare_dataset(csv_path)
    except ValueError as error:
        update_training_run(
            run_id,
            validation_status="failed",
            preprocessing_status="failed",
            training_status="failed",
            completed_at=utc_now(),
            error_message=str(error),
        )
        event_logger("Dataset", "validation_failed", "Failed", message=str(error))
        print(f"Validation failed: {error}", file=sys.stderr)
        return 1

    update_training_run(
        run_id,
        row_count=prepared.summary["rows"],
        feature_count=prepared.summary["features"],
        normal_count=prepared.summary["normal_count"],
        anomaly_count=prepared.summary["anomaly_count"],
        target_column=prepared.summary["target_column"],
        class_distribution=prepared.summary["label_counts"],
        validation_status="passed",
        preprocessing_status="completed",
        training_status="in_progress",
    )
    event_logger("Dataset", "validation_passed", "Success", message="CSV validation passed.")
    print(
        f"Prepared {prepared.summary['rows']:,} rows and {prepared.summary['features']} features.\n"
    )

    try:
        results = train_and_compare_models(
            prepared,
            args.models_dir,
            run_id,
            event_logger=event_logger,
            progress_callback=_console_progress,
        )
    except Exception as error:
        update_training_run(
            run_id,
            training_status="failed",
            completed_at=utc_now(),
            error_message=str(error),
        )
        event_logger("Training", "training_failed", "Failed", message=str(error))
        print(f"Training failed: {error}", file=sys.stderr)
        return 1

    save_training_results(run_id, results["model_results"], results["best_model"])
    report_name = save_report(run_id, results["model_results"])
    insert_report(admin_id, "Model Performance", run_id=run_id)
    event_logger("Reports", "report_generated", "Success", message=f"Generated {report_name}.")

    _print_ranked_table(results["model_results"])
    if results["best_model"]:
        print(f"Top individual model: {results['best_model']['model_name']}")
    else:
        print("No candidate completed every required metric.")
    print(f"Report: {get_config().report_folder / report_name}")

    if args.deploy and not _deploy_stacking(run_id, admin_id):
        return 1
    if not args.deploy:
        print("\nRun again with --deploy to promote the Stacking Ensemble.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
