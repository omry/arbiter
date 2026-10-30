"""Real authority migration, issuer administration and fresh-v1 integrity."""

import asyncio
import base64
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
from uuid import UUID, uuid4

import pytest

from arbiter_server import authority as a, credentials as c, generations as g
from arbiter_server import local_control, owner_issuers as issuers, prepared
from arbiter_server.enrollment_activation import activate
from arbiter_server.enrollment_receipt import result_fields
from arbiter_server.main import main
from server.tests.unit.test_pairing_proofs import proof_setup

pytestmark = pytest.mark.usefixtures("private_windows_test_parent")


def setup(tmp_path):
    root = tmp_path / "authority"
    record = a.initialize_authority(root, recovery_file=tmp_path / "operator.json")
    selected = g._control(root / g.ACTIVE)
    assert selected is not None
    return root, record, g._database(root, selected["active"])


def certificate(owner=None, now=None):
    return c.create_issuer(
        c.Identity(c.Role.OWNER, owner or str(uuid4())), now=now
    ).certificate_der


def register(root, der):
    return issuers.control(
        root, "register", {"certificate_der": base64.b64encode(der).decode()}
    )


def commit(root):
    _, approval, _, _, _, now = proof_setup()
    raw = prepared.prepare(root, approval, int(now.timestamp()) + 300)
    activate(root, approval, raw, set, lambda: None, now)
    return approval, raw, now


def downgrade(path):
    with closing(sqlite3.connect(path)) as db:
        db.execute("DROP TABLE owner_issuers")
        db.execute("DROP TABLE agent_enrollments")
        db.execute("PRAGMA user_version=3")
        db.commit()


def test_exact_issuer_revision_tombstone_and_independent_same_owner(tmp_path):
    root, record, path = setup(tmp_path)
    der = certificate(record.owner_id)
    fp = hashlib.sha256(der).hexdigest()
    with a.running_instance(root):
        first = register(root, der)
        assert first == register(root, der)
        assert (
            first["issuer_sha256"] == fp and first["revision"] == first["active"] == 1
        )
        rows = issuers.control(root, "list", {})["issuers"]
        assert isinstance(rows, list)
        assert len(rows) == 2 and {row["owner_id"] for row in rows} == {record.owner_id}
        assert len({row["issuer_sha256"] for row in rows}) == 2
        exported = issuers.control(root, "export", {"issuer_sha256": fp})
        encoded = exported["certificate_der"]
        assert isinstance(encoded, str) and base64.b64decode(encoded) == der
        removed = issuers.control(root, "remove", {"issuer_sha256": fp})
        assert removed["active"] == 0 and removed["revision"] == 2
        assert issuers.control(root, "remove", {"issuer_sha256": fp}) == removed
    before = path.read_bytes()
    assert a.authority_status(root) == record
    assert path.read_bytes() == before
    with a.running_instance(root):
        assert issuers.control(root, "export", {"issuer_sha256": fp})["active"] == 0
        revived = register(root, der)
        assert revived["revision"] == 3 and revived["active"] == 1
        assert register(root, der) == revived


@pytest.mark.parametrize(
    "invalid",
    [
        "der",
        "oversize",
        "base64",
        "noncanonical",
        "wrong-role",
        "expired",
        "future",
        "extra",
    ],
)
def test_invalid_registration_preserves_database(tmp_path, invalid):
    root, _, path = setup(tmp_path)
    now = datetime.now(timezone.utc)
    der = certificate(
        now=(
            now + timedelta(days=1)
            if invalid == "future"
            else now - timedelta(days=4000) if invalid == "expired" else now
        )
    )
    if invalid == "wrong-role":
        der = c.create_issuer(
            c.Identity(c.Role.SERVER_AUTHORITY, str(uuid4()))
        ).certificate_der
    if invalid in {"der", "oversize"}:
        der = b"x" * (4097 if invalid == "oversize" else 10)
    encoded = base64.b64encode(der).decode()
    args: dict[str, object] = {
        "certificate_der": (
            "!"
            if invalid == "base64"
            else encoded + "\n" if invalid == "noncanonical" else encoded
        )
    }
    if invalid == "extra":
        args["path"] = "/server/private-file"
    with a.running_instance(root):
        before = path.read_bytes()
        with pytest.raises(ValueError):
            issuers.control(root, "register", args)
        assert path.read_bytes() == before


