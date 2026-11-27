import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from arbiter_server.errors import OperationError
from arbiter_server.storage import PluginStorage
from arbiter_whatsapp.credentials import BindingStore
from arbiter_whatsapp.state import StateStore, reference_key


@pytest.fixture(autouse=True)
def operator_prepared_windows_parent(tmp_path):
    if os.name == "nt":
        from arbiter_server.file_protection.windows import _windows_file_security

        owner = _windows_file_security(tmp_path).owner_sid
        assert owner is not None
        subprocess.run(
            [
                "icacls",
                str(tmp_path),
                "/inheritance:r",
                "/remove:g",
                "*S-1-3-4",
                "/grant:r",
                f"*{owner}:(OI)(CI)F",
                "*S-1-5-18:(OI)(CI)F",
                "*S-1-5-32-544:(OI)(CI)F",
            ],
            check=True,
            capture_output=True,
        )


@pytest.fixture
def store(tmp_path):
    state = StateStore(PluginStorage(plugin_name="whatsapp", root=tmp_path))
    state.root(create=True)
    state.directory("bindings", create=True)
    state.directory("locks", create=True)
    with state.lock("root-binding"):
        with state.database(initialize=True):
            pass
    return BindingStore(state)


def test_binding_is_complete_and_private_across_processes(store):
    binding = store.publish(
        "whatsapp/first", "provider-1", "synthetic-token", previous=None
    )
    assert binding == store.snapshot("whatsapp/first")
    assert "synthetic-token" not in repr(binding)
    assert "provider-1" not in repr(binding)
    script = """
import sys
from pathlib import Path
from arbiter_server.storage import PluginStorage
from arbiter_whatsapp.state import StateStore
from arbiter_whatsapp.credentials import BindingStore
s=BindingStore(StateStore(PluginStorage(plugin_name='whatsapp',root=Path(sys.argv[1]))))
b=s.snapshot('whatsapp/first')
assert b.provider_id=='provider-1' and b.token=='synthetic-token'
print('complete')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(store.state.path.parent)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "complete\n"
    if os.name != "nt":
        assert store.state.path.stat().st_mode & 0o777 == 0o700
        for path in store.state.path.rglob("*"):
            assert path.stat().st_mode & 0o077 == 0


def test_duplicate_identity_and_stale_replacement_preserve_bytes(store):
    original = store.publish("whatsapp/first", "provider-1", "old-token", previous=None)
    path = store.state.path / "bindings" / (reference_key("whatsapp/first") + ".json")
    before = path.read_bytes()
    with pytest.raises(OperationError) as error:
        store.publish("whatsapp/second", "provider-1", "new-token", previous=None)
    assert error.value.domain_code == "provider_already_bound"
    with pytest.raises(OperationError) as error:
        store.publish("whatsapp/first", "provider-1", "new-token", previous=None)
    assert error.value.domain_code == "binding_changed"
    assert path.read_bytes() == before
    replacement = store.publish(
        "whatsapp/first", "provider-1", "new-token", previous=original
    )
    assert replacement.store_uuid == original.store_uuid


def test_missing_database_cannot_reinitialize_active_binding(store):
    store.publish("whatsapp/first", "provider-1", "synthetic", previous=None)
    (store.state.path / "state.sqlite").unlink()
    with pytest.raises(OperationError):
        with store.state.database(initialize=True):
            pytest.fail("reconstructed state")
    assert not (store.state.path / "state.sqlite").exists()


def test_mismatched_uuid_and_unknown_version_fail_closed(store):
    store.publish("whatsapp/first", "provider-1", "synthetic", previous=None)
    with store.state.database() as db:
        db.execute("UPDATE store SET store_uuid='another-store'")
    with pytest.raises(OperationError):
        store.snapshot("whatsapp/first")
    with store.state.database() as db:
        db.execute("PRAGMA user_version=999")
    with pytest.raises(OperationError):
        with store.state.database():
            pytest.fail("accepted unsupported schema")


@pytest.mark.parametrize("table", ["replies", "sources"])
def test_missing_safety_table_fails_closed_without_recreation(store, table):
    store.publish("whatsapp/first", "provider-1", "synthetic", previous=None)
    path = store.state.path / "state.sqlite"
    with store.state.database() as db:
        db.execute(f"DROP TABLE {table}")
    damaged = path.read_bytes()

    with pytest.raises(OperationError):
        store.snapshot("whatsapp/first")
    assert path.read_bytes() == damaged
    with pytest.raises(OperationError):
        with store.state.database(initialize=True):
            pytest.fail("recreated damaged state")
    assert path.read_bytes() == damaged


def test_missing_v1_table_constraint_fails_closed_and_preserves_bytes(store):
    store.publish("whatsapp/first", "provider-1", "synthetic", previous=None)
    path = store.state.path / "state.sqlite"
    with store.state.database() as db:
        store_uuid = db.execute("SELECT store_uuid FROM store").fetchone()[0]
        db.execute(
            "CREATE TABLE replacement_store "
            "(singleton INTEGER PRIMARY KEY, store_uuid TEXT NOT NULL)"
        )
        db.execute("INSERT INTO replacement_store VALUES(1, ?)", (store_uuid,))
        db.execute("DROP TABLE store")
        db.execute("ALTER TABLE replacement_store RENAME TO store")
    damaged = path.read_bytes()

    with pytest.raises(OperationError):
        store.snapshot("whatsapp/first")
    assert path.read_bytes() == damaged


@pytest.mark.parametrize(
    "damage", ["oversize", "unknown-field", "version", "identity", "token"]
)
def test_tampered_binding_is_unavailable_and_preserved(store, damage):
    store.publish("whatsapp/first", "provider-1", "synthetic", previous=None)
    path = store.state.path / "bindings" / (reference_key("whatsapp/first") + ".json")
    data = json.loads(path.read_text())
    if damage == "unknown-field":
        data["extra"] = True
    elif damage == "version":
        data["schema_version"] = True
    elif damage == "identity":
        data["provider_id"] = "\x00"
    elif damage == "token":
        data["token"] = "\r\n"
    raw = b"x" * 16385 if damage == "oversize" else json.dumps(data).encode()
    path.write_bytes(raw)
    with pytest.raises(OperationError):
        store.snapshot("whatsapp/first")
    assert path.read_bytes() == raw


def test_publication_error_retains_inspectable_evidence(store, monkeypatch):
    import arbiter_whatsapp.credentials as credentials

    original = credentials._publish

    def uncertain(*args):
        original(*args)
        raise OSError("synthetic-sensitive-exception")

    monkeypatch.setattr(credentials, "_publish", uncertain)
    with pytest.raises(OperationError) as error:
        store.publish("whatsapp/first", "provider-1", "synthetic", previous=None)
    assert error.value.domain_code == "binding_publication_uncertain"
    assert "synthetic-sensitive-exception" not in str(error.value)
    assert store.snapshot("whatsapp/first").provider_id == "provider-1"


@pytest.mark.skipif(os.name == "nt", reason="POSIX exact modes")
def test_unsafe_existing_root_and_lock_are_not_repaired(store):
    store.state.path.chmod(0o755)
    with pytest.raises(OperationError):
        store.state.root()
    assert store.state.path.stat().st_mode & 0o777 == 0o755
    store.state.path.chmod(0o700)
    path = store.state.path / "locks" / "unsafe"
    path.write_text("")
    path.chmod(0o644)
    with pytest.raises(OperationError):
        with store.state.lock("unsafe"):
            pytest.fail("unsafe lock acquired")
    assert path.stat().st_mode & 0o777 == 0o644


def test_unexpected_sqlite_sidecar_and_uninitialized_store_fail_closed(store):
    path = store.state.path / "state.sqlite-wal"
    path.touch(mode=0o600)
    with pytest.raises(OperationError):
        with store.state.database():
            pytest.fail("unexpected journal accepted")
    path.unlink()
    with store.state.database() as db:
        db.execute("DELETE FROM store")
    with pytest.raises(OperationError):
        with store.state.database():
            pytest.fail("missing store identity accepted")


def test_binding_replacement_between_open_and_inspection_is_rejected(
    store, monkeypatch
):
    import arbiter_whatsapp.credentials as credentials

    store.publish("whatsapp/first", "provider-1", "synthetic", previous=None)
    path = store.state.path / "bindings" / (reference_key("whatsapp/first") + ".json")
    original_open = credentials.os.open

    def raced_open(candidate, *args, **kwargs):
        fd = original_open(candidate, *args, **kwargs)
        if candidate == path:
            original = path.read_bytes()
            path.rename(path.with_name("old-record"))
            path.write_bytes(original)
            path.chmod(0o600)
        return fd

    monkeypatch.setattr(credentials.os, "open", raced_open)
    with pytest.raises(OperationError):
        store.snapshot("whatsapp/first")


def test_duplicate_or_unrecognized_committed_records_fail_closed(store):
    store.publish("whatsapp/first", "provider-1", "synthetic", previous=None)
    directory = store.state.path / "bindings"
    original = directory / (reference_key("whatsapp/first") + ".json")
    extra = directory / (reference_key("whatsapp/alias") + ".json")
    extra.write_bytes(original.read_bytes())
    extra.chmod(0o600)
    with pytest.raises(OperationError) as error:
        store.bound_identities()
    assert error.value.domain_code == "duplicate_provider_identity"
    extra.unlink()
    extra = directory / "unknown"
    extra.touch(mode=0o600)
    with pytest.raises(OperationError):
        store.bound_identities()


def test_missing_agent_or_binding_is_unavailable_and_large_record_cannot_activate(
    store,
):
    with pytest.raises(OperationError) as error:
        store.snapshot("whatsapp/absent")
    assert error.value.domain_code == "account_not_enrolled"
    with pytest.raises(OperationError):
        store.publish("whatsapp/large", "provider-1", "\\" * 8192, previous=None)
    assert store.optional("whatsapp/large") is None
    store.publish("whatsapp/first", "provider-1", "synthetic", previous=None)
    with store.state.database() as db:
        db.execute("DELETE FROM agents")
    with pytest.raises(OperationError):
        store.snapshot("whatsapp/first")


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode and link checks")
@pytest.mark.parametrize("unsafe", ["symlink", "hardlink", "public", "executable"])
def test_unsafe_binding_is_never_repaired_silently(store, tmp_path, unsafe):
    store.publish("whatsapp/first", "provider-1", "synthetic", previous=None)
    path = store.state.path / "bindings" / (reference_key("whatsapp/first") + ".json")
    if unsafe == "symlink":
        target = tmp_path / "target"
        path.rename(target)
        path.symlink_to(target)
    elif unsafe == "hardlink":
        os.link(path, tmp_path / "extra-link")
    elif unsafe == "public":
        path.chmod(0o644)
    else:
        path.chmod(0o700)
    with pytest.raises(OperationError):
        store.snapshot("whatsapp/first")
    if unsafe == "public":
        assert path.stat().st_mode & 0o777 == 0o644


def test_persistent_lock_excludes_an_independent_process(store):
    script = """
