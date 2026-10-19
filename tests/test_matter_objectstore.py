"""A2 matter store in an object store (fallback to the network volume): the replication layer, the S3 client and the wiring.
Invented text only; both arms of every rule; an in-memory object store with the real conditional-write rules stands in for S3."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application import matter_objectstore as O
from pravrudhi.application.matter_spans import MatterError, MatterNotFound, MatterStore, tenant_dir_name
from pravrudhi.application.matter_spans_config import MatterSpansConfig

TENANT, OTHER = "org:acme", "org:zorbland"
PAPER = (
    "1. The complainant paid a deposit on 3 March.\n\n2. The statutory notice was sent on 4 May.\fPage two holds the NEEDLEWORD."
)
NEEDLE = "NEEDLEWORD"


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


def worker(tmp_path: Path, name: str, objects: O.Objects, clock: Clock | None = None, **kw: Any) -> O.ObjectBackedMatterStore:
    c = clock or Clock()
    return O.ObjectBackedMatterStore(tmp_path / name, MatterSpansConfig(**kw), objects, "pfx", sleep=c.sleep, clock=c)


@pytest.fixture
def objs() -> O.MemoryObjects:
    return O.MemoryObjects()


def keys(objs: O.MemoryObjects) -> list[str]:
    return [k for k, _ in objs.list("pfx/") if "/_probe/" not in k]


# ---- the signature ----


def test_sigv4_matches_the_published_aws_example() -> None:
    """The GET Object example of the AWS Signature V4 documentation (header form, with a Range header)."""
    h = O.sign_v4(
        "GET", "examplebucket.s3.amazonaws.com", "/test.txt", {}, {"Range": "bytes=0-9"}, O._sha256(b""),
        access_key="AKIAIOSFODNN7EXAMPLE", secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", region="us-east-1",
        now=datetime(2013, 5, 24, tzinfo=UTC),
    )  # fmt: skip
    assert "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41" in h["Authorization"]
    assert "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date" in h["Authorization"]


# ---- settings ----


def test_the_settings_need_an_endpoint_and_a_bucket_and_read_only_the_environment() -> None:
    assert O.object_store_settings({}) is None
    assert O.object_store_settings({O.ENDPOINT_ENV: "https://s3.example.test"}) is None
    s = O.object_store_settings({O.ENDPOINT_ENV: "https://s3.example.test/", O.BUCKET_ENV: "b", O.PREFIX_ENV: "/x/y/"})
    assert s == O.ObjectStoreSettings("https://s3.example.test", "b", "x/y", "us-east-1")


def test_a_store_with_no_credentials_or_a_bad_endpoint_answers_503_to_every_call(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    for env in ({O.ACCESS_KEY_ENV: "", O.SECRET_KEY_ENV: ""}, {O.ACCESS_KEY_ENV: "k", O.SECRET_KEY_ENV: "s"}):
        st = O.ObjectStoreSettings("https://s3.example.test" if env[O.ACCESS_KEY_ENV] == "" else "ftp://nowhere", "b")
        store = O.make_store(tmp_path / "w", MatterSpansConfig(), st, env)
        with pytest.raises(MatterError) as e:
            store.create_matter(TENANT)
        assert e.value.code == "store_unavailable" and e.value.status_code == 503
    assert "s3.example.test" not in str(e.value) and "secret" not in caplog.text.lower()


# ---- the S3 client, with the network replaced ----


class FakeResp:
    def __init__(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
        self.status, self._b, self.headers = status, body, headers or {}

    def read(self) -> bytes:
        return self._b

    def __enter__(self) -> FakeResp:
        return self

    def __exit__(self, *a: Any) -> None:
        return None


def client(monkeypatch: pytest.MonkeyPatch, replies: list[Any], seen: list[Any]) -> O.S3Objects:
    def fake(req: Any, timeout: float = 0) -> Any:
        seen.append(req)
        r = replies.pop(0)
        if isinstance(r, int):
            raise urllib.error.HTTPError(req.full_url, r, "x", {}, __import__("io").BytesIO(b""))  # type: ignore[arg-type]
        return r

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    env = {O.ACCESS_KEY_ENV: "AKIAFAKEKEY", O.SECRET_KEY_ENV: "fakesecretvalue123"}
    return O.S3Objects(O.ObjectStoreSettings("https://s3.example.test", "bkt"), env, lambda: datetime(2026, 10, 19, tzinfo=UTC))


def test_a_put_carries_the_condition_and_a_signature_and_never_the_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []
    c = client(monkeypatch, [FakeResp(200, headers={"ETag": '"abc"'}), 412, 409, 500], seen)
    assert c.put("pfx/a b/HEAD", b"{}", if_none_match=True) == '"abc"'
    r = seen[0]
    assert (
        r.get_method() == "PUT"
        and r.get_header("If-none-match") == "*"
        and r.full_url == "https://s3.example.test/bkt/pfx/a%20b/HEAD"
    )
    assert r.get_header("Authorization").startswith("AWS4-HMAC-SHA256 Credential=AKIAFAKEKEY/20261019/us-east-1/s3/aws4_request")
    assert "fakesecretvalue123" not in str(r.headers) + r.full_url
    with pytest.raises(O.Precondition):
        c.put("k", b"x", if_match='"abc"')
    with pytest.raises(O.Precondition):
        c.put("k", b"x", if_none_match=True)
    with pytest.raises(O.ObjectStoreError) as e:
        c.put("k", b"x")
    assert "fakesecret" not in str(e.value) and "s3.example.test" not in str(e.value)
    assert seen[1].get_header("If-match") == '"abc"'


def test_get_delete_and_a_paged_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []
    page1 = b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>true</IsTruncated><NextContinuationToken>T1</NextContinuationToken><Contents><Key>pfx/a/HEAD</Key><Size>5</Size></Contents></ListBucketResult>'
    page2 = b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>false</IsTruncated><Contents><Key>pfx/b/HEAD</Key><Size>7</Size></Contents></ListBucketResult>'
    c = client(
        monkeypatch,
        [404, FakeResp(200, b"data", {"ETag": "e1"}), FakeResp(204), 404, FakeResp(200, page1), FakeResp(200, page2), 403],
        seen,
    )
    assert c.get("missing") is None and c.get("k") == (b"data", "e1")
    c.delete("k")
    c.delete("gone")  # a 404 on delete is fine
    assert c.list("pfx/") == [("pfx/a/HEAD", 5), ("pfx/b/HEAD", 7)]
    assert "continuation-token=T1" in seen[5].full_url and "list-type=2" in seen[4].full_url
    with pytest.raises(O.ObjectStoreError):
        c.get("denied")


def test_an_unreachable_service_is_an_object_store_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(req: Any, timeout: float = 0) -> Any:
        raise urllib.error.URLError("no route to s3.example.test")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    c = O.S3Objects(O.ObjectStoreSettings("https://s3.example.test", "b"), {O.ACCESS_KEY_ENV: "k", O.SECRET_KEY_ENV: "s"})
    with pytest.raises(O.ObjectStoreError) as e:
        c.get("k")
    assert "s3.example.test" not in str(e.value)


# ---- the store: a cold worker finds everything ----


def test_a_second_cold_worker_finds_the_matter_the_first_one_wrote(tmp_path: Path, objs: O.MemoryObjects) -> None:
    a, b = worker(tmp_path, "a", objs), worker(tmp_path, "b", objs)
    m = a.create_matter(TENANT)
    doc = a.add_document(TENANT, m, PAPER, "paper.txt")
    assert b.list_documents(TENANT, m)["documents"][0]["doc_id"] == doc["doc_id"]
    assert b.query(TENANT, m, NEEDLE)["found"] is True
    docs, spans = b.read_passages(TENANT, m)
    assert docs and any(NEEDLE in s["text"] for s in spans)
    assert b.check_quote(TENANT, m, doc["doc_id"], spans[0]["span_id"], spans[0]["text"][:30])["ok"] is True


def test_writes_from_two_workers_interleave_without_losing_either(tmp_path: Path, objs: O.MemoryObjects) -> None:
    a, b = worker(tmp_path, "a", objs), worker(tmp_path, "b", objs)
    m = a.create_matter(TENANT)
    a.add_document(TENANT, m, "alpha paper says ALPHAWORD.", "one")
    b.add_document(TENANT, m, "beta paper says BETAWORD.", "two")
    a.add_document(TENANT, m, "gamma paper says GAMMAWORD.", "three")
    for w in (a, b):
        assert len(w.list_documents(TENANT, m)["documents"]) == 3
        assert all(w.query(TENANT, m, t)["found"] for t in ("ALPHAWORD", "BETAWORD", "GAMMAWORD"))
    head = json.loads(objs.get(f"pfx/{tenant_dir_name(TENANT)}/{m}/HEAD")[0])  # type: ignore[index]
    assert head["seq"] == 4  # the create and three writes


def test_snapshots_have_unique_names_and_only_the_previous_one_is_kept(tmp_path: Path, objs: O.MemoryObjects) -> None:
    a = worker(tmp_path, "a", objs)
    m = a.create_matter(TENANT)
    for i in range(4):
        a.add_document(TENANT, m, f"paper number {i} here.", f"p{i}")
    snaps = [k for k in keys(objs) if "/snapshot-" in k]
    assert len(snaps) == 2 and len(set(snaps)) == 2 and all(k.rsplit("-", 1)[-1].endswith(".sqlite3") for k in snaps)
    assert not [k for k in keys(objs) if k.endswith(".tmp")]


def test_another_tenant_finds_nothing_and_the_answer_is_the_same_as_for_no_such_matter(
    tmp_path: Path, objs: O.MemoryObjects
) -> None:
    a, b = worker(tmp_path, "a", objs), worker(tmp_path, "b", objs)
    m = a.create_matter(TENANT)
    a.add_document(TENANT, m, PAPER, "paper")
    for call in (b.list_documents, b.query):
        args = (OTHER, m) if call == b.list_documents else (OTHER, m, NEEDLE)
        with pytest.raises(MatterNotFound):
            call(*args)
    with pytest.raises(MatterNotFound):
        b.list_documents(OTHER, "0" * 32)
    with pytest.raises(MatterNotFound):
        b.list_documents(TENANT, "../" + m)
    assert not (tmp_path / "b" / tenant_dir_name(OTHER) / m).exists()


# ---- deletion ----


def test_deleting_a_document_leaves_its_text_in_no_object_and_no_local_file(tmp_path: Path, objs: O.MemoryObjects) -> None:
    a, b = worker(tmp_path, "a", objs), worker(tmp_path, "b", objs)
    m = a.create_matter(TENANT)
    a.add_document(TENANT, m, "keep this FIRSTPAPER text.", "one")
    secret = a.add_document(TENANT, m, f"remove this {NEEDLE} text.", "two")["doc_id"]
    a.add_document(TENANT, m, "keep that THIRDPAPER text.", "three")
    assert any(NEEDLE.encode() in objs.get(k)[0] for k in keys(objs) if "/snapshot-" in k)  # type: ignore[index]
    b.delete_document(TENANT, m, secret)
    for k in keys(objs):
        assert NEEDLE.encode() not in objs.get(k)[0], k  # type: ignore[index]
    assert b.query(TENANT, m, NEEDLE)["found"] is False and a.query(TENANT, m, "THIRDPAPER")["found"] is True
    assert len([k for k in keys(objs) if "/snapshot-" in k]) == 1


def test_deleting_a_matter_removes_every_object_first_the_pointer(tmp_path: Path, objs: O.MemoryObjects) -> None:
    a, b = worker(tmp_path, "a", objs), worker(tmp_path, "b", objs)
    m = a.create_matter(TENANT)
    a.add_document(TENANT, m, PAPER, "paper")
    keep = b.create_matter(TENANT)
    b.delete_matter(TENANT, m)
    assert [k for k in keys(objs) if f"/{m}/" in k] == []
    with pytest.raises(MatterNotFound):
        a.query(TENANT, m, NEEDLE)  # a's local copy is stale: the missing pointer decides, and the stale copy goes
    assert not (tmp_path / "a" / tenant_dir_name(TENANT) / m).exists()
    assert a.list_documents(TENANT, keep)["documents"] == []


def test_deleting_a_tenant_removes_every_matter_of_it_and_none_of_another(tmp_path: Path, objs: O.MemoryObjects) -> None:
    a, b = worker(tmp_path, "a", objs), worker(tmp_path, "b", objs)
    m1, m2 = a.create_matter(TENANT), a.create_matter(TENANT)
    a.add_document(TENANT, m1, PAPER, "paper")
    other = a.create_matter(OTHER)
    out = b.delete_tenant(TENANT)
    assert out["matters"] >= 2
    assert [k for k in keys(objs) if tenant_dir_name(TENANT) in k] == []
    assert a.list_documents(OTHER, other)["documents"] == []
    for m in (m1, m2):
        with pytest.raises(MatterNotFound):
            b.list_documents(TENANT, m)


# ---- failure: never a half-written matter ----


class Flaky(O.MemoryObjects):
    def __init__(self) -> None:
        super().__init__()
        self.fail_on: str | None = None

    def put(self, key: str, data: bytes, *, if_none_match: bool = False, if_match: str | None = None) -> str:
        if self.fail_on and key.endswith(self.fail_on):
            raise O.ObjectStoreError("down")
        return super().put(key, data, if_none_match=if_none_match, if_match=if_match)


def test_a_failed_write_answers_503_leaves_the_last_good_snapshot_and_drops_the_local_copy(tmp_path: Path) -> None:
    objs = Flaky()
    a = worker(tmp_path, "a", objs)
    m = a.create_matter(TENANT)
    a.add_document(TENANT, m, "first paper FIRSTWORD.", "one")
    objs.fail_on = "/HEAD"
    with pytest.raises(MatterError) as e:
        a.add_document(TENANT, m, "second paper SECONDWORD.", "two")
    assert e.value.code == "store_unavailable" and e.value.status_code == 503
    assert not (tmp_path / "a" / tenant_dir_name(TENANT) / m).exists()
    objs.fail_on = None
    docs = a.list_documents(TENANT, m)["documents"]
    assert len(docs) == 1 and a.query(TENANT, m, "SECONDWORD")["found"] is False
    a.add_document(TENANT, m, "second paper SECONDWORD.", "two")
    assert len(a.list_documents(TENANT, m)["documents"]) == 2
    assert [k for k in keys(objs) if k.endswith("/LOCK")] == []


def test_a_failed_create_leaves_nothing_behind(tmp_path: Path) -> None:
    objs = Flaky()
    a = worker(tmp_path, "a", objs)
    objs.fail_on = "/HEAD"
    with pytest.raises(MatterError):
        a.create_matter(TENANT)
    objs.fail_on = None
    assert not any(p.is_dir() for p in (tmp_path / "a" / tenant_dir_name(TENANT)).iterdir())
    assert a.create_matter(TENANT)


def test_a_pointer_that_does_not_match_its_snapshot_is_never_opened(tmp_path: Path, objs: O.MemoryObjects) -> None:
    a, b = worker(tmp_path, "a", objs), worker(tmp_path, "b", objs)
    m = a.create_matter(TENANT)
    a.add_document(TENANT, m, PAPER, "paper")
    mp = f"pfx/{tenant_dir_name(TENANT)}/{m}/"
    snap = next(k for k in keys(objs) if "/snapshot-" in k and objs.get(mp + "HEAD")[0].find(k.rsplit("/", 1)[-1].encode()) != -1)  # type: ignore[index]
    objs.put(snap, b"not a database")
    with pytest.raises(MatterError) as e:
        b.list_documents(TENANT, m)
    assert e.value.status_code == 503 and not (tmp_path / "b" / tenant_dir_name(TENANT) / m / "matter.sqlite3").exists()


def test_the_caps_still_apply_the_matter_count_on_the_remote_objects(tmp_path: Path, objs: O.MemoryObjects) -> None:
    a, b = worker(tmp_path, "a", objs, max_matters_per_tenant=2), worker(tmp_path, "b", objs, max_matters_per_tenant=2)
    a.create_matter(TENANT)
    b.create_matter(TENANT)
    with pytest.raises(MatterError) as e:
        a.create_matter(TENANT)  # a has one local folder; the two remote pointers decide
    assert e.value.code == "too_many_matters"
    assert a.create_matter(OTHER)


# ---- the lock ----


def test_a_held_lock_makes_a_writer_wait_then_answer_503_and_nothing_is_written(tmp_path: Path, objs: O.MemoryObjects) -> None:
    clock = Clock()
    a = worker(tmp_path, "a", objs, clock)
    m = a.create_matter(TENANT)
    lock = f"pfx/{tenant_dir_name(TENANT)}/{m}/LOCK"
    objs.put(lock, json.dumps({"owner": "someone-else", "expires": clock() + 10_000}).encode())
    t0 = clock()
    with pytest.raises(MatterError) as e:
        a.add_document(TENANT, m, PAPER, "paper")
    assert e.value.code == "store_busy" and e.value.status_code == 503 and clock() - t0 >= O.LOCK_WAIT_S
    assert json.loads(objs.get(lock)[0])["owner"] == "someone-else"  # type: ignore[index]  # not ours: left alone
    assert a.list_documents(TENANT, m)["documents"] == []


def test_an_expired_lock_is_taken_over_and_a_lock_lost_mid_write_fails_the_write(tmp_path: Path, objs: O.MemoryObjects) -> None:
    clock = Clock()
    a = worker(tmp_path, "a", objs, clock)
    m = a.create_matter(TENANT)
    lock = f"pfx/{tenant_dir_name(TENANT)}/{m}/LOCK"
    objs.put(lock, json.dumps({"owner": "dead-worker", "expires": clock() - 1}).encode())
    a.add_document(TENANT, m, PAPER, "paper")
    assert len(a.list_documents(TENANT, m)["documents"]) == 1 and objs.get(lock) is None
    real = a._current

    def steal(tenant: str, matter_id: str) -> Any:
        out = real(tenant, matter_id)
        objs.put(
            lock, json.dumps({"owner": "thief", "expires": clock() + 1000}).encode()
        )  # the lock changes hands during the write
        return out

    a._current = steal  # type: ignore[method-assign]
    with pytest.raises(MatterError) as e:
        a.add_document(TENANT, m, "second paper.", "two")
    assert e.value.code == "store_busy"
    a._current = real  # type: ignore[method-assign]
    objs.delete(lock)
    assert len(a.list_documents(TENANT, m)["documents"]) == 1


def test_three_workers_writing_at_once_lose_nothing(tmp_path: Path, objs: O.MemoryObjects) -> None:
    ws = [O.ObjectBackedMatterStore(tmp_path / f"w{i}", MatterSpansConfig(), objs, "pfx") for i in range(3)]
    m = ws[0].create_matter(TENANT)
    errors: list[Exception] = []

    def run(i: int) -> None:
        for j in range(4):
            try:
                ws[i].add_document(TENANT, m, f"worker {i} paper {j} says W{i}X{j}Z.", f"p{i}{j}")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

    ts = [threading.Thread(target=run, args=(i,)) for i in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert errors == []
    for w in ws:
        assert len(w.list_documents(TENANT, m)["documents"]) == 12
        assert w.query(TENANT, m, "W2X3Z")["found"] is True
    assert json.loads(objs.get(f"pfx/{tenant_dir_name(TENANT)}/{m}/HEAD")[0])["seq"] == 13  # type: ignore[index]
    assert [k for k in keys(objs) if k.endswith("/LOCK")] == []


# ---- the conditional-write probe ----


def test_a_service_that_ignores_conditions_is_not_used(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    a = worker(tmp_path, "a", O.MemoryObjects(conditional=False))
    with pytest.raises(MatterError) as e:
        a.create_matter(TENANT)
    assert e.value.code == "store_unavailable" and e.value.status_code == 503
    with pytest.raises(MatterError):
        a.list_documents(TENANT, "0" * 32)
    assert "ignores conditional writes" in caplog.text


def test_the_probe_object_is_removed_and_an_unreachable_service_is_probed_again_later(tmp_path: Path) -> None:
    objs = Flaky()
    a = worker(tmp_path, "a", objs)
    objs.fail_on = "_probe"  # never matches a key suffix: use the first put instead
    real_put = objs.put
    calls = {"n": 0}

    def put(key: str, data: bytes, *, if_none_match: bool = False, if_match: str | None = None) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise O.ObjectStoreError("down")
        return real_put(key, data, if_none_match=if_none_match, if_match=if_match)

    objs.put = put  # type: ignore[method-assign]
    with pytest.raises(MatterError) as e:
        a.create_matter(TENANT)
    assert e.value.status_code == 503
    assert a.create_matter(TENANT)  # the service is back: the probe ran again, not remembered as failed
    assert [k for k in objs.list("pfx/") if "/_probe/" in k[0]] == []


# ---- the wiring ----


def test_with_no_object_store_in_the_environment_the_plain_disk_store_is_used(tmp_path: Path) -> None:
    assert O.object_store_settings({}) is None
    plain = MatterStore(tmp_path / "p", MatterSpansConfig())
    assert not isinstance(plain, O.ObjectBackedMatterStore)