def test_bounded_pages_and_strict_local_arguments(tmp_path):
    root, _, _ = setup(tmp_path)
    with a.running_instance(root):
        for _ in range(33):
            register(root, certificate())
        page = issuers.control(root, "list", {})
        assert isinstance(page["issuers"], list)
        assert len(page["issuers"]) == 32 and page["next_cursor"]
        tail = issuers.control(root, "list", {"cursor": page["next_cursor"]})
        assert isinstance(tail["issuers"], list)
        rows = page["issuers"] + tail["issuers"]
        assert len(rows) == len({row["issuer_sha256"] for row in rows}) == 34
        assert tail["next_cursor"] is None
        assert all(
            set(row) == {"issuer_sha256", "owner_id", "active", "revision"}
            for row in rows
        )
        invalid_arguments: list[tuple[str, dict[str, object]]] = [
            ("list", {"cursor": "A" * 64}),
            ("remove", {"issuer_sha256": True}),
            ("register", {"certificate_file": "private"}),
            ("replace", {}),
        ]
        for command, arguments in invalid_arguments:
            with pytest.raises(ValueError):
                issuers.control(root, command, arguments)
        dispatch = local_control.AuthorityControl(root)
        with pytest.raises(a.AuthorityError):
            dispatch.dispatch(
                {
                    "root": str(root / "other"),
                    "action": "owner-issuer",
                    "command": "list",
                    "arguments": {},
                }
            )


def test_upgrade_backfills_real_results_without_mutating_history(tmp_path):
    root, record, path = setup(tmp_path)
    results = [commit(root)[1] for _ in range(3)]
    with closing(sqlite3.connect(path)) as db:
        db.execute(
            "UPDATE enrollments SET denied=1 WHERE enrollment_id=?",
            (result_fields(results[1])[0][3],),
        )
        db.execute(
            "UPDATE enrollments SET inactive_after=1 WHERE enrollment_id=?",
            (result_fields(results[2])[0][3],),
        )
        db.commit()
        before = db.execute(
            "SELECT * FROM enrollments ORDER BY enrollment_id"
        ).fetchall()
        credentials = db.execute("SELECT * FROM credentials").fetchall()
    downgrade(path)
    with pytest.raises(a.AuthorityError, match="schema"):
        a._read_record(path)
    selected = g._control(root / g.ACTIVE)
    assert selected is not None
    g._write_control(
        root,
        g._INTENT,
        {"version": 1, "active": selected["active"], "sha256": g._digest(path)},
    )
    assert a.authority_status(root) == record
    assert not (root / g._INTENT).exists()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (4,)
        assert (
            db.execute("SELECT * FROM enrollments ORDER BY enrollment_id").fetchall()
            == before
        )
        assert db.execute("SELECT * FROM credentials").fetchall() == credentials
        assert set(db.execute("SELECT * FROM agent_enrollments")) == {
            (result_fields(raw)[0][2], result_fields(raw)[0][3]) for raw in results
        }
    unchanged = path.read_bytes()
    assert a.authority_status(root) == record and path.read_bytes() == unchanged
    _, raw, _ = commit(root)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT COUNT(*) FROM agent_enrollments").fetchone() == (4,)
        assert db.execute(
            "SELECT prepared FROM enrollments WHERE enrollment_id=?",
            (result_fields(raw)[0][3],),
        ).fetchone() == (raw,)


@pytest.mark.parametrize(
    "damage", ["malformed", "server", "enrollment", "duplicate", "interrupted"]
)
def test_failed_upgrade_is_atomic_and_preserves_old_database(
    tmp_path, monkeypatch, damage
):
    root, record, path = setup(tmp_path)
    first, second = commit(root)[1], commit(root)[1]
    ids, _ = result_fields(second)
    if damage != "interrupted":
        replacement = (
            b"broken"
            if damage == "malformed"
            else (
                second.replace(ids[0].encode(), str(uuid4()).encode(), 1)
                if damage == "server"
                else (
                    second.replace(ids[3].encode(), str(uuid4()).encode(), 1)
                    if damage == "enrollment"
                    else second.replace(
                        ids[2].encode(), result_fields(first)[0][2].encode(), 1
                    )
                )
            )
        )
        with closing(sqlite3.connect(path)) as db:
            db.execute(
                "UPDATE enrollments SET prepared=? WHERE enrollment_id=?",
                (replacement, ids[3]),
            )
            db.commit()
    downgrade(path)
    original = issuers.create_tables
    if damage == "interrupted":

        def fail(db, authority):
            original(db, authority)
            raise OSError("synthetic failure after schema writes")

        monkeypatch.setattr(issuers, "create_tables", fail)
    before = path.read_bytes()
    expected: tuple[type[Exception], ...] = (ValueError, sqlite3.Error, OSError)
    with pytest.raises(expected):
        a.authority_status(root)
    assert path.read_bytes() == before
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (3,)
        assert (
            db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='agent_enrollments'"
            ).fetchone()
            is None
        )
    if damage == "interrupted":
        monkeypatch.setattr(issuers, "create_tables", original)
        assert a.authority_status(root) == record