import sys
from pathlib import Path
from arbiter_server.storage import PluginStorage
from arbiter_server.errors import OperationError
from arbiter_whatsapp.state import StateStore
s=StateStore(PluginStorage(plugin_name='whatsapp',root=Path(sys.argv[1])))
try:
 with s.lock('same.poll'): pass
except OperationError as e:
 assert e.code=='conflict'
 print('excluded')
else: raise AssertionError('concurrent lock acquired')
"""
    with store.state.lock("same.poll"):
        result = subprocess.run(
            [sys.executable, "-c", script, str(store.state.path.parent)],
            capture_output=True,
            text=True,
            timeout=15,
        )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "excluded\n"
    assert (store.state.path / "locks" / "same.poll").exists()
    with store.state.lock("same.poll"):
        pass


def test_concurrent_process_aliases_cannot_publish_the_same_identity(store, tmp_path):
    script = """
import sys,time
from pathlib import Path
from arbiter_server.storage import PluginStorage
from arbiter_server.errors import OperationError
from arbiter_whatsapp.state import StateStore
from arbiter_whatsapp.credentials import BindingStore
s=BindingStore(StateStore(PluginStorage(plugin_name='whatsapp',root=Path(sys.argv[1]))))
deadline=time.monotonic()+5
while not Path(sys.argv[3]).exists():
 if time.monotonic()>deadline: raise AssertionError('barrier timeout')
 time.sleep(0.005)
