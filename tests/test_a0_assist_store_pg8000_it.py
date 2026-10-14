"""INTEGRATION test of the spend store against a real Postgres over TLS with the real pg8000 driver. Skipped
unless PRAVRUDHI_A0_PG_IT=1 and pg8000 and docker are available.

Proves, on a throwaway local postgres:17 container with OUR OWN CA: SCRAM-SHA-256 login works; TLS is verified (a wrong
CA is refused, a wrong host name is refused, sslmode=disable is refused before any connection); a bounded timeout
cuts a slow statement; NO named prepared statement is left on a connection after repeated calls (the pooler in
transaction mode would break named ones); the DSN, the password and the host never appear in an error or a log
record."""

from __future__ import annotations

import datetime as dt
import ipaddress
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("PRAVRUDHI_A0_PG_IT") != "1" or shutil.which("docker") is None,
    reason="integration: set PRAVRUDHI_A0_PG_IT=1 (needs docker and pg8000)",
)
pytest.importorskip("pg8000")

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

from pravrudhi.application.a0_assist import store as ST  # noqa: E402

NAME, PORT, ROLE, PW = "trackc-a0-store-it", 55434, "assist_it_role", "it-" + os.urandom(6).hex()


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _ca(cn):
    k = _key()
    now = dt.datetime.now(dt.UTC)
    c = (
        x509.CertificateBuilder()
        .subject_name(_name(cn))
        .issuer_name(_name(cn))
        .public_key(k.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(k.public_key()), critical=False)
        .sign(k, hashes.SHA256())
    )  # Python 3.13 verifies in STRICT mode: SKI and key usage are required
    return k, c


def _server(ca_key, ca_cert, sans):
    k = _key()
    now = dt.datetime.now(dt.UTC)
    c = (
        x509.CertificateBuilder()
        .subject_name(_name("localhost"))
        .issuer_name(ca_cert.subject)
        .public_key(k.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    return k, c


def _pem(path: Path, obj, key=False):
    path.write_bytes(
        obj.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
        )
        if key
        else obj.public_bytes(serialization.Encoding.PEM)
    )


def sh(*a, **k):
    return subprocess.run(list(a), capture_output=True, text=True, timeout=120, **k)


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    d = tmp_path_factory.mktemp("pgtls")
    ca_k, ca = _ca("A0 test CA")
    other_k, other = _ca("Some other CA")
    good_k, good = _server(ca_k, ca, [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))])
    _pem(d / "ca.crt", ca)
    _pem(d / "other.crt", other)
    _pem(d / "server.crt", good)
    _pem(d / "server.key", good_k, key=True)
    sh("docker", "rm", "-f", NAME)
    r = sh(
        "docker",
        "run",
        "-d",
        "--name",
        NAME,
        "--memory",
        "1g",
        "-p",
        f"127.0.0.1:{PORT}:5432",
        "-v",
        f"{d}:/certs:ro",
        "-e",
        "POSTGRES_PASSWORD=" + os.urandom(6).hex(),
        "--user",
        "root",
        "--entrypoint",
        "bash",
        "postgres:17.6",
        "-c",
        "mkdir -p /var/lib/postgresql/ssl && cp /certs/server.crt /certs/server.key /var/lib/postgresql/ssl/ "
        "&& chown -R postgres:postgres /var/lib/postgresql/ssl && chmod 600 /var/lib/postgresql/ssl/server.key && "
        "exec docker-entrypoint.sh postgres -c ssl=on -c ssl_cert_file=/var/lib/postgresql/ssl/server.crt "
        "-c ssl_key_file=/var/lib/postgresql/ssl/server.key",
    )
    assert r.returncode == 0, r.stderr
    for _ in range(90):
        time.sleep(1)
        if (
            sh("docker", "exec", NAME, "pg_isready", "-U", "postgres").returncode == 0
            and sh("docker", "exec", NAME, "psql", "-U", "postgres", "-Atc", "select 1").stdout.strip() == "1"
        ):
            break
    sh("docker", "exec", NAME, "psql", "-U", "postgres", "-c", f"create role {ROLE} login password '{PW}'")
    yield {"dir": d, "ca": str(d / "ca.crt"), "other": str(d / "other.crt")}
    sh("docker", "rm", "-f", "-v", NAME)


