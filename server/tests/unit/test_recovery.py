from __future__ import annotations

import base64
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest
from cryptography.hazmat.primitives import hashes, serialization as s
from cryptography.hazmat.primitives.asymmetric import ec, x25519

from arbiter_server import authority as a, recovery as r
from arbiter_server.main import main

pytestmark = pytest.mark.usefixtures("private_windows_test_parent")


def dbpath(root):
    selector = root / "active-generation.json"
    name = (
        json.loads(selector.read_text())["active"]
        if selector.exists()
        else ".generation.pending"
    )
    return root / "generations" / name / a.DATABASE


def initialize(tmp_path, name="state", *, reuse=False):
    return a.initialize_authority(
        tmp_path / name,
        recovery_file=tmp_path / "operator.json",
        reuse_recovery_file=reuse,
    )


def read(tmp_path):
    return json.loads((tmp_path / "operator.json").read_bytes())


def test_real_setup_separates_private_custody_and_public_trust(tmp_path, capsys):
    assert (
        a.authority_cli(
            "init",
            tmp_path / "state",
            as_json=True,
            recovery_file=tmp_path / "operator.json",
        )
        == 0
    )
    output = capsys.readouterr().out
    doc = read(tmp_path)
    assert next(iter(doc)) == "warning" and doc["warning"].startswith(
        "NEVER SHARE THIS"
    )
    record = a.authority_status(tmp_path / "state")
    assert record is not None and doc["owner_id"] == record.owner_id
    signer = s.load_der_private_key(
        base64.b64decode(doc["recovery_signer_pkcs8"]), None
    )
    decryptor = s.load_der_private_key(
        base64.b64decode(doc["backup_x25519_pkcs8"]), None
    )
    assert isinstance(signer, ec.EllipticCurvePrivateKey)
    assert isinstance(decryptor, x25519.X25519PrivateKey)
    with closing(sqlite3.connect(dbpath(tmp_path / "state"))) as db:
        _, owner, verifier, recipient, origin_verifier, origin_private = db.execute(
            "SELECT * FROM recovery_trust"
        ).fetchone()
    assert owner == record.owner_id
    assert verifier == r._public(signer.public_key())
    assert recipient == r._public(decryptor.public_key())
    statement = b"synthetic recovery proof"
    recovery_public = s.load_der_public_key(verifier)
    assert isinstance(recovery_public, ec.EllipticCurvePublicKey)
    recovery_public.verify(
        signer.sign(statement, ec.ECDSA(hashes.SHA256())),
        statement,
        ec.ECDSA(hashes.SHA256()),
    )
    peer = x25519.X25519PrivateKey.generate()
    backup_public = s.load_der_public_key(recipient)
    assert isinstance(backup_public, x25519.X25519PublicKey)
    assert peer.exchange(backup_public) == decryptor.exchange(peer.public_key())
    assert (
        base64.b64decode(doc["backup_origins_spki"][record.server_id])
        == origin_verifier
    )
    origin = s.load_der_private_key(origin_private, None)
    assert r._public(origin.public_key()) == origin_verifier != verifier
    database_bytes = (dbpath(tmp_path / "state")).read_bytes()
    for name in ("recovery_signer_pkcs8", "backup_x25519_pkcs8"):
        assert doc[name] not in output
        assert base64.b64decode(doc[name]) not in database_bytes
    assert set(p.name for p in (tmp_path / "state").iterdir()) == {
        "generations",
        "active-generation.json",
        a._LOCK,
    }
    if os.name != "nt":
        assert (tmp_path / "operator.json").stat().st_mode & 0o777 == 0o600
    # Runtime inspection requires public trust, never the external private file.
    (tmp_path / "operator.json").unlink()
    assert a.authority_status(tmp_path / "state") == record


def test_explicit_reuse_preserves_owner_keys_and_all_origin_trust(tmp_path):
    first = initialize(tmp_path)
    before = read(tmp_path)
    with pytest.raises(a.AuthorityError, match="explicit reuse"):
        initialize(tmp_path, "other")
    assert read(tmp_path) == before
    assert not (dbpath(tmp_path / "other")).exists()
    second = initialize(tmp_path, "other", reuse=True)
    after = read(tmp_path)
    assert first.owner_id == second.owner_id and first.server_id != second.server_id
    assert before["recovery_signer_pkcs8"] == after["recovery_signer_pkcs8"]
    assert before["backup_x25519_pkcs8"] == after["backup_x25519_pkcs8"]
    assert before["backup_origins_spki"].items() <= after["backup_origins_spki"].items()
    assert set(after["backup_origins_spki"]) == {first.server_id, second.server_id}