try:
 s.publish(sys.argv[2],'same-provider','synthetic',previous=None)
 print('published')
except OperationError as error:
 assert error.code=='conflict'
 print('excluded')
"""
    barrier = tmp_path / "start"
    children = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                str(store.state.path.parent),
                f"whatsapp/{name}",
                str(barrier),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for name in ("first", "second")
    ]
    barrier.touch()
    outputs = []
    for child in children:
        out, err = child.communicate(timeout=15)
        assert child.returncode == 0, err
        outputs.append(out.strip())
    assert sorted(outputs) == ["excluded", "published"]
    assert len(store.bound_identities()) == 1


@pytest.mark.parametrize("when", ["before", "after"])
def test_process_exit_at_binding_publication_boundary(store, when):
    script = """
import os,sys
from pathlib import Path
from arbiter_server.storage import PluginStorage
from arbiter_whatsapp.state import StateStore
from arbiter_whatsapp.credentials import BindingStore
import arbiter_whatsapp.credentials as c
s=BindingStore(StateStore(PluginStorage(plugin_name='whatsapp',root=Path(sys.argv[1]))))
original=c._publish
def crash(*args):
 if sys.argv[2]=='after': original(*args)
 os._exit(71)
c._publish=crash
s.publish('whatsapp/crash','provider-crash','synthetic',previous=None)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(store.state.path.parent), when],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 71
    if when == "after":
        assert store.snapshot("whatsapp/crash").provider_id == "provider-crash"
    else:
        assert store.optional("whatsapp/crash") is None
        # An inert complete pending record is retained, but never activates.
        assert not store.bound_identities()
        assert any(
            p.name.startswith(".pending-")
            for p in (store.state.path / "bindings").iterdir()
        )
        store.publish(
            "whatsapp/crash", "provider-crash", "fresh-proof-token", previous=None
        )
