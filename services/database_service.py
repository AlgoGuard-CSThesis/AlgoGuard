import json
import math
import os
import secrets
import sqlite3
from datetime import datetime, timezone

from werkzeug.security import generate_password_hash

from config import get_config
from services.model_registry import MODEL_WORKFLOW_VERSION

_cfg = get_config()
DATABASE_FOLDER = str(_cfg.database_folder)
DATABASE_PATH = str(_cfg.database_path)


class ClosingSQLiteConnection(sqlite3.Connection):
    """Commit or roll back, then release the SQLite file handle."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


MODEL_RESULT_COLUMNS = {
    "run_id": "INTEGER",
    "model_type": "TEXT",
    "evaluation_status": "TEXT NOT NULL DEFAULT 'completed'",
    "error_message": "TEXT",
    "normalized_accuracy": "REAL",
    "normalized_precision": "REAL",
    "normalized_recall": "REAL",
    "normalized_f1": "REAL",
    "normalized_roc_auc": "REAL",
    "normalized_fpr": "REAL",
    "normalized_cpu": "REAL",
    "normalized_ram": "REAL",
    "normalized_model_size": "REAL",
    "overall_score": "REAL",
    "rank": "INTEGER",
    "artifact_path": "TEXT",
    "is_recommended": "INTEGER NOT NULL DEFAULT 0",
    "is_deployed": "INTEGER NOT NULL DEFAULT 0",
    "deployed_at": "TEXT",
}

PREDICTION_COLUMNS = {
    "deployment_id": "INTEGER",
    "model_name": "TEXT",
    "latency_ms": "REAL",
    "alert_created": "INTEGER NOT NULL DEFAULT 0",
    "input_payload": "TEXT",
}

SYSTEM_LOG_COLUMNS = {
    "message": "TEXT",
    "run_id": "INTEGER",
    "dataset_filename": "TEXT",
    "model_name": "TEXT",
    "prediction_id": "INTEGER",
}

TRAINING_RUN_COUNT_COLUMNS = {
    "normal_count": "INTEGER",
    "anomaly_count": "INTEGER",
}

LEGACY_METRIC_COLUMNS = {
    "cpu_usage": "REAL",
    "training_time": "REAL",
    "normalized_cpu": "REAL",
}


def utc_now():
    """Return a sortable UTC timestamp for SQLite records."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def get_connection():
    """Open a SQLite connection with foreign keys and dictionary-like rows."""
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    connection = sqlite3.connect(
        DATABASE_PATH,
        timeout=30,
        factory=ClosingSQLiteConnection,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def initialize_database():
    """Create the baseline schema, apply additive migrations, and seed an admin."""
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS admin (
                admin_id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                email TEXT,
                role TEXT NOT NULL DEFAULT 'Administrator',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS training_run (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                filename TEXT NOT NULL,
                stored_filename TEXT,
                upload_timestamp TEXT NOT NULL,
                row_count INTEGER,
                feature_count INTEGER,
                normal_count INTEGER,
                anomaly_count INTEGER,
                target_column TEXT,
                class_distribution TEXT,
                validation_status TEXT NOT NULL DEFAULT 'pending',
                preprocessing_status TEXT NOT NULL DEFAULT 'pending',
                training_status TEXT NOT NULL DEFAULT 'pending',
                recommended_model_id INTEGER,
                recommended_model_name TEXT,
                recommended_score REAL,
                deployment_status TEXT NOT NULL DEFAULT 'not_deployed',
                completed_at TEXT,
                error_message TEXT,
                FOREIGN KEY (admin_id) REFERENCES admin (admin_id)
            );

            CREATE TABLE IF NOT EXISTS detection_model (
                model_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER,
                model_name TEXT NOT NULL,
                model_type TEXT,
                version TEXT,
                training_date TEXT,
                accuracy REAL,
                precision_score REAL,
                recall REAL,
                f1_score REAL,
                specificity REAL,
                fpr REAL,
                roc_auc REAL,
                cpu_usage REAL,
                ram_usage REAL,
                training_time REAL,
                model_size REAL,
                evaluation_status TEXT NOT NULL DEFAULT 'completed',
                error_message TEXT,
                normalized_accuracy REAL,
                normalized_precision REAL,
                normalized_recall REAL,
                normalized_f1 REAL,
                normalized_roc_auc REAL,
                normalized_fpr REAL,
                normalized_cpu REAL,
                normalized_ram REAL,
                normalized_model_size REAL,
                overall_score REAL,
                rank INTEGER,
                artifact_path TEXT,
                is_recommended INTEGER NOT NULL DEFAULT 0,
                is_deployed INTEGER NOT NULL DEFAULT 0,
                deployed_at TEXT,
                FOREIGN KEY (run_id) REFERENCES training_run (run_id)
            );

            CREATE TABLE IF NOT EXISTS model_deployment (
                deployment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id INTEGER NOT NULL,
                run_id INTEGER NOT NULL,
                deployed_by INTEGER,
                artifact_path TEXT NOT NULL,
                deployed_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                replaced_deployment_id INTEGER,
                FOREIGN KEY (model_id) REFERENCES detection_model (model_id),
                FOREIGN KEY (run_id) REFERENCES training_run (run_id),
                FOREIGN KEY (deployed_by) REFERENCES admin (admin_id),
                FOREIGN KEY (replaced_deployment_id) REFERENCES model_deployment (deployment_id)
            );

            CREATE TABLE IF NOT EXISTS network_traffic (
                traffic_id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                source_ip TEXT,
                destination_ip TEXT,
                source_port INTEGER,
                destination_port INTEGER,
                protocol TEXT,
                packet_size INTEGER,
                flags TEXT,
                dataset_source TEXT,
                feature_payload TEXT
            );

            CREATE TABLE IF NOT EXISTS capture_session (
                capture_id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                interface TEXT,
                bpf_filter TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                packets_captured INTEGER NOT NULL DEFAULT 0,
                packets_dropped INTEGER NOT NULL DEFAULT 0,
                flows_emitted INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'running',
                FOREIGN KEY (admin_id) REFERENCES admin (admin_id)
            );

            CREATE TABLE IF NOT EXISTS prediction (
                prediction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                traffic_id INTEGER NOT NULL,
                model_id INTEGER NOT NULL,
                deployment_id INTEGER,
                model_name TEXT,
                predicted_label TEXT NOT NULL,
                confidence_score REAL,
                prediction_timestamp TEXT NOT NULL,
                latency_ms REAL,
                alert_created INTEGER NOT NULL DEFAULT 0,
                input_payload TEXT,
                FOREIGN KEY (traffic_id) REFERENCES network_traffic (traffic_id),
                FOREIGN KEY (model_id) REFERENCES detection_model (model_id),
                FOREIGN KEY (deployment_id) REFERENCES model_deployment (deployment_id)
            );

            CREATE TABLE IF NOT EXISTS alert (
                alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_id INTEGER NOT NULL,
                severity_level TEXT NOT NULL,
                alert_status TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                description TEXT,
                FOREIGN KEY (prediction_id) REFERENCES prediction (prediction_id)
            );

            CREATE TABLE IF NOT EXISTS report (
                report_id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                run_id INTEGER,
                report_type TEXT NOT NULL,
                date_range_start TEXT,
                date_range_end TEXT,
                generated_at TEXT NOT NULL,
                FOREIGN KEY (admin_id) REFERENCES admin (admin_id),
                FOREIGN KEY (run_id) REFERENCES training_run (run_id)
            );

            CREATE TABLE IF NOT EXISTS report_alert (
                report_id INTEGER NOT NULL,
                alert_id INTEGER NOT NULL,
                PRIMARY KEY (report_id, alert_id),
                FOREIGN KEY (report_id) REFERENCES report (report_id),
                FOREIGN KEY (alert_id) REFERENCES alert (alert_id)
            );

            CREATE TABLE IF NOT EXISTS system_log (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                module TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT,
                run_id INTEGER,
                dataset_filename TEXT,
                model_name TEXT,
                prediction_id INTEGER,
                ip_address TEXT,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (admin_id) REFERENCES admin (admin_id),
                FOREIGN KEY (run_id) REFERENCES training_run (run_id),
                FOREIGN KEY (prediction_id) REFERENCES prediction (prediction_id)
            );

            CREATE TABLE IF NOT EXISTS schema_migration (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            );

            """
        )
        _apply_training_workflow_migration(connection)
        _apply_traffic_count_migration(connection)
        _apply_legacy_metric_compatibility_migration(connection)
        _ensure_indexes(connection)

    seed_default_admin()


def _column_names(connection, table_name):
    return {
        row["name"] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def _add_missing_columns(connection, table_name, columns):
    existing = _column_names(connection, table_name)
    for column_name, column_type in columns.items():
        if column_name not in existing:
            connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def _apply_training_workflow_migration(connection):
    """Upgrade older databases without deleting or rewriting existing rows."""
    migration = connection.execute(
        "SELECT version FROM schema_migration WHERE version = 1"
    ).fetchone()
    if migration:
        return

    _add_missing_columns(connection, "detection_model", MODEL_RESULT_COLUMNS)
    _add_missing_columns(connection, "prediction", PREDICTION_COLUMNS)
    _add_missing_columns(connection, "system_log", SYSTEM_LOG_COLUMNS)
    _add_missing_columns(connection, "network_traffic", {"feature_payload": "TEXT"})
    _add_missing_columns(connection, "report", {"run_id": "INTEGER"})

    connection.execute(
        "INSERT INTO schema_migration (version, name, applied_at) VALUES (?, ?, ?)",
        (1, "seven_model_training_workflow", utc_now()),
    )


def _apply_traffic_count_migration(connection):
    """Store explicit Normal and Attack counts for every new training run."""
    migration = connection.execute(
        "SELECT version FROM schema_migration WHERE version = 2"
    ).fetchone()
    if migration:
        return
    _add_missing_columns(connection, "training_run", TRAINING_RUN_COUNT_COLUMNS)
    connection.execute(
        "INSERT INTO schema_migration (version, name, applied_at) VALUES (?, ?, ?)",
        (2, "training_run_traffic_counts", utc_now()),
    )


def _apply_legacy_metric_compatibility_migration(connection):
    """Let the ca4 metric contract read databases created by newer revisions."""
    migration = connection.execute(
        "SELECT version FROM schema_migration WHERE version = 4"
    ).fetchone()
    if migration:
        return

    existing = _column_names(connection, "detection_model")
    _add_missing_columns(connection, "detection_model", LEGACY_METRIC_COLUMNS)

    if "cpu_time_seconds" in existing:
        connection.execute(
            """
            UPDATE detection_model
            SET cpu_usage = COALESCE(cpu_usage, cpu_time_seconds)
            """
        )
    if "processing_time_seconds" in existing:
        connection.execute(
            """
            UPDATE detection_model
            SET training_time = COALESCE(training_time, processing_time_seconds)
            """
        )
    if "normalized_cpu_time" in existing:
        connection.execute(
            """
            UPDATE detection_model
            SET normalized_cpu = COALESCE(normalized_cpu, normalized_cpu_time)
            """
        )

    connection.execute(
        "INSERT INTO schema_migration (version, name, applied_at) VALUES (?, ?, ?)",
        (4, "legacy_metric_column_compatibility", utc_now()),
    )


def _ensure_indexes(connection):
    """Create indexes after every required legacy column has been added."""
    connection.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_training_run_uploaded
            ON training_run (upload_timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_detection_model_run
            ON detection_model (run_id, rank);
        CREATE INDEX IF NOT EXISTS idx_prediction_timestamp
            ON prediction (prediction_timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_alert_detected
            ON alert (detected_at DESC);
        CREATE INDEX IF NOT EXISTS idx_system_log_timestamp
            ON system_log (timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_system_log_run
            ON system_log (run_id, timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_model_deployment_active
            ON model_deployment (is_active, deployed_at DESC);
        """
    )


def migration_status():
    """Return applied schema migration records."""
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT version, name, applied_at FROM schema_migration ORDER BY version"
        ).fetchall()
        return [dict(row) for row in rows]


