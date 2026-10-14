"""The store's TLS context against a real handshake (in memory, no network): the certificate must chain to the configured
CA AND name the pooler host, and a different CA or a different host name is refused."""

from __future__ import annotations

import contextlib
import datetime as dt
import ssl
from pathlib import Path

import pytest

x509 = pytest.importorskip("cryptography.x509")
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

from pravrudhi.application.a0_assist import store as ST  # noqa: E402

POOLER = "aws-1-eu-west-1.pooler.supabase.com"


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _cert(subject_key, cn, issuer_key=None, issuer_name=None, *, ca=False, sans=()):
    now = dt.datetime.now(dt.UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    b = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(issuer_name or name)
        .public_key(subject_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if ca:
        b = b.add_extension(
            x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True
        ).add_extension(x509.SubjectKeyIdentifier.from_public_key(subject_key.public_key()), critical=False)
    else:
        b = b.add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key((issuer_key or subject_key).public_key()), critical=False
        ).add_extension(x509.SubjectAlternativeName([x509.DNSName(s) for s in sans]), critical=False)
    return b.sign(issuer_key or subject_key, hashes.SHA256())


def _pem(path: Path, obj, key=False):
    path.write_bytes(
        obj.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
        )
        if key
        else obj.public_bytes(serialization.Encoding.PEM)
    )


def _handshake(client_ctx: ssl.SSLContext, server_ctx: ssl.SSLContext, host: str) -> None:
    c_in, c_out, s_in, s_out = ssl.MemoryBIO(), ssl.MemoryBIO(), ssl.MemoryBIO(), ssl.MemoryBIO()
    c = client_ctx.wrap_bio(c_in, c_out, server_hostname=host)
    s = server_ctx.wrap_bio(s_in, s_out, server_side=True)
    for _ in range(20):
        for side, outb, other in ((c, c_out, s_in), (s, s_out, c_in)):
            with contextlib.suppress(ssl.SSLWantReadError):
                side.do_handshake()
            data = outb.read()
            if data:
                other.write(data)
    c.do_handshake()


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    d = tmp_path_factory.mktemp("tls")
    ca_k, other_k, srv_k = _key(), _key(), _key()
    ca = _cert(ca_k, "test-ca", ca=True)
    other = _cert(other_k, "other-ca", ca=True)
    srv = _cert(srv_k, "pooler", ca_k, ca.subject, sans=[POOLER])
    _pem(d / "ca.crt", ca)
    _pem(d / "other.crt", other)
    _pem(d / "srv.crt", srv)
    _pem(d / "srv.key", srv_k, key=True)
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(d / "srv.crt", d / "srv.key")
    return d, server


def test_the_pooler_host_name_with_the_right_ca_verifies(pki):
    d, server = pki
    _handshake(ST.tls_context(str(d / "ca.crt")), server, POOLER)


def test_a_different_host_name_is_refused_even_with_the_right_ca(pki):
    d, server = pki
    with pytest.raises(ssl.SSLCertVerificationError):
        _handshake(ST.tls_context(str(d / "ca.crt")), server, "aws-0-eu-west-1.pooler.supabase.com")


def test_a_different_ca_is_refused(pki):
    d, server = pki
    with pytest.raises(ssl.SSLCertVerificationError):
        _handshake(ST.tls_context(str(d / "other.crt")), server, POOLER)


def test_the_system_bundle_refuses_a_private_ca_certificate(pki):
    _, server = pki
    with pytest.raises(ssl.SSLCertVerificationError):
        _handshake(ST.tls_context(None), server, POOLER)


def _flags(ctx):
    return ctx.verify_flags, ctx.verify_mode, ctx.check_hostname


def test_with_a_ca_file_only_the_strict_flag_is_cleared_and_nothing_else_is_relaxed(pki):
    d, _ = pki
    ref = ssl.create_default_context()
    ctx = ST.tls_context(str(d / "ca.crt"))
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
    assert ctx.verify_flags == ref.verify_flags & ~ssl.VERIFY_X509_STRICT
    assert not ctx.verify_flags & ssl.VERIFY_X509_STRICT
    # the literal value: the interpreter's default flags (trusted-first, partial-chain) minus strict, nothing added or removed
    assert ctx.verify_flags == ssl.VERIFY_X509_TRUSTED_FIRST | ssl.VERIFY_X509_PARTIAL_CHAIN
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2
    # only the pinned root is trusted: adding the system bundle on top of it would raise this count
    assert len(ctx.get_ca_certs()) == (d / "ca.crt").read_text().count("BEGIN CERTIFICATE") == 1


def test_without_a_ca_file_the_default_strict_context_is_kept():
    ctx = ST.tls_context(None)
    assert _flags(ctx) == _flags(ssl.create_default_context())
    assert ctx.verify_flags & ssl.VERIFY_X509_STRICT


def test_a_root_without_key_usage_verifies_with_the_ca_file_but_not_under_the_strict_default(tmp_path):
    ca_k, srv_k = _key(), _key()
    now = dt.datetime.now(dt.UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "no-ku-root")])
    root = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(ca_k.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_k, hashes.SHA256())
    )
    srv = _cert(srv_k, "pooler", ca_k, root.subject, sans=[POOLER])
    _pem(tmp_path / "root.crt", root)
    _pem(tmp_path / "srv.crt", srv)
    _pem(tmp_path / "srv.key", srv_k, key=True)
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(tmp_path / "srv.crt", tmp_path / "srv.key")
    _handshake(ST.tls_context(str(tmp_path / "root.crt")), server, POOLER)
    strict = ssl.create_default_context(cafile=str(tmp_path / "root.crt"))
    with pytest.raises(ssl.SSLCertVerificationError):
        _handshake(strict, server, POOLER)
    with pytest.raises(ssl.SSLCertVerificationError):
        _handshake(ST.tls_context(str(tmp_path / "root.crt")), server, "aws-0-eu-west-1.pooler.supabase.com")