def dsn(host="127.0.0.1", mode="verify-full"):
    return f"postgresql://{ROLE}:{PW}@{host}:{PORT}/postgres?sslmode={mode}"


def test_scram_over_verified_tls_works(env):
    s = ST.PgSpendStore(dsn(), timeout_s=5, ca_file=env["ca"])
    rows = s._call("select ssl, version from pg_stat_ssl where pid = pg_backend_pid()", ())
    assert rows[0][0] is True and rows[0][1].startswith("TLS")
    enc = sh(
        "docker",
        "exec",
        NAME,
        "psql",
        "-U",
        "postgres",
        "-Atc",
        f"select rolpassword like 'SCRAM-SHA-256$%' from pg_authid where rolname = '{ROLE}'",
    ).stdout.strip()
    assert enc == "t"  # the stored verifier is SCRAM: the login above used SCRAM-SHA-256


def test_a_wrong_ca_is_refused(env):
    with pytest.raises(ST.StoreError):
        ST.PgSpendStore(dsn(), timeout_s=5, ca_file=env["other"])._call("select 1", ())


def test_the_system_bundle_does_not_trust_our_test_ca():
    with pytest.raises(ST.StoreError):
        ST.PgSpendStore(dsn(), timeout_s=5)._call("select 1", ())


def test_a_wrong_host_name_is_refused(env):
    # the certificate names localhost and 127.0.0.1; connect by an address it does NOT name (the container's other loopback alias)
    with pytest.raises(ST.StoreError):
        ST.PgSpendStore(
            f"postgresql://{ROLE}:{PW}@127.0.0.2:{PORT}/postgres?sslmode=verify-full", timeout_s=5, ca_file=env["ca"]
        )._call("select 1", ())


def test_sslmode_disable_is_refused_before_any_connection():
    with pytest.raises(ST.StoreNotConfigured):
        ST.PgSpendStore(dsn(mode="disable"), timeout_s=5)


def test_a_slow_statement_is_cut_by_the_bounded_timeout(env):
    s = ST.PgSpendStore(dsn(), timeout_s=1, ca_file=env["ca"])
    t0 = time.time()
    with pytest.raises(ST.StoreError):
        s._call("select pg_sleep(6)", ())
    assert time.time() - t0 < 5


def test_no_named_prepared_statement_survives_repeated_calls(env):
    s = ST.PgSpendStore(dsn(), timeout_s=5, ca_file=env["ca"])
    conn = s._connect()
    try:
        cur = conn.cursor()
        for _ in range(8):
            cur.execute("select %s::int + %s::int", (1, 2))
            cur.fetchall()
        cur.execute("select count(*) from pg_prepared_statements")
        assert cur.fetchall()[0][0] == 0
    finally:
        conn.close()


def test_the_dsn_password_and_host_never_appear_in_errors_or_logs(env, caplog):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ST.StoreError) as e:
        ST.PgSpendStore(dsn(), timeout_s=5, ca_file=env["other"])._call("select 1", ())
    blob = str(e.value) + " ".join(r.getMessage() for r in caplog.records)
    assert PW not in blob and ROLE not in blob and "127.0.0.1" not in blob


def test_a_wrong_password_is_a_store_error_without_the_password(env):
    with pytest.raises(ST.StoreError) as e:
        ST.PgSpendStore(dsn().replace(PW, "wrong-password"), timeout_s=5, ca_file=env["ca"])._call("select 1", ())
    assert "wrong-password" not in str(e.value) and PW not in str(e.value)
