"""Synthetic credentials must not survive the shared logging backstop."""

import pytest

from redaction import redact_for_logging


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "postgresql://admin:SYNTHETIC@PASSWORD@localhost:54322/postgres",
            "postgresql://admin:***@localhost:54322/postgres",
        ),
        (
            "postgresql://admin:SYNTHETIC%2FPASSWORD@localhost/db?sslmode=require",
            "postgresql://admin:***@localhost/db?sslmode=require",
        ),
        (
            "postgresql://admin:SYNTHETIC'PASSWORD@localhost/db",
            "postgresql://admin:***@localhost/db",
        ),
        (
            "failed postgresql://admin:SECRET@host; contact support@example.test",
            "failed postgresql://admin:***@host; contact support@example.test",
        ),
        (
            'request="https://storage.test/object?token=SYNTHETIC&download=file.csv"',
            'request="https://storage.test/object?token=***&download=file.csv"',
        ),
        (
            "https://storage.test/object?X-Amz-Signature=SECRET&X-Api-Key=KEY&page=2",
            "https://storage.test/object?X-Amz-Signature=***&X-Api-Key=***&page=2",
        ),
        (
            "https://storage.test/object?sig=SECRET&access_token=TOKEN#section",
            "https://storage.test/object?sig=***&access_token=***#section",
        ),
        (
            "https://host.test/path?password=SECRET&ordinary=value",
            "https://host.test/path?password=***&ordinary=value",
        ),
        (
            "host=localhost password=SYNTHETIC dbname=demo",
            "host=localhost password=*** dbname=demo",
        ),
        ("password = 'SYNTHETIC WITH SPACE' user=admin", "password = '***' user=admin"),
        (r"password='SYNTHETIC\'TAIL' port=54322", "password='***' port=54322"),
        (r"password=SYNTHETIC\ TAIL dbname=demo", "password=*** dbname=demo"),
        (r"password='SYNTHETIC\\TAIL' user=admin", "password='***' user=admin"),
        ('PASSWORD="SYNTHETIC WITH SPACE" user=admin', 'PASSWORD="***" user=admin'),
        ("password='SYNTHETIC UNTERMINATED", "password='***"),
        ("Authorization: Bearer SYNTHETIC.TOKEN-123", "Authorization: Bearer ***"),
        ("authorization: basic c3ludGhldGljOnNlY3JldA==", "authorization: basic ***"),
        (
            "{'Authorization': 'Bearer SYNTHETIC', 'Accept': 'application/json'}",
            "{'Authorization': 'Bearer ***', 'Accept': 'application/json'}",
        ),
        (
            '{"Authorization": "Basic c3ludGhldGljOnNlY3JldA==", "retry": 2}',
            '{"Authorization": "Basic ***", "retry": 2}',
        ),
        (
            "Proxy-Authorization: Basic c3ludGhldGljOnNlY3JldA==\nAccept: text/plain",
            "Proxy-Authorization: Basic ***\nAccept: text/plain",
        ),
        (
            "host=localhost user=admin dbname=demo port=54322 sslmode=prefer",
            "host=localhost user=admin dbname=demo port=54322 sslmode=prefer",
        ),
        (
            "https://host.test/path?download=report.csv&page=2#section",
            "https://host.test/path?download=report.csv&page=2#section",
        ),
    ],
)
def test_redacts_supported_secret_forms_without_losing_context(source, expected):
    assert redact_for_logging(source) == expected


def test_mixed_credentials_are_redacted_together():
    source = (
        "postgresql://admin:URL_SECRET@host/db "
        "host=localhost password='KEYWORD SECRET' "
        "{'Authorization': 'Bearer AUTH_SECRET'} "
        "https://storage.test/object?token=QUERY_SECRET&download=report.csv"
    )
    result = redact_for_logging(source)
    for secret in ("URL_SECRET", "KEYWORD SECRET", "AUTH_SECRET", "QUERY_SECRET"):
        assert secret not in result
    assert "download=report.csv" in result
    assert redact_for_logging(result) == result