def test_empty_upgrade_creates_no_synthetic_enrollment(tmp_path):
    root, record, path = setup(tmp_path)
    downgrade(path)
    assert a.authority_status(root) == record
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT * FROM agent_enrollments").fetchall() == []
        assert (
            db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='enrollments'"
            ).fetchone()
            is None
        )


@pytest.mark.parametrize("state", ["active", "denied", "inactive"])
def test_fresh_v1_cannot_duplicate_existing_agent(tmp_path, monkeypatch, state):
    root, _, path = setup(tmp_path)
    _, raw, _ = commit(root)
    ids, _ = result_fields(raw)
    with closing(sqlite3.connect(path)) as db:
        if state != "active":
            db.execute(
                "UPDATE enrollments SET "
                + ("denied=1" if state == "denied" else "inactive_after=1")
            )
            db.commit()
    values = iter([UUID(ids[2]), uuid4()])
    monkeypatch.setattr(prepared, "uuid4", lambda: next(values))
    with pytest.raises(sqlite3.IntegrityError):
        commit(root)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT * FROM agent_enrollments").fetchall() == [
            (ids[2], ids[3])
        ]
        assert db.execute("SELECT COUNT(*) FROM enrollments").fetchone() == (1,)


def test_exact_replay_does_not_repair_missing_binding_or_reactivate_issuer(tmp_path):
    root, record, path = setup(tmp_path)
    approval, raw, now = commit(root)
    with a.running_instance(root):
        rows = issuers.control(root, "list", {})["issuers"]
        assert isinstance(rows, list)
        local = next(row for row in rows if row["owner_id"] == record.owner_id)
        issuers.control(root, "remove", {"issuer_sha256": local["issuer_sha256"]})
        assert activate(root, approval, raw, set, lambda: None, now) == raw
        with pytest.raises(ValueError, match="issuer unavailable"):
            commit(root)
    with closing(sqlite3.connect(path)) as db:
        db.execute("DELETE FROM agent_enrollments")
        db.commit()
    before = path.read_bytes()
    with pytest.raises(ValueError, match="binding unavailable"):
        activate(root, approval, raw, set, lambda: None, now)
    assert path.read_bytes() == before


def test_real_native_control_and_cli_public_export(tmp_path, capsysbinary):
    root, _, _ = setup(tmp_path)
    der = certificate()
    path = tmp_path / "public.der"
    path.write_bytes(der)
    fp = hashlib.sha256(der).hexdigest()

    async def exercise():
        with a.running_instance(root):
            async with local_control.running(root):
                prefix = ["authority", "owner-issuer"]

                async def command(action, *args):
                    return await asyncio.to_thread(
                        main, prefix + [action, "--state-dir", str(root), *args]
                    )

                assert (
                    await command("register", "--certificate-file", str(path), "--json")
                    == 0
                )
                reply = json.loads(capsysbinary.readouterr().out)
                assert reply["issuer_sha256"] == fp and reply["revision"] == 1
                assert await command("export", "--issuer-sha256", fp) == 0
                assert capsysbinary.readouterr().out == der
                assert await command("remove", "--issuer-sha256", fp) == 0
                assert json.loads(capsysbinary.readouterr().out)["active"] == 0
                assert await command("list", "--json") == 0
                assert len(json.loads(capsysbinary.readouterr().out)["issuers"]) == 2

    asyncio.run(exercise())


def test_stopped_cli_and_oversize_input_preserve_state(tmp_path, capsys):
    root, _, path = setup(tmp_path)
    prefix = ["authority", "owner-issuer"]
    assert main(prefix + ["list", "--state-dir", str(root), "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["issuers"]) == 1
    public = tmp_path / "oversize.der"
    public.write_bytes(b"x" * 4097)
    before = path.read_bytes()
    assert (
        main(
            prefix
            + ["register", "--state-dir", str(root), "--certificate-file", str(public)]
        )
        == 1
    )
    assert path.read_bytes() == before