def seed_default_admin():
    """Create the first local admin without a predictable fallback password."""
    cfg = get_config()
    username = cfg.admin_username
    email = cfg.admin_email

    with get_connection() as connection:
        if connection.execute("SELECT COUNT(*) FROM admin").fetchone()[0]:
            return

        password = cfg.admin_password
        generated_password = password is None
        if generated_password:
            password = secrets.token_urlsafe(18)
        connection.execute(
            """
            INSERT INTO admin (username, password_hash, email, role, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (username, generate_password_hash(password), email, "Administrator", utc_now()),
        )

    if generated_password:
        print(
            "\nAlgoGuard created the first local administrator.\n"
            f"Username: {username}\n"
            f"Password: {password}\n"
            "Store this password now; it will not be shown again.\n",
            flush=True,
        )


def get_admin_by_username(username):
    """Fetch an admin account by username."""
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM admin WHERE LOWER(username) = LOWER(?)",
            (username,),
        ).fetchone()
        return dict(row) if row else None


def list_admins():
    """Return all admin accounts for the management page."""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT admin_id, username, email, role, created_at
            FROM admin
            ORDER BY admin_id
            """
        ).fetchall()
        return [dict(row) for row in rows]


def create_admin(username, password, email="", role="Administrator"):
    """Create a new admin account with a hashed password."""
    username = str(username or "").strip()
    password = str(password or "")
    email = str(email or "").strip()
    role = str(role or "Administrator").strip()

    if len(username) < 3:
        raise ValueError("Username must be at least 3 characters.")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters.")
    if role not in {"Administrator", "Analyst"}:
        raise ValueError("Invalid admin role.")

    with get_connection() as connection:
        existing = connection.execute(
            "SELECT admin_id FROM admin WHERE LOWER(username) = LOWER(?)",
            (username,),
        ).fetchone()
        if existing:
            raise ValueError("An admin with that username already exists.")

        cursor = connection.execute(
            """
            INSERT INTO admin (username, password_hash, email, role, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (username, generate_password_hash(password), email, role, utc_now()),
        )
        return cursor.lastrowid


def log_system_event(
    admin_id,
    module,
    action,
    status,
    ip_address=None,
    message=None,
    run_id=None,
    dataset_filename=None,
    model_name=None,
    prediction_id=None,
):
    """Write a structured, human-readable audit event."""
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO system_log (
                admin_id, module, action, status, message, run_id,
                dataset_filename, model_name, prediction_id, ip_address, timestamp
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                admin_id,
                module,
                action,
                status,
                message or action,
                run_id,
                dataset_filename,
                model_name,
                prediction_id,
                ip_address,
                utc_now(),
            ),
        )
        return cursor.lastrowid


def create_training_run(admin_id, filename, stored_filename=None):
    """Create one independent record for one uploaded CSV."""
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO training_run (
                admin_id, filename, stored_filename, upload_timestamp,
                validation_status, preprocessing_status, training_status
            )
            VALUES (?, ?, ?, ?, 'pending', 'pending', 'pending')
            """,
            (admin_id, filename, stored_filename, utc_now()),
        )
        return cursor.lastrowid