@pytest.mark.parametrize("phase", ["write", "external", "database"])
def test_failed_setup_preserves_custody_without_claiming_success(
    tmp_path, monkeypatch, phase
):
    real = a._publish
    real_flush = a._flush_parents

    def publish(pending, target):
        if phase == "database" and target.name == a.DATABASE:
            raise OSError("injected database publication failure")
        return real(pending, target)

    def flush(parent):
        if phase == "external" and parent == tmp_path:
            raise OSError("injected external publication flush failure")
        real_flush(parent)

    with monkeypatch.context() as patch:
        patch.setattr(a, "_publish", publish)
        patch.setattr(a, "_flush_parents", flush)
        if phase == "write":
            patch.setattr(
                r,
                "_private",
                lambda key: (_ for _ in ()).throw(
                    OSError("injected key write failure")
                ),
            )
        with pytest.raises(OSError, match="injected"):
            initialize(tmp_path)
    assert a.authority_status(tmp_path / "state") is None
    assert not (tmp_path / "state" / a._PENDING).exists()
    assert not (tmp_path / ".operator.json.arbiter-pending").exists()
    exists = (tmp_path / "operator.json").exists()
    assert exists == (phase != "write")
    previous = read(tmp_path) if exists else None
    record = initialize(tmp_path, reuse=exists)
    assert a.authority_status(tmp_path / "state") == record
    if previous:
        assert (
            read(tmp_path)["recovery_signer_pkcs8"] == previous["recovery_signer_pkcs8"]
        )


@pytest.mark.parametrize(
    "damage",
    [
        "unknown",
        "duplicate",
        "trailing",
        "large",
        "nested",
        "curve",
        "origin",
        "owner",
        "base64",
    ],
)
def test_invalid_recovery_file_never_rewrites_or_initializes(tmp_path, damage):
    initialize(tmp_path)
    doc = read(tmp_path)
    if damage == "unknown":
        doc["extra_authority"] = True
    elif damage == "curve":
        doc["recovery_signer_pkcs8"] = r._encode(
            r._private(x25519.X25519PrivateKey.generate())
        )
    elif damage == "origin":
        doc["backup_origins_spki"] = {"not-an-id": "garbage"}
    elif damage == "owner":
        doc["owner_id"] = "not-an-id"
    elif damage == "base64":
        doc["backup_x25519_pkcs8"] += "="
    data = json.dumps(doc).encode()
    if damage == "duplicate":
        data = data[:-1] + b', "owner_id": "duplicate"}'
    elif damage == "trailing":
        data += b"junk"
    elif damage == "large":
        data = b"x" * (r.MAX_FILE_BYTES + 1)
    elif damage == "nested":
        data = b"[" * 2000 + b"]" * 2000
    path = tmp_path / "operator.json"
    path.write_bytes(data)
    with pytest.raises(a.AuthorityError):
        initialize(tmp_path, "other", reuse=True)
    assert path.read_bytes() == data
    assert not (dbpath(tmp_path / "other")).exists()


@pytest.mark.parametrize(
    "column",
    ["verifier", "backup_recipient", "origin_verifier", "origin_private", "owner_id"],
)
def test_damaged_registered_trust_is_not_repaired(tmp_path, column):
    initialize(tmp_path)
    path = dbpath(tmp_path / "state")
    with closing(sqlite3.connect(path)) as db:
        db.execute(f"UPDATE recovery_trust SET {column}=?", ("invalid",))
        db.commit()
    before = path.read_bytes()
    with pytest.raises(a.AuthorityError):
        a.authority_status(tmp_path / "state")
    assert path.read_bytes() == before


@pytest.mark.parametrize("destination", ["inside", "missing-parent", "missing-reuse"])
def test_setup_requires_explicit_safe_external_destination(tmp_path, destination):
    root = tmp_path / "state"
    file = {
        "inside": root / "operator.json",
        "missing-parent": tmp_path / "absent" / "operator.json",
        "missing-reuse": tmp_path / "operator.json",
    }[destination]
    with pytest.raises(a.AuthorityError):
        a.initialize_authority(
            root, recovery_file=file, reuse_recovery_file=destination == "missing-reuse"
        )
    assert not (dbpath(root)).exists()
    assert not file.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX aliases and modes")
@pytest.mark.parametrize("damage", ["symlink", "hardlink", "mode", "parent"])
def test_external_custody_rejects_unsafe_paths(tmp_path, damage):
    initialize(tmp_path)
    path = tmp_path / "operator.json"
    before = path.read_bytes()
    if damage == "symlink":
        other = tmp_path / "original"
        path.rename(other)
        path.symlink_to(other)
    elif damage == "hardlink":
        os.link(path, tmp_path / "alias")
    elif damage == "mode":
        path.chmod(0o644)
    else:
        tmp_path.chmod(0o755)
    with pytest.raises(a.AuthorityError):
        initialize(tmp_path, "other", reuse=True)
    assert path.read_bytes() == before


