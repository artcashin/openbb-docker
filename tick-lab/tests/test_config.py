"""S3 connection settings read from the environment."""

import pytest

from tick_lab.config import ConfigError, S3Config, from_env

FULL = {
    "ARCTICDB_S3_ENDPOINT": "minio.example.ts.net",
    "ARCTICDB_S3_BUCKET": "openbb",
    "ARCTICDB_S3_ACCESS": "someaccesskey",
    "ARCTICDB_S3_SECRET": "somesecretkey",
}


def test_reads_a_complete_environment():
    cfg = from_env(FULL)
    assert cfg == S3Config(
        endpoint="minio.example.ts.net",
        bucket="openbb",
        access="someaccesskey",
        secret="somesecretkey",
        port=9000,
        secure=True,
    )


def test_uri_matches_the_verified_arcticdb_shape():
    assert from_env(FULL).uri == (
        "s3s://minio.example.ts.net:openbb"
        "?port=9000&access=someaccesskey&secret=somesecretkey"
        "&use_virtual_addressing=false"
    )


def test_plain_scheme_when_insecure():
    assert from_env({**FULL, "ARCTICDB_S3_SECURE": "false"}).uri.startswith("s3://")


def test_credentials_are_url_encoded():
    assert "secret=a%2Fb%26c" in from_env({**FULL, "ARCTICDB_S3_SECRET": "a/b&c"}).uri


def test_missing_variables_are_all_named_at_once():
    with pytest.raises(ConfigError) as exc:
        from_env({"ARCTICDB_S3_ENDPOINT": "minio.example.ts.net"})
    message = str(exc.value)
    for name in ("ARCTICDB_S3_BUCKET", "ARCTICDB_S3_ACCESS", "ARCTICDB_S3_SECRET"):
        assert name in message


def test_blank_values_count_as_missing():
    with pytest.raises(ConfigError, match="ARCTICDB_S3_SECRET"):
        from_env({**FULL, "ARCTICDB_S3_SECRET": "   "})


def test_rejects_non_numeric_port():
    with pytest.raises(ConfigError, match="ARCTICDB_S3_PORT"):
        from_env({**FULL, "ARCTICDB_S3_PORT": "nine"})


def test_rejects_unparseable_secure_flag():
    with pytest.raises(ConfigError, match="ARCTICDB_S3_SECURE"):
        from_env({**FULL, "ARCTICDB_S3_SECURE": "maybe"})


def test_secret_is_not_leaked_by_repr():
    assert "somesecretkey" not in repr(from_env(FULL))