def update_training_run(run_id, **values):
    """Update allowed training-run status and metadata fields."""
    allowed = {
        "stored_filename",
        "row_count",
        "feature_count",
        "normal_count",
        "anomaly_count",
        "target_column",
        "class_distribution",
        "validation_status",
        "preprocessing_status",
        "training_status",
        "recommended_model_id",
        "recommended_model_name",
        "recommended_score",
        "deployment_status",
        "completed_at",
        "error_message",
    }
    updates = {key: value for key, value in values.items() if key in allowed}
    if not updates:
        return
    if "class_distribution" in updates and not isinstance(updates["class_distribution"], str):
        updates["class_distribution"] = json.dumps(updates["class_distribution"])

    assignments = ", ".join(f"{column} = ?" for column in updates)
    params = list(updates.values()) + [run_id]
    with get_connection() as connection:
        connection.execute(
            f"UPDATE training_run SET {assignments} WHERE run_id = ?",
            params,
        )


def _decode_run(row):
    if not row:
        return None
    result = dict(row)
    raw_distribution = result.get("class_distribution")
    if raw_distribution:
        try:
            result["class_distribution"] = json.loads(raw_distribution)
        except json.JSONDecodeError:
            result["class_distribution"] = {}
    else:
        result["class_distribution"] = {}
    return result


