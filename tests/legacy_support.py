"""A small legacy installation with colliding IDs and deliberately unattributed evidence."""

import sqlite3

from config import reset_config_cache
from services import database_service as db

BIG_ID = 2**53 + 123
STAMP = "2026-09-10 01:02:03"


def make_legacy(root, monkeypatch, artifact=b"historical model bytes"):
    root.mkdir(parents=True, exist_ok=True)
    database = root / "legacy.sqlite3"
    model = root / "model.joblib"
    model.write_bytes(artifact)
    with monkeypatch.context() as local:
        local.setenv("ALGOGUARD_DATABASE_PATH", str(database))
        reset_config_cache()
        db.initialize_database()
    reset_config_cache()
    with sqlite3.connect(database) as connection:
        connection.execute("pragma foreign_keys=on")
        connection.execute("update admin set username='historical analyst'")
        connection.execute(
            "insert into training_run(run_id,admin_id,filename,upload_timestamp,training_status) "
            "values(1,1,'legacy.csv',?,'completed')",
            (STAMP,),
        )
        connection.execute(
            "insert into detection_model(model_id,run_id,model_name,model_type,version,"
            "accuracy,f1_score,roc_auc,artifact_path,is_deployed) "
            "values(1,1,'Stacking Ensemble','ensemble','stacking-five-v3',95,95,95,?,1)",
            (str(model),),
        )
        connection.execute(
            "insert into model_deployment(deployment_id,model_id,run_id,deployed_by,artifact_path,"
            "deployed_at,is_active) values(1,1,1,1,?,?,1)",
            (str(model), STAMP),
        )
        connection.execute(
            "insert into capture_session(capture_id,admin_id,started_at,finished_at,status) "
            "values(1,1,?,?,'completed')",
            (STAMP, STAMP),
        )
        for index in (1, 2):
            connection.execute(
                "insert into network_traffic(traffic_id,timestamp,source_ip,packet_size,"
                "feature_payload) values(?,?,'10.0.0.1',?,?)",
                (BIG_ID + index, STAMP, 2**40, '{"dur":1.5}'),
            )
            connection.execute(
                "insert into prediction(prediction_id,traffic_id,model_id,deployment_id,"
                "model_name,predicted_label,confidence_score,prediction_timestamp,alert_created) "
                "values(?,?,1,1,'Stacking Ensemble','Attack',94.5,?,1)",
                (index, BIG_ID + index, STAMP),
            )
            connection.execute(
                "insert into alert(alert_id,prediction_id,severity_level,alert_status,detected_at) "
                "values(?,?,'High','New',?)",
                (index, index, STAMP),
            )
        connection.execute(
            "insert into system_log(log_id,admin_id,module,action,status,prediction_id,timestamp) "
            "values(1,1,'Prediction','legacy_prediction','Success',1,?)",
            (STAMP,),
        )
        connection.execute(
            "insert into report(report_id,admin_id,report_type,generated_at) "
            "values(1,1,'Legacy evidence',?)",
            (STAMP,),
        )
        connection.execute("insert into report_alert values(1,1)")
        connection.execute("insert into report_alert values(1,2)")
    return database