def test_new_file_that_appears_during_setup_is_never_overwritten(tmp_path, monkeypatch):
    real = r.register_and_publish

    def publish(path, *args):
        path.write_bytes(b"preserve unexpected file")
        path.chmod(0o600)
        return real(path, *args)

    monkeypatch.setattr(r, "register_and_publish", publish)
    with pytest.raises(FileExistsError):
        initialize(tmp_path)
    assert (tmp_path / "operator.json").read_bytes() == b"preserve unexpected file"
    assert a.authority_status(tmp_path / "state") is None


def test_cli_requires_destination_and_never_prints_private_material(tmp_path, capsys):
    with pytest.raises(SystemExit):
        main(["authority", "init", "--state-dir", str(tmp_path / "state")])
    capsys.readouterr()
    assert (
        main(
            [
                "authority",
                "init",
                "--state-dir",
                str(tmp_path / "state"),
                "--recovery-file",
                str(tmp_path / "operator.json"),
                "--json",
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    doc = read(tmp_path)
    assert not output.err
    assert doc["recovery_signer_pkcs8"] not in output.out
    assert doc["backup_x25519_pkcs8"] not in output.out


def test_interrupted_external_staging_is_bounded_and_reconciled(tmp_path):
    # This is the deterministic artifact left by a killed offline initializer.
    pending = tmp_path / ".operator.json.arbiter-pending"
    pending.write_bytes(b"interrupted private staging")
    pending.chmod(0o600)
    record = initialize(tmp_path)
    assert a.authority_status(tmp_path / "state") == record
    assert not pending.exists()
    assert not list(tmp_path.glob("*.pending"))


def test_real_kill_after_link_preserves_keys_and_retry_completes(tmp_path):
    ready = tmp_path / "linked"
    script = """
import sys, time
from pathlib import Path
from arbiter_server import authority, recovery
root = Path(sys.argv[1])
real = recovery.os.link
def pause(source, target):
    real(source, target)
    (root / "linked").write_text("ready")
    time.sleep(60)
recovery.os.link = pause
authority.initialize_authority(root / "state", recovery_file=root / "operator.json")
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 15
        while (
            not ready.exists() and child.poll() is None and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert ready.exists(), "child did not reach the hard-link publication boundary"
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=15)
    file = tmp_path / "operator.json"
    pending = tmp_path / ".operator.json.arbiter-pending"
    assert file.stat().st_nlink == pending.stat().st_nlink == 2
    before = read(tmp_path)
    record = initialize(tmp_path, reuse=True)
    assert a.authority_status(tmp_path / "state") == record
    assert not pending.exists() and file.stat().st_nlink == 1
    after = read(tmp_path)
    for name in ("recovery_signer_pkcs8", "backup_x25519_pkcs8", "owner_id"):
        assert before[name] == after[name]
    assert before["backup_origins_spki"].items() <= after["backup_origins_spki"].items()


@pytest.mark.parametrize("state", ["third-link", "mismatched-inode"])
def test_reconciliation_never_accepts_unrelated_hardlinks(tmp_path, state):
    initialize(tmp_path)
    target = tmp_path / "operator.json"
    pending = tmp_path / ".operator.json.arbiter-pending"
    before = target.read_bytes()
    if state == "third-link":
        os.link(target, pending)
        os.link(target, tmp_path / "extra")
    else:
        pending.write_bytes(before)
        pending.chmod(0o600)
        os.link(pending, tmp_path / "extra")
    with pytest.raises(a.AuthorityError, match="exactly"):
        initialize(tmp_path, "other", reuse=True)
    assert target.read_bytes() == before and pending.read_bytes() == before
    assert not (dbpath(tmp_path / "other")).exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows recovery custody ACL")
@pytest.mark.parametrize("damage", ["readable", "foreign-owner"])
def test_windows_recovery_file_acl_is_not_repaired(tmp_path, damage):
    initialize(tmp_path)
    file = tmp_path / "operator.json"
    original = file.read_bytes()
    command = (
        ["icacls", str(file), "/grant", "*S-1-5-32-545:R"]
        if damage == "readable"
        else ["icacls", str(file), "/setowner", "*S-1-5-32-545"]
    )
    subprocess.run(command, check=True, capture_output=True, text=True)
    before_acl = subprocess.check_output(["icacls", str(file)], text=True)
    with pytest.raises(a.AuthorityError):
        initialize(tmp_path, "other", reuse=True)
    assert file.read_bytes() == original
    assert subprocess.check_output(["icacls", str(file)], text=True) == before_acl