def get_training_run(run_id):
    """Return one training run."""
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM training_run WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return _decode_run(row)


def get_latest_training_run():
    """Return the newest training run."""
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM training_run ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
        return _decode_run(row)


def list_training_runs(page=1, per_page=20):
    """Return independent training runs with pagination."""
    page = max(int(page), 1)
    per_page = max(min(int(per_page), 100), 1)
    offset = (page - 1) * per_page
    with get_connection() as connection:
        total = connection.execute("SELECT COUNT(*) FROM training_run").fetchone()[0]
        rows = connection.execute(
            """
            SELECT tr.*, a.username AS admin_username,
                   COUNT(dm.model_id) AS model_result_count
            FROM training_run tr
            LEFT JOIN admin a ON a.admin_id = tr.admin_id
            LEFT JOIN detection_model dm ON dm.run_id = tr.run_id
            GROUP BY tr.run_id
            ORDER BY tr.run_id DESC
            LIMIT ? OFFSET ?
            """,
            (per_page, offset),
        ).fetchall()
    return {
        "items": [_decode_run(row) for row in rows],
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": max((total + per_page - 1) // per_page, 1),
    }


def insert_model_result(run_id, model_result, version=MODEL_WORKFLOW_VERSION):
    """Persist raw metrics, normalized metrics, score, and artifact metadata."""
    normalized = model_result.get("normalized_metrics") or {}
    fpr = _safe_float(model_result.get("false_positive_rate"))
    specificity = round(100 - fpr, 6) if fpr is not None else None

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO detection_model (
                run_id, model_name, model_type, version, training_date,
                accuracy, precision_score, recall, f1_score, specificity,
                fpr, roc_auc, cpu_usage, ram_usage, training_time, model_size,
                evaluation_status, error_message,
                normalized_accuracy, normalized_precision, normalized_recall,
                normalized_f1, normalized_roc_auc, normalized_fpr,
                normalized_cpu, normalized_ram, normalized_model_size,
                overall_score, rank, artifact_path, is_recommended
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                run_id,
                model_result.get("model_name"),
                model_result.get("model_type"),
                version,
                utc_now(),
                _safe_float(model_result.get("accuracy")),
                _safe_float(model_result.get("precision")),
                _safe_float(model_result.get("recall")),
                _safe_float(model_result.get("f1_score")),
                specificity,
                fpr,
                _safe_float(model_result.get("roc_auc")),
                _safe_float(model_result.get("cpu_usage")),
                _safe_float(model_result.get("ram_usage")),
                _safe_float(model_result.get("processing_time")),
                _safe_float(model_result.get("model_size")),
                model_result.get("status", "completed"),
                model_result.get("error_message"),
                _safe_float(normalized.get("accuracy")),
                _safe_float(normalized.get("precision")),
                _safe_float(normalized.get("recall")),
                _safe_float(normalized.get("f1_score")),
                _safe_float(normalized.get("roc_auc")),
                _safe_float(normalized.get("false_positive_rate")),
                _safe_float(normalized.get("cpu_usage")),
                _safe_float(normalized.get("ram_usage")),
                _safe_float(normalized.get("model_size")),
                _safe_float(model_result.get("overall_score")),
                model_result.get("rank"),
                model_result.get("model_path"),
                int(bool(model_result.get("is_recommended"))),
            ),
        )
        return cursor.lastrowid


