"""A2 deletion: DELETE + optimize + VACUUM + every file of the matter removed. The test that counts rows is not enough (the bytes
can stay in the file), so each test SCANS EVERY FILE under the store, and the temp folder the run used, for the deleted secret.
Invented text only."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pravrudhi.application import matter_spans as M
from pravrudhi.application.matter_spans import MatterError, MatterStore
from pravrudhi.application.matter_spans_config import MatterSpansConfig

TENANT, OTHER = "org:acme", "org:zorbland"
NEEDLE = "ZORBLATT-NEEDLE-PHRASE-7781"
KEEP = "QUILLFEATHER-KEEP-PHRASE-4402"


def _text(secret: str) -> str:
    head = f"1. An ordinary paragraph about a cheque.\n\n2. The notice was sent on 4 May. {secret} appears in this paragraph.\n\n"
    return head + "3. Filler prose so that the file holds more than one page of data. " * 40


def holders(root: Path, needle: str) -> list[str]:
    """Every file under `root` whose bytes contain `needle` (UTF-8 and UTF-16LE spellings), however it is named."""
    out = []
    for p in root.rglob("*"):
        if p.is_file():
            data = p.read_bytes()
            if needle.encode() in data or needle.encode("utf-16-le") in data:
                out.append(str(p))
    return out


@pytest.fixture
def store(tmp_path: Path) -> MatterStore:
    return MatterStore(tmp_path / "store", MatterSpansConfig())


def test_the_secret_is_really_in_the_file_before_the_delete(store: MatterStore) -> None:
    """The scan can fail: it finds the secret while the document is stored (so 'absent after' means something)."""
    m = store.create_matter(TENANT)
    store.add_document(TENANT, m, _text(NEEDLE), "paper")
    assert holders(store.base, NEEDLE)


def test_deleting_one_document_removes_its_bytes_from_the_file_and_keeps_the_other_document(store: MatterStore) -> None:
    m = store.create_matter(TENANT)
    gone = store.add_document(TENANT, m, _text(NEEDLE), "gone")["doc_id"]
    store.add_document(TENANT, m, _text(KEEP), "kept")
    assert holders(store.base, NEEDLE)
    out = store.delete_document(TENANT, m, gone)
    assert out["deleted"] is True
    assert holders(store.base, NEEDLE) == []
    assert store.query(TENANT, m, "zorblatt needle")["found"] is False
    assert store.query(TENANT, m, "quillfeather keep")["found"] is True
    assert [d["name"] for d in store.list_documents(TENANT, m)["documents"]] == ["kept"]


def test_deleting_a_matter_leaves_no_file_and_no_bytes(store: MatterStore) -> None:
    m = store.create_matter(TENANT)
    store.add_document(TENANT, m, _text(NEEDLE), "paper")
    out = store.delete_matter(TENANT, m)
    assert out["deleted"] is True and out["documents"] == 1
    assert holders(store.base, NEEDLE) == []
    assert [p for p in store.base.rglob("*")] == []  # the matter folder and the empty tenant folder are gone


def test_leftover_files_beside_the_database_are_removed_and_scanned_too(store: MatterStore) -> None:
    """A journal, -wal, -shm, a temp extraction file, an upload and a thumbnail that hold the secret: all gone."""
    m = store.create_matter(TENANT)
    store.add_document(TENANT, m, _text(NEEDLE), "paper")
    mdir = store._matter_dir(TENANT, m)
    for name in ("matter.sqlite3-wal", "matter.sqlite3-shm", "matter.sqlite3-journal", "extract.tmp", "upload.pdf", "thumb.png"):
        (mdir / name).write_bytes(b"x" + NEEDLE.encode() + b"y")
    (mdir / "uploads").mkdir()
    (mdir / "uploads" / "original.pdf").write_bytes(NEEDLE.encode())
    assert len(holders(store.base, NEEDLE)) >= 7
    store.delete_matter(TENANT, m)
    assert holders(store.base, NEEDLE) == [] and list(store.base.rglob("*")) == []


def test_other_matters_of_the_same_tenant_and_other_tenants_are_untouched(store: MatterStore) -> None:
    keep_m = store.create_matter(TENANT)
    store.add_document(TENANT, keep_m, _text(KEEP), "kept")
    other_m = store.create_matter(OTHER)
    store.add_document(OTHER, other_m, _text(KEEP), "other")
    gone_m = store.create_matter(TENANT)
    store.add_document(TENANT, gone_m, _text(NEEDLE), "gone")
    store.delete_matter(TENANT, gone_m)
    assert holders(store.base, NEEDLE) == []
    assert store.query(TENANT, keep_m, "quillfeather")["found"] and store.query(OTHER, other_m, "quillfeather")["found"]
    assert len(holders(store.base, KEEP)) == 2


def test_deleting_a_tenant_removes_all_its_matters_and_nothing_else(store: MatterStore) -> None:
    ms = [store.create_matter(TENANT) for _ in range(3)]
    for m in ms:
        store.add_document(TENANT, m, _text(NEEDLE), "paper")
    om = store.create_matter(OTHER)
    store.add_document(OTHER, om, _text(KEEP), "kept")
    out = store.delete_tenant(TENANT)
    assert out["matters"] == 3 and holders(store.base, NEEDLE) == []
    assert store.query(OTHER, om, "quillfeather")["found"]
    assert store.delete_tenant("org:never-existed")["matters"] == 0


def test_a_second_delete_is_not_found_not_a_crash(store: MatterStore) -> None:
    m = store.create_matter(TENANT)
    d = store.add_document(TENANT, m, _text(NEEDLE), "paper")["doc_id"]
    store.delete_document(TENANT, m, d)
    with pytest.raises(MatterError) as e:
        store.delete_document(TENANT, m, d)
    assert e.value.code == "document_not_found"
    store.delete_matter(TENANT, m)
    with pytest.raises(MatterError) as e2:
        store.delete_matter(TENANT, m)
    assert e2.value.code == "matter_not_found"


def test_the_removal_refuses_a_path_outside_the_store(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    with pytest.raises(MatterError) as e:
        M._remove_tree(outside, tmp_path / "store")
    assert e.value.code == "bad_path" and (outside / "keep.txt").exists()
    base = tmp_path / "store"
    base.mkdir()
    with pytest.raises(MatterError):
        M._remove_tree(base, base)


def test_a_symbolic_link_inside_a_matter_folder_is_not_followed_out_of_the_store(store: MatterStore, tmp_path: Path) -> None:
    m = store.create_matter(TENANT)
    store.add_document(TENANT, m, _text(NEEDLE), "paper")
    outside = tmp_path / "outside.txt"
    outside.write_text("do not delete me")
    os.symlink(outside, store._matter_dir(TENANT, m) / "link")
    store.delete_matter(TENANT, m)
    assert outside.read_text() == "do not delete me" and list(store.base.rglob("*")) == []


def test_the_delete_needs_no_flag_and_no_service_through_the_command_line(
    store: MatterStore, capsys: pytest.CaptureFixture[str]
) -> None:
    m = store.create_matter(TENANT)
    d = store.add_document(TENANT, m, _text(NEEDLE), "paper")["doc_id"]
    assert M.main(["delete-document", "--tenant", TENANT, "--matter", m, "--doc", d, "--store", str(store.base)]) == 0
    assert holders(store.base, NEEDLE) == []
    d2 = store.add_document(TENANT, m, _text(NEEDLE), "again")["doc_id"]
    assert d2 and M.main(["delete-matter", "--tenant", TENANT, "--matter", m, "--store", str(store.base)]) == 0
    assert list(store.base.rglob("*")) == []
    assert M.main(["delete-matter", "--tenant", TENANT, "--matter", m, "--store", str(store.base)]) == 2
    assert "matter_not_found" in capsys.readouterr().err
    m2 = store.create_matter(OTHER)
    store.add_document(OTHER, m2, _text(NEEDLE), "paper")
    assert M.main(["delete-tenant", "--tenant", OTHER, "--store", str(store.base)]) == 0
    assert holders(store.base, NEEDLE) == []


def test_the_messages_never_claim_more_than_deletion_from_the_store(store: MatterStore) -> None:
    m = store.create_matter(TENANT)
    d = store.add_document(TENANT, m, _text(NEEDLE), "paper")["doc_id"]
    outs = [store.delete_document(TENANT, m, d), store.delete_matter(TENANT, m), store.delete_tenant(TENANT)]
    text = " ".join(str(o) for o in outs).lower()
    banned = ("erased", "irrecoverable", "unrecoverable", "permanently", "forever", "wiped", "destroyed", "cannot be recovered")
    for word in banned:
        assert word not in text, word
    assert all("deleted from this service's store" in o["message"] for o in outs)
    src = Path(M.__file__).read_text().lower()
    for word in ("irrecoverable", "unrecoverable", "cannot be recovered"):
        assert word not in src.replace("no claim of irrecoverability", "")


# ---- A2 amendment 2: each deletion step is necessary on its own (mutation-proved) ----------------------------------------------
# What the experiment showed (needle = the verbatim text, token = its lowercase FTS5 token): without `optimize` the token stays
# in an FTS5 segment; without VACUUM the file keeps its size; without secure_delete a plain DELETE leaves the verbatim bytes.
# VACUUM and secure_delete each hide the other's gap in a bytes scan, so each has its own test on its own effect.

TOKEN = "zorblatt"


def _db(store: MatterStore, m: str) -> Path:
    return store._matter_dir(TENANT, m) / M.DB_NAME


def test_the_lowercase_fts_token_of_a_deleted_document_is_gone_from_the_file(store: MatterStore) -> None:
    m = store.create_matter(TENANT)
    gone = store.add_document(TENANT, m, _text(NEEDLE), "gone")["doc_id"]
    store.add_document(TENANT, m, _text(KEEP), "kept")
    assert holders(store.base, TOKEN)  # the scan can fail: the token is in the index while the document is stored
    store.delete_document(TENANT, m, gone)
    assert holders(store.base, TOKEN) == [] and holders(store.base, NEEDLE) == []  # needs FTS5 `optimize`


def test_vacuum_gives_the_pages_back_so_the_file_shrinks_after_a_delete(store: MatterStore) -> None:
    m = store.create_matter(TENANT)
    gone = store.add_document(TENANT, m, _text(NEEDLE) * 5, "gone")["doc_id"]
    store.add_document(TENANT, m, _text(KEEP), "kept")
    before = _db(store, m).stat().st_size
    store.delete_document(TENANT, m, gone)
    assert _db(store, m).stat().st_size < before * 0.6  # a file that only marks pages free keeps its size


def test_secure_delete_is_on_for_every_connection_and_blanks_a_plain_delete(store: MatterStore) -> None:
    m = store.create_matter(TENANT)
    store.add_document(TENANT, m, _text(NEEDLE), "paper")
    with store._open(_db(store, m)) as conn:
        assert conn.execute("PRAGMA secure_delete").fetchone()[0] == 1
        conn.execute("DELETE FROM span")  # no VACUUM, no optimize: only secure_delete stands between the rows and the file
        conn.execute("DELETE FROM span_fts")  # (FTS5 keeps its own copy of each span's text in its content table)
    assert holders(store.base, NEEDLE) == []


def test_each_of_the_three_steps_is_in_the_delete_path(store: MatterStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """The statements a document delete runs, seen from the connection: optimize, VACUUM and the secure_delete pragma."""
    seen: list[str] = []
    real_open = store._open

    from contextlib import contextmanager

    @contextmanager
    def spy(path: Path):  # type: ignore[no-untyped-def]
        with real_open(path) as conn:
            conn.set_trace_callback(seen.append)
            yield conn

    monkeypatch.setattr(store, "_open", spy)
    m = store.create_matter(TENANT)
    d = store.add_document(TENANT, m, _text(NEEDLE), "paper")["doc_id"]
    seen.clear()
    store.delete_document(TENANT, m, d)
    joined = "\n".join(seen)
    assert "'optimize'" in joined and "VACUUM" in joined


# ---- A2 amendment 3: delete_tenant never reports deleted while text is left; the lock re-checks the folder -------------------


def test_delete_tenant_removes_a_folder_that_has_no_database_and_counts_it(store: MatterStore) -> None:
    ms = [store.create_matter(TENANT) for _ in range(3)]
    for m in ms:
        store.add_document(TENANT, m, _text(NEEDLE), "paper")
    tdir = store._tenant_dir(TENANT)
    bare = tdir / ("0" * 32)  # sorts first; no matter.sqlite3 (a failed create or a manual edit), but a journal holds text
    bare.mkdir()
    (bare / "matter.sqlite3-journal").write_bytes(b"x" + NEEDLE.encode() + b"y")
    (tdir / "stray-folder").mkdir()
    (tdir / "stray-folder" / "upload.pdf").write_bytes(NEEDLE.encode())
    assert len(holders(store.base, NEEDLE)) == 5
    out = store.delete_tenant(TENANT)
    assert out["deleted"] is True and out["matters"] == 3 and out["folders_without_database"] == 2
    assert holders(store.base, NEEDLE) == [] and list(store.base.rglob("*")) == []


def test_delete_tenant_of_a_tenant_with_no_folder_is_a_no_op(store: MatterStore) -> None:
    out = store.delete_tenant("org:never-existed")
    assert (out["matters"], out["folders_without_database"]) == (0, 0)


def test_a_matter_not_found_inside_the_body_is_never_swallowed(store: MatterStore, monkeypatch: pytest.MonkeyPatch) -> None:
    m = store.create_matter(TENANT)
    store.add_document(TENANT, m, _text(NEEDLE), "paper")

    def boom(self: MatterStore, tenant: str, matter_id: str) -> int:
        raise M.MatterNotFound()

    monkeypatch.setattr(MatterStore, "_delete_matter_locked", boom)
    with pytest.raises(M.MatterNotFound):
        store.delete_tenant(TENANT)


def test_the_tenant_lock_locks_the_new_folder_when_the_old_one_was_replaced_while_waiting(
    store: MatterStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.create_matter(TENANT)
    tdir = store._tenant_dir(TENANT)
    real, calls = M.fcntl.flock, [0]

    def swap_first(fd: int, op: int) -> None:
        real(fd, op)
        calls[0] += 1
        if calls[0] == 1:  # while we "waited", the folder was removed and made again
            os.rename(tdir, tdir.with_name(tdir.name + "-old"))
            tdir.mkdir(mode=0o700)

    monkeypatch.setattr(M.fcntl, "flock", swap_first)
    with store._tenant_lock(TENANT):
        assert calls[0] == 2  # the first lock was on the vanished folder: dropped, and the new folder locked


def test_symlinks_in_a_tenant_folder_are_unlinked_never_followed_and_counted(store: MatterStore, tmp_path: Path) -> None:
    ms = [store.create_matter(TENANT) for _ in range(2)]
    for m in ms:
        store.add_document(TENANT, m, _text(NEEDLE), "paper")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_bytes(b"outside " + NEEDLE.encode())
    (outside / "matter.sqlite3").write_bytes(b"not ours")
    tdir = store._tenant_dir(TENANT)
    os.symlink(outside, tdir / ("0" * 32))  # a link to a folder with a database-shaped file, named like a matter
    os.symlink(outside / "keep.txt", tdir / "link-to-file")
    os.symlink(tmp_path / "nowhere", tdir / "dangling")
    out = store.delete_tenant(TENANT)
    assert out["deleted"] is True and out["matters"] == 2 and out["symlinks_removed"] == 3
    assert holders(store.base, NEEDLE) == [] and list(store.base.rglob("*")) == []
    assert (outside / "keep.txt").read_bytes() == b"outside " + NEEDLE.encode()
    assert (outside / "matter.sqlite3").read_bytes() == b"not ours"


def test_a_symlinked_matter_folder_is_unlinked_and_reads_as_not_found(store: MatterStore, tmp_path: Path) -> None:
    store.create_matter(TENANT)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "matter.sqlite3").write_bytes(b"not ours")
    link = store._tenant_dir(TENANT) / ("1" * 32)
    os.symlink(outside, link)
    with pytest.raises(M.MatterNotFound):
        store.delete_matter(TENANT, "1" * 32)
    assert not link.is_symlink() and (outside / "matter.sqlite3").read_bytes() == b"not ours"


def test_delete_tenant_never_scrubs_a_database_that_is_a_link_to_another_store(store: MatterStore, tmp_path: Path) -> None:
    other = MatterStore(tmp_path / "other-store", MatterSpansConfig())
    om = other.create_matter(OTHER)
    other.add_document(OTHER, om, _text(KEEP), "theirs")
    their_db = other._matter_dir(OTHER, om) / M.DB_NAME
    store.create_matter(TENANT)
    folder = store._tenant_dir(TENANT) / ("2" * 32)  # named like a matter; its database is a link out of the store
    folder.mkdir()
    os.symlink(their_db, folder / M.DB_NAME)
    out = store.delete_tenant(TENANT)
    assert out["deleted"] is True and out["symlinks_removed"] == 1 and list(store.base.rglob("*")) == []
    assert their_db.is_file() and [d["name"] for d in other.list_documents(OTHER, om)["documents"]] == ["theirs"]
    assert other.query(OTHER, om, "quillfeather keep")["found"] is True and holders(other.base, KEEP)


def _outside_matter(tmp_path: Path) -> tuple[MatterStore, str, Path]:
    other = MatterStore(tmp_path / "other-store", MatterSpansConfig())
    om = other.create_matter(OTHER)
    other.add_document(OTHER, om, _text(KEEP), "theirs")
    return other, om, other._matter_dir(OTHER, om) / M.DB_NAME


def _theirs_intact(other: MatterStore, om: str) -> bool:
    docs = other.list_documents(OTHER, om)["documents"]
    return [d["name"] for d in docs] == ["theirs"] and bool(holders(other.base, KEEP))


def test_a_symlinked_matter_database_is_never_opened_by_a_query_or_a_delete(store: MatterStore, tmp_path: Path) -> None:
    other, om, their_db = _outside_matter(tmp_path)
    store.create_matter(TENANT)
    mid = "3" * 32
    folder = store._tenant_dir(TENANT) / mid
    folder.mkdir()
    os.symlink(their_db, folder / M.DB_NAME)
    for call in (
        lambda: store.query(TENANT, mid, "quillfeather"),
        lambda: store.list_documents(TENANT, mid),
        lambda: store.add_document(TENANT, mid, _text(NEEDLE), "x"),
        lambda: store.delete_document(TENANT, mid, "a" * 32),
    ):
        with pytest.raises(M.MatterNotFound):
            call()
    assert _theirs_intact(other, om)
    with pytest.raises(M.MatterNotFound):
        store.delete_matter(TENANT, mid)  # nothing opened or scrubbed; the folder with the link is removed
    assert not folder.exists() and _theirs_intact(other, om)


def test_a_symlinked_tenant_folder_is_refused_before_anything_is_touched(store: MatterStore, tmp_path: Path) -> None:
    other, om, _ = _outside_matter(tmp_path)
    store.base.mkdir(parents=True, exist_ok=True)
    os.symlink(other._tenant_dir(OTHER), store.base / M.tenant_dir_name(OTHER))
    for call in (
        lambda: store.query(OTHER, om, "quillfeather"),
        lambda: store.delete_matter(OTHER, om),
        lambda: store.delete_tenant(OTHER),
        lambda: store.create_matter(OTHER),
        lambda: store.add_document(OTHER, om, _text(NEEDLE), "x"),
        lambda: store.delete_document(OTHER, om, "a" * 32),
    ):
        with pytest.raises(MatterError) as e:
            call()
        assert e.value.code == "bad_path"
    assert _theirs_intact(other, om) and holders(other.base, NEEDLE) == []


def test_a_tenant_folder_that_resolves_outside_the_store_without_being_a_symlink_is_refused(
    store: MatterStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second half of `_tenant_dir`: a folder that is NOT a symlink but resolves outside the store (a Windows junction or a
    bind mount in real life). POSIX offers no such folder without privileges, so `Path.resolve` is monkeypatched to report it:
    this tests the guard's logic, not the operating system."""
    store.create_matter(TENANT)
    tdir = store.base / M.tenant_dir_name(TENANT)
    assert tdir.exists() and not tdir.is_symlink()
    outside = tmp_path / "elsewhere" / tdir.name
    outside.mkdir(parents=True)
    real_resolve = Path.resolve

    def resolve(self: Path, *a: object, **k: object) -> Path:
        return outside if self == tdir else real_resolve(self, *a, **k)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(MatterError) as err:
        store._tenant_dir(TENANT)
    assert err.value.code == "bad_path"
    with pytest.raises(MatterError):
        store.create_matter(TENANT)
    monkeypatch.undo()
    assert store._tenant_dir(TENANT) == tdir  # control: the same folder is accepted when it really resolves inside the store
