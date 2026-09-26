import copy
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import joblib
import pytest
from sklearn.pipeline import Pipeline
from stack_support import connect_local_database

from cloud_repository import CloudRepository, UserNodeContext
from model_delivery import ModelCache
from model_runtime import ModelDeliveryError
from publish_model import publish, stage_publication

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def fitted_publication(tmp_path_factory, binary_dataframe):
    from services.flow_tracker_service import FLOW_FEATURE_COLUMNS
    from services.model_registry import build_model_candidates
    from services.preprocessing_service import build_feature_preprocessor, prepare_dataset
    from services.training_service import _artifact_payload

    directory = tmp_path_factory.mktemp("publication")
    frame = binary_dataframe.copy()
    for feature in FLOW_FEATURE_COLUMNS:
        if feature not in frame:
            frame[feature] = "-" if feature == "service" else 1
    frame[list(FLOW_FEATURE_COLUMNS) + ["label"]].to_csv(directory / "training.csv", index=False)
    prepared = prepare_dataset(directory / "training.csv")
    stack = build_model_candidates(stacking_cv=2)["stacking"]["estimator"]
    pipeline = Pipeline(
        [
            (
                "preprocessor",
                build_feature_preprocessor(prepared.numeric_columns, prepared.categorical_columns),
            ),
            ("classifier", stack),
        ]
    )
    pipeline.fit(prepared.X_train, prepared.y_train)
    artifact = _artifact_payload(pipeline, prepared, 1, "stacking", "Stacking Ensemble", "ensemble")
    path = directory / "model.joblib"
    joblib.dump(artifact, path)
    model = dict(
        model_name="Stacking Ensemble",
        version="stacking-five-v3",
        run_training_status="completed",
        evaluation_status="completed",
        accuracy=95.0,
        f1_score=95.0,
        roc_auc=95.0,
    )
    return model, path


@pytest.fixture
def publication_cleanup(cloud, local_stack, local_http):
    before = set(row[0] for row in cloud["sql"]("select object_path from public.model_manifest"))
    yield
    after = cloud["sql"](
        "select object_path from public.model_manifest where created_by=%s",
        (cloud["users"][0]["profile"],),
    )
    for (path,) in after:
        if path not in before:
            response = local_http.delete(
                local_stack["api_url"] + "/storage/v1/object/models/" + path,
                headers=cloud["maintenance"],
            )
            assert response.status_code in {200, 400, 404}


def connection(local_stack):
    import psycopg2

    return connect_local_database(psycopg2.connect, local_stack["db_url"])


def run_publication(local_stack, cloud, fitted_publication, publication_id=None, conn=None):
    model, path = fitted_publication
    own_connection = conn is None
    conn = conn or connection(local_stack)
    try:
        return publish(
            conn,
            local_stack["api_url"],
            local_stack["secret_key"],
            publication_id or uuid4(),
            model,
            path,
            cloud["users"][0]["profile"],
        )
    finally:
        if own_connection:
            conn.close()


def test_publication_download_and_immutable_metadata(
    cloud, scope, local_stack, fitted_publication, publication_cleanup, tmp_path
):
    cloud["sql"]("NOTIFY pgrst, 'reload schema'")
    publication_id = uuid4()
    published = run_publication(local_stack, cloud, fitted_publication, publication_id)
    assert published["status"] == "active"
    assert (
        run_publication(local_stack, cloud, fitted_publication, publication_id)["manifest_id"]
        == published["manifest_id"]
    )
    actor = cloud["users"][1]
    repo = CloudRepository(
        local_stack["api_url"],
        local_stack["publishable_key"],
        UserNodeContext(
            UUID(actor["id"]), actor["profile"], UUID(scope["nodes"][0]), actor["token"]
        ),
    )
    cache = ModelCache(repo, tmp_path)
    first = cache.load_active()
    assert first.manifest["manifest_id"] == published["manifest_id"]
    assert cache.load_active().cache_path == first.cache_path
    assert first.artifact["model_name"] == "Stacking Ensemble"
    for user in cloud["users"]:
        response = cloud["request"](
            "PATCH",
            "/rest/v1/model_manifest",
            user,
            params={"manifest_id": "eq." + str(published["manifest_id"])},
            json={"object_sha256": "0" * 64},
        )
        assert response.status_code in {401, 403}
        response = cloud["rpc"](
            "activate_model_publication", {"p_publication_id": str(publication_id)}, user
        )
        assert response.status_code in {401, 403, 404}
    with pytest.raises(Exception, match="immutable"):
        cloud["sql"](
            "update public.model_manifest set object_sha256=%s where manifest_id=%s",
            ("0" * 64, published["manifest_id"]),
        )