def save_training_results(run_id, model_results, best_model):
    """Save all evaluations and retain the top individual comparison result."""
    model_ids = {}
    for result in model_results:
        result["is_recommended"] = bool(
            best_model and result.get("model_name") == best_model.get("model_name")
        )
        model_ids[result.get("model_name")] = insert_model_result(run_id, result)

    if best_model:
        best_id = model_ids[best_model["model_name"]]
        update_training_run(
            run_id,
            recommended_model_id=best_id,
            recommended_model_name=best_model["model_name"],
            recommended_score=best_model["overall_score"],
            training_status="completed",
            completed_at=utc_now(),
            error_message=None,
        )
        return best_id

    update_training_run(
        run_id,
        training_status="failed",
        completed_at=utc_now(),
        error_message="No model completed all nine required metrics.",
    )
    return None


def list_model_results(run_id):
    """Return model evaluations for one run, ranked when available."""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT dm.*, tr.training_status AS run_training_status
            FROM detection_model dm
            JOIN training_run tr ON tr.run_id = dm.run_id
            WHERE dm.run_id = ?
            ORDER BY CASE WHEN rank IS NULL THEN 1 ELSE 0 END, rank, model_name
            """,
            (run_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_model_result(model_id):
    """Return one deployable model result and its run metadata."""
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT dm.*, tr.filename, tr.deployment_status,
                   tr.training_status AS run_training_status
            FROM detection_model dm
            LEFT JOIN training_run tr ON tr.run_id = dm.run_id
            WHERE dm.model_id = ?
            """,
            (model_id,),
        ).fetchone()
        return dict(row) if row else None


def record_deployment(model_id, run_id, deployed_by, artifact_path):
    """Activate one deployment and preserve replacement history."""
    with get_connection() as connection:
        # Serialize activation across independent training processes, including
        # the read of the deployment that this transaction will replace.
        connection.execute("BEGIN IMMEDIATE")
        previous = connection.execute(
            "SELECT * FROM model_deployment WHERE is_active = 1 LIMIT 1"
        ).fetchone()

        connection.execute("UPDATE model_deployment SET is_active = 0 WHERE is_active = 1")
        connection.execute("UPDATE detection_model SET is_deployed = 0 WHERE is_deployed = 1")
        if previous:
            connection.execute(
                "UPDATE training_run SET deployment_status = 'replaced' WHERE run_id = ?",
                (previous["run_id"],),
            )

        deployed_at = utc_now()
        cursor = connection.execute(
            """
            INSERT INTO model_deployment (
                model_id, run_id, deployed_by, artifact_path, deployed_at,
                is_active, replaced_deployment_id
            )
            VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            (
                model_id,
                run_id,
                deployed_by,
                artifact_path,
                deployed_at,
                previous["deployment_id"] if previous else None,
            ),
        )
        connection.execute(
            """
            UPDATE detection_model
            SET is_deployed = 1, deployed_at = ?
            WHERE model_id = ?
            """,
            (deployed_at, model_id),
        )
        connection.execute(
            "UPDATE training_run SET deployment_status = 'deployed' WHERE run_id = ?",
            (run_id,),
        )
        return {
            "deployment_id": cursor.lastrowid,
            "previous": dict(previous) if previous else None,
            "deployed_at": deployed_at,
        }


def get_active_deployment():
    """Return the currently active deployment and model/run metadata."""
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT md.*, dm.model_name, dm.model_type, dm.overall_score,
                   dm.accuracy, dm.precision_score, dm.recall, dm.f1_score,
                   dm.roc_auc, dm.fpr, dm.cpu_usage, dm.ram_usage, dm.model_size,
                   tr.filename, tr.upload_timestamp
            FROM model_deployment md
            JOIN detection_model dm ON dm.model_id = md.model_id
            JOIN training_run tr ON tr.run_id = md.run_id
            WHERE md.is_active = 1
            ORDER BY md.deployment_id DESC
            LIMIT 1
            """
        ).fetchone()
        return dict(row) if row else None


def insert_network_traffic_from_flow(flow_data, dataset_source="simulation"):
    """Store one manual traffic record while retaining its full feature payload."""
    with get_connection() as connection:
        return _insert_network_traffic_from_flow(connection, flow_data, dataset_source)


def _insert_network_traffic_from_flow(connection, flow_data, dataset_source):
    source_bytes = _safe_float(flow_data.get("sbytes"), 0) or 0
    destination_bytes = _safe_float(flow_data.get("dbytes"), 0) or 0
    packet_size = _safe_int(
        flow_data.get("packet_size"),
        _safe_int(source_bytes + destination_bytes),
    )

    cursor = connection.execute(
        """
        INSERT INTO network_traffic (
            timestamp, source_ip, destination_ip, source_port,
            destination_port, protocol, packet_size, flags,
            dataset_source, feature_payload
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(flow_data.get("timestamp") or utc_now()),
            str(flow_data.get("source_ip") or "manual"),
            str(flow_data.get("destination_ip") or "manual"),
            _safe_int(flow_data.get("source_port"), 0),
            _safe_int(flow_data.get("destination_port"), 0),
            str(flow_data.get("protocol") or flow_data.get("proto") or ""),
            packet_size,
            str(flow_data.get("flags") or flow_data.get("state") or ""),
            dataset_source,
            json.dumps(flow_data, default=str),
        ),
    )
    return cursor.lastrowid


def insert_capture_session(admin_id, interface, bpf_filter):
    """Record the start of one live packet-capture session."""
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO capture_session (admin_id, interface, bpf_filter, started_at)
            VALUES (?, ?, ?, ?)
            """,
            (admin_id, str(interface or "default"), str(bpf_filter or ""), utc_now()),
        )
        return cursor.lastrowid