def test_independent_publishers_serialize_and_retry_does_not_reactivate(
    cloud, local_stack, fitted_publication, publication_cleanup
):
    # Clear only this fixture's publication records, so the race also covers
    # an initially empty deployment table (there is no active row to lock).
    owner = cloud["users"][0]["profile"]
    cloud["sql"]("delete from public.model_manifest where created_by=%s", (owner,))
    cloud["sql"]("delete from public.model_deployment where deployed_by=%s", (owner,))
    assert cloud["sql"]("select count(*) from public.model_deployment")[0][0] == 0
    ids = [uuid4(), uuid4()]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda value: run_publication(local_stack, cloud, fitted_publication, value), ids
            )
        )
    active = cloud["sql"](
        "select deployment_id,replaced_deployment_id from public.model_deployment where is_active=1"
    )
    assert len(active) == 1
    assert (
        cloud["sql"]("select count(*) from public.model_manifest where status='active'")[0][0] == 1
    )
    deployments = {row["deployment_id"] for row in results}
    assert active[0][0] in deployments and active[0][1] in deployments
    older = next(row for row in results if row["deployment_id"] != active[0][0])
    retry = run_publication(local_stack, cloud, fitted_publication, UUID(older["publication_id"]))
    assert retry["status"] == "superseded"
    assert (
        cloud["sql"]("select deployment_id from public.model_deployment where is_active=1")[0][0]
        == active[0][0]
    )


def test_failed_quality_gate_and_activation_keep_previous_model(
    cloud, local_stack, fitted_publication, publication_cleanup
):
    model, path = fitted_publication
    failing = {**model, "f1_score": 1.0}
    before = cloud["sql"]("select count(*) from public.model_manifest")[0][0]
    conn = connection(local_stack)
    try:
        with pytest.raises(ModelDeliveryError, match="Quality gate"):
            stage_publication(conn, uuid4(), failing, path)
        assert cloud["sql"]("select count(*) from public.model_manifest")[0][0] == before
        previous = run_publication(local_stack, cloud, fitted_publication)

        fault = {"mode": "before", "committed": False}

        class BrokenCursor:
            def __init__(self):
                self.cursor = conn.cursor()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.cursor.close()

            def execute(self, query, *args):
                if "select public.activate_model_publication" in query:
                    if fault["mode"] == "before":
                        raise RuntimeError("injected activation failure")
                    fault["committed"] = True
                return self.cursor.execute(query, *args)

            def __getattr__(self, name):
                return getattr(self.cursor, name)

        class BrokenActivation:
            def __enter__(self):
                conn.__enter__()
                return self

            def __exit__(self, *args):
                result = conn.__exit__(*args)
                if fault["committed"]:
                    fault["committed"] = False
                    raise RuntimeError("injected lost commit acknowledgement")
                return result

            def cursor(self):
                return BrokenCursor()

            def rollback(self):
                conn.rollback()

        failed_id = uuid4()
        with pytest.raises(ModelDeliveryError, match="incomplete"):
            run_publication(local_stack, cloud, fitted_publication, failed_id, BrokenActivation())
        assert (
            cloud["sql"](
                "select status from public.model_manifest where publication_id=%s",
                (str(failed_id),),
            )[0][0]
            == "orphaned"
        )
        assert (
            cloud["sql"]("select manifest_id from public.model_manifest where status='active'")[0][
                0
            ]
            == previous["manifest_id"]
        )
        assert (
            run_publication(local_stack, cloud, fitted_publication, failed_id)["status"] == "active"
        )
        fault["mode"] = "after"
        lost_id = uuid4()
        with pytest.raises(ModelDeliveryError, match="incomplete"):
            run_publication(local_stack, cloud, fitted_publication, lost_id, BrokenActivation())
        committed = cloud["sql"](
            "select manifest_id from public.model_manifest "
            "where publication_id=%s and status='active'",
            (str(lost_id),),
        )[0][0]
        retry = run_publication(local_stack, cloud, fitted_publication, lost_id)
        assert retry["manifest_id"] == committed and retry["status"] == "active"
        # Historical artifacts without producer provenance must be retrained.
        legacy = copy.deepcopy(joblib.load(path))
        legacy.pop("training_runtime")
        legacy_path = path.with_name("legacy.joblib")
        joblib.dump(legacy, legacy_path)
        with pytest.raises(ModelDeliveryError, match="Retrain"):
            stage_publication(conn, uuid4(), model, legacy_path)
    finally:
        conn.close()