def finalize_capture_session(capture_id, packets_captured, packets_dropped, flows_emitted, status):
    """Close a live capture session's record with its final counters."""
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE capture_session
            SET finished_at = ?, packets_captured = ?, packets_dropped = ?,
                flows_emitted = ?, status = ?
            WHERE capture_id = ?
            """,
            (
                utc_now(),
                int(packets_captured or 0),
                int(packets_dropped or 0),
                int(flows_emitted or 0),
                str(status),
                capture_id,
            ),
        )


def list_capture_sessions(limit=50):
    """Most recent live capture sessions, newest first."""
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM capture_session ORDER BY capture_id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [dict(row) for row in rows]


def insert_prediction(
    traffic_id,
    model_id,
    predicted_label,
    confidence_score=None,
    deployment_id=None,
    model_name=None,
    latency_ms=None,
    input_payload=None,
    alert_created=False,
):
    """Store a single-record prediction without dataset-level metrics."""
    with get_connection() as connection:
        return _insert_prediction(
            connection,
            traffic_id,
            model_id,
            predicted_label,
            confidence_score,
            deployment_id,
            model_name,
            latency_ms,
            input_payload,
            alert_created,
        )


def _insert_prediction(
    connection,
    traffic_id,
    model_id,
    predicted_label,
    confidence_score=None,
    deployment_id=None,
    model_name=None,
    latency_ms=None,
    input_payload=None,
    alert_created=False,
):
    cursor = connection.execute(
        """
        INSERT INTO prediction (
            traffic_id, model_id, deployment_id, model_name,
            predicted_label, confidence_score, prediction_timestamp,
            latency_ms, alert_created, input_payload
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            traffic_id,
            model_id,
            deployment_id,
            model_name,
            str(predicted_label),
            _safe_float(confidence_score),
            utc_now(),
            _safe_float(latency_ms),
            int(bool(alert_created)),
            json.dumps(input_payload or {}, default=str),
        ),
    )
    return cursor.lastrowid


def mark_prediction_alert_created(prediction_id):
    with get_connection() as connection:
        connection.execute(
            "UPDATE prediction SET alert_created = 1 WHERE prediction_id = ?",
            (prediction_id,),
        )


def insert_alert(prediction_id, severity_level, description, alert_status="Open"):
    """Store an attack alert linked to one prediction."""
    with get_connection() as connection:
        return _insert_alert(
            connection,
            prediction_id,
            severity_level,
            description,
            alert_status,
        )


def _insert_alert(connection, prediction_id, severity_level, description, alert_status="Open"):
    cursor = connection.execute(
        """
        INSERT INTO alert (
            prediction_id, severity_level, alert_status, detected_at, description
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (prediction_id, severity_level, alert_status, utc_now(), description),
    )
    return cursor.lastrowid


def store_classified_flow(
    flow_data,
    dataset_source,
    model_id,
    predicted_label,
    confidence_score=None,
    deployment_id=None,
    model_name=None,
    latency_ms=None,
    create_alert=False,
    alert_severity="High",
    alert_description=None,
):
    """Atomically store a classified flow, its prediction, and optional alert."""
    with get_connection() as connection:
        traffic_id = _insert_network_traffic_from_flow(
            connection,
            flow_data,
            dataset_source,
        )
        prediction_id = _insert_prediction(
            connection,
            traffic_id,
            model_id,
            predicted_label,
            confidence_score,
            deployment_id,
            model_name,
            latency_ms,
            flow_data,
            alert_created=create_alert,
        )
        alert_id = None
        if create_alert:
            alert_id = _insert_alert(
                connection,
                prediction_id,
                alert_severity,
                alert_description or f"{predicted_label} traffic detected.",
            )
        return traffic_id, prediction_id, alert_id


def list_alerts(limit=100):
    """Return newest anomalous prediction alerts."""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT a.*, p.predicted_label, p.confidence_score, p.model_name,
                   p.prediction_timestamp
            FROM alert a
            JOIN prediction p ON p.prediction_id = a.prediction_id
            ORDER BY a.alert_id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [dict(row) for row in rows]


def get_latest_prediction():
    """Return the newest manual prediction."""
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT * FROM prediction
            ORDER BY prediction_id DESC
            LIMIT 1
            """
        ).fetchone()
        return dict(row) if row else None


def get_detection_stats():
    """Aggregate detection activity for the dashboard in one pass."""
    with get_connection() as connection:
        prediction_row = connection.execute(
            """
            SELECT
                COUNT(*) AS total_flows,
                SUM(CASE WHEN predicted_label = 'Attack' THEN 1 ELSE 0 END) AS attack_count,
                SUM(CASE WHEN predicted_label = 'Normal' THEN 1 ELSE 0 END) AS normal_count,
                AVG(latency_ms) AS avg_latency_ms,
                AVG(confidence_score) AS avg_confidence,
                MAX(prediction_timestamp) AS last_prediction_at
            FROM prediction
            """
        ).fetchone()
        alert_row = connection.execute(
            """
            SELECT
                COUNT(*) AS alerts_total,
                SUM(CASE WHEN alert_status = 'Open' THEN 1 ELSE 0 END) AS alerts_open
            FROM alert
            """
        ).fetchone()

    total_flows = int(prediction_row["total_flows"] or 0)
    attack_count = int(prediction_row["attack_count"] or 0)
    return {
        "total_flows": total_flows,
        "attack_count": attack_count,
        "normal_count": int(prediction_row["normal_count"] or 0),
        "attack_ratio": round((attack_count / total_flows) * 100, 2) if total_flows else 0.0,
        "avg_latency_ms": _safe_float(prediction_row["avg_latency_ms"]),
        "avg_confidence": _safe_float(prediction_row["avg_confidence"]),
        "last_prediction_at": prediction_row["last_prediction_at"],
        "alerts_total": int(alert_row["alerts_total"] or 0),
        "alerts_open": int(alert_row["alerts_open"] or 0),
    }


def count_traffic_by_source():
    """Return how many stored flows came from each ingest source."""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT dataset_source, COUNT(*) AS flow_count
            FROM network_traffic
            GROUP BY dataset_source
            ORDER BY flow_count DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def list_system_logs(filters=None, page=1, per_page=25):
    """Return newest-first, safely filtered system logs with pagination."""
    filters = filters or {}
    page = max(int(page), 1)
    per_page = max(min(int(per_page), 100), 1)
    conditions = []
    params = []

    if filters.get("date_from"):
        conditions.append("date(sl.timestamp) >= date(?)")
        params.append(filters["date_from"])
    if filters.get("date_to"):
        conditions.append("date(sl.timestamp) <= date(?)")
        params.append(filters["date_to"])
    if filters.get("module"):
        conditions.append("sl.module = ?")
        params.append(filters["module"])
    if filters.get("status"):
        conditions.append("sl.status = ?")
        params.append(filters["status"])
    if filters.get("model"):
        conditions.append("sl.model_name = ?")
        params.append(filters["model"])
    if filters.get("run_id"):
        conditions.append("sl.run_id = ?")
        params.append(filters["run_id"])
    if filters.get("search"):
        search = f"%{filters['search']}%"
        conditions.append(
            "(sl.message LIKE ? OR sl.action LIKE ? OR sl.dataset_filename LIKE ? "
            "OR sl.model_name LIKE ? OR CAST(sl.run_id AS TEXT) LIKE ?)"
        )
        params.extend([search, search, search, search, search])

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    offset = (page - 1) * per_page
    with get_connection() as connection:
        total = connection.execute(
            f"SELECT COUNT(*) FROM system_log sl {where}",
            params,
        ).fetchone()[0]
        rows = connection.execute(
            f"""
            SELECT sl.*, a.username AS admin_username
            FROM system_log sl
            LEFT JOIN admin a ON a.admin_id = sl.admin_id
            {where}
            ORDER BY sl.log_id DESC
            LIMIT ? OFFSET ?
            """,
            params + [per_page, offset],
        ).fetchall()

    return {
        "items": [dict(row) for row in rows],
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": max((total + per_page - 1) // per_page, 1),
    }


def get_log_filter_options():
    """Return distinct values used by the System Logs filters."""
    with get_connection() as connection:
        modules = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT module FROM system_log ORDER BY module"
            ).fetchall()
            if row[0]
        ]
        statuses = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT status FROM system_log ORDER BY status"
            ).fetchall()
            if row[0]
        ]
        models = [
            row[0]
            for row in connection.execute(
                """
                SELECT DISTINCT model_name FROM system_log
                WHERE model_name IS NOT NULL AND model_name != ''
                ORDER BY model_name
                """
            ).fetchall()
        ]
    return {"modules": modules, "statuses": statuses, "models": models}


def insert_report(
    admin_id,
    report_type,
    date_range_start=None,
    date_range_end=None,
    run_id=None,
):
    """Store generated report metadata."""
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO report (
                admin_id, run_id, report_type,
                date_range_start, date_range_end, generated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                admin_id,
                run_id,
                report_type,
                date_range_start,
                date_range_end,
                utc_now(),
            ),
        )
        return cursor.lastrowid


def _safe_float(value, default=None):
    try:
        if value is None or value == "":
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_int(value, default=None):
    try:
        if value is None or value == "":
            return default
        try:
            number = int(value)
        except ValueError:
            number = int(float(value))
        # SQLite INTEGER is signed 64-bit. Keep exact integers out of float
        # conversion, and leave unavailable summary metadata at its fallback.
        return number if -(2**63) <= number < 2**63 else default
    except (TypeError, ValueError, OverflowError):
        return default
