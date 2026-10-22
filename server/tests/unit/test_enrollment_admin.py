"""Approval uses the local OS boundary and never resets public request ownership."""

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, cast

import pytest

from arbiter_server import authority as a, enrollment as e, enrollment_admin as admin
from arbiter_server import local_control as c
from arbiter_server.errors import OperationError
from server.tests.unit.test_enrollment_requests import advertisement, ROUTE
from server.tests.unit.test_native_http_protocol import _asgi_request, _client

ACCOUNTS = {("echo", "primary"), ("echo", "other")}


def proposed(
    manager: e.EnrollmentRequests,
) -> tuple[bytes, e.PendingRequest, dict[str, Any]]:
    raw = json.dumps(advertisement()).encode()
    request = manager.register(raw)
    secret = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    args: dict[str, Any] = dict(
        selector=request.advertisement.selector,
        revision=request.revision,
        request_digest=request.advertisement.digest,
        psk=secret,
        grants=["echo/primary"],
    )
    return raw, request, args


def test_approval_immutable_idempotent_private_and_counted():
    now = [0.0]
    manager = e.EnrollmentRequests(1, clock=lambda: now[0])
    manager.open_window()
    raw, pending, args = proposed(manager)
    first = manager.control("approve", args, ACCOUNTS)
    approved = manager.approved(pending.advertisement.selector)
    assert approved is not None and approved.request is pending
    assert approved.grants == (("echo", "primary"),)
    assert len(approved.secret) == 32
    assert args["psk"] not in repr(approved)
    assert args["psk"] not in json.dumps(first)
    assert args["psk"] not in json.dumps(manager.control("list", {}, ACCOUNTS))
    summary = first["request"]
    assert isinstance(summary, dict) and summary["status"] == "approved"
    with pytest.raises(FrozenInstanceError):
        setattr(approved, "grants", ())
    now[0] = 599
    manager.open_window()
    assert manager.register(raw) is pending
    manager.control("approve", args, ACCOUNTS)
    assert manager.approved(pending.advertisement.selector) is approved
    assert pending.deadline == 600 and len(manager.pending()) == 1
    changes: list[dict[str, object]] = [{"grants": ["echo/other"]}, {"psk": "A" * 43}]
    for change in changes:
        with pytest.raises(ValueError, match="immutable"):
            manager.control("approve", {**args, **change}, ACCOUNTS)
    with pytest.raises(OperationError) as error:
        manager.register(json.dumps(advertisement()).encode())
    assert error.value.code == "resource_exhausted"
    now[0] = 600
    assert manager.approved(pending.advertisement.selector) is None
    replacement = manager.register(raw)
    assert replacement.revision != pending.revision
    with pytest.raises(ValueError, match="selection changed"):
        manager.control("approve", args, ACCOUNTS)
    assert manager.approved(replacement.advertisement.selector) is None


@pytest.mark.parametrize(
    "change",
    [
        {"psk": "short"},
        {"psk": "A" * 44},
        {"psk": "!" * 43},
        {"psk": None},
        {"grants": ["echo/missing"]},
        {"grants": ["echo/primary", "echo/primary"]},
        {"grants": ["echo/primary/extra"]},
        {"grants": [42]},
        {"grants": {}},
        {"revision": "stale"},
        {"request_digest": "stale"},
        {"extra": True},
    ],
)
def test_invalid_approval_never_creates_context(change):
    manager = e.EnrollmentRequests()
    manager.open_window()
    _, pending, args = proposed(manager)
    with pytest.raises(ValueError):
        manager.control("approve", dict(args, **change), ACCOUNTS)
    assert manager.pending() == (pending,)
    assert manager.approved(pending.advertisement.selector) is None


def test_expiry_during_grant_validation_and_restart_remove_approval():
    now = [0.0]
    manager = e.EnrollmentRequests(clock=lambda: now[0])
    manager.open_window()
    _, pending, args = proposed(manager)

    class ExpiringAccounts(set):
        def __contains__(self, value):
            now[0] = 600
            return super().__contains__(value)

    with pytest.raises(ValueError, match="missing or expired"):
        manager.control("approve", args, ExpiringAccounts(ACCOUNTS))
    assert manager.pending() == ()
    manager.open_window()
    _, pending, args = proposed(manager)
    manager.control("approve", args, ACCOUNTS)
    manager.reset()
    assert manager.approved(pending.advertisement.selector) is None
    with pytest.raises(OperationError, match="closed"):
        manager.require_open()


def test_reject_frees_slot_without_reviving_old_approval():
    manager = e.EnrollmentRequests(1)
    manager.open_window()
    raw, pending, args = proposed(manager)
    manager.control("approve", args, ACCOUNTS)
    selection = {k: args[k] for k in ("selector", "revision", "request_digest")}
    manager.control("reject", selection, ACCOUNTS)
    assert manager.pending() == () and manager.approved(args["selector"]) is None
    replacement = manager.register(raw)
    assert replacement.revision != pending.revision
    for command, arguments in (("approve", args), ("reject", selection)):
        with pytest.raises(ValueError, match="selection changed"):
            manager.control(command, arguments, ACCOUNTS)
    assert manager.pending() == (replacement,)


def test_full_capacity_pages_and_exact_selection_fit_shared_frame():
    manager = e.EnrollmentRequests(140)
    manager.open_window()
    name = "😀" * 64
    for _ in range(140):
        data = advertisement()
        data["name"] = name
        manager.register(json.dumps(data, ensure_ascii=False).encode())

    opened = manager.control("open", {}, ACCOUNTS)
    assert "requests" not in opened
    assert len(c._frame({"ok": True, "result": opened})) - 4 <= c.MAX_MESSAGE

    rows: list[dict[str, object]] = []
    cursor: str | None = None
    pages = 0
    while True:
        arguments: dict[str, object] = {}
        if cursor is not None:
            arguments = {"cursor": cursor}
        page = manager.control("list", arguments, ACCOUNTS)
        assert len(c._frame({"ok": True, "result": page})) - 4 <= c.MAX_MESSAGE
        rows.extend(cast(list[dict[str, object]], page["requests"]))
        pages += 1
        cursor = cast(str | None, page["next_cursor"])
        if cursor is None:
            break
    assert pages > 1 and len(rows) == 140
    assert {row["selector"] for row in rows} == {
        request.advertisement.selector for request in manager.pending()
    }

    selected = rows[0]
    exact = manager.control("list", {"selector": selected["selector"]}, ACCOUNTS)
    exact_rows = cast(list[dict[str, object]], exact["requests"])
    assert len(exact_rows) == 1
    assert exact_rows[0]["revision"] == selected["revision"]
    selection = {
        key: selected[key] for key in ("selector", "revision", "request_digest")
    }
    secret = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    approved = manager.control(
        "approve",
        {**selection, "psk": secret, "grants": ["echo/primary"]},
        ACCOUNTS,
    )
    rejected = manager.control("reject", selection, ACCOUNTS)
    assert len(c._frame({"ok": True, "result": approved})) - 4 <= c.MAX_MESSAGE
    assert len(c._frame({"ok": True, "result": rejected})) - 4 <= c.MAX_MESSAGE
    assert rejected["rejected"] == selected["selector"]


def test_cli_list_combines_cursor_pages(tmp_path, monkeypatch, capsys):
    pages = iter(
        [
            {
                "initialized": True,
                "open": True,
                "pending_count": 2,
                "requests": [{"selector": "first"}],
                "next_cursor": "first",
            },
            {
                "initialized": True,
                "open": True,
                "pending_count": 2,
                "requests": [{"selector": "second"}],
                "next_cursor": None,
            },
        ]
    )
    calls = []
    monkeypatch.setattr(
        admin,
        "enrollment_request",
        lambda *args: calls.append(args) or next(pages),
    )
    assert admin.enrollment_cli("list", tmp_path, as_json=True) == 0
    listing = json.loads(capsys.readouterr().out)
    assert [row["selector"] for row in listing["requests"]] == [
        "first",
        "second",
    ]
    assert [call[2] for call in calls] == [{}, {"cursor": "first"}]


def test_cli_list_rejects_replaced_cursor_boundary(tmp_path, monkeypatch):
    now = [0.0]
    manager = e.EnrollmentRequests(64, clock=lambda: now[0])
    manager.open_window()
    old_raw = []
    old_revisions = []
    for _ in range(32):
        raw = json.dumps(advertisement()).encode()
        old_raw.append(raw)
        old_revisions.append(manager.register(raw).revision)
    now[0] = 1.0
    for _ in range(32):
        manager.register(json.dumps(advertisement()).encode())
    now[0] = 599.9
    manager.open_window()
    boundary_revision = old_revisions[-1]
    calls = []
    original = admin.enrollment_request

    def request(state_dir, command, arguments):
        calls.append(dict(arguments))
        result = manager.control(command, arguments, set())
        if len(calls) == 1:
            now[0] = 600.0
            replacement = manager.register(old_raw[-1])
            assert replacement.revision != boundary_revision
        return result

    monkeypatch.setattr(admin, "enrollment_request", request)
    with pytest.raises(ValueError, match="cursor is invalid; list again"):
        admin._list_all(Path(tmp_path))
    assert calls[1].keys() == {"cursor"}
    assert len(manager.pending()) == 33


def test_concurrent_approval_registration_and_reset_keep_single_capacity():
    manager = e.EnrollmentRequests(1)
    manager.open_window()
    _, pending, args = proposed(manager)
    extra = json.dumps(advertisement()).encode()

    def attempt(index):
        if index % 2:
            manager.control("approve", args, ACCOUNTS)
        else:
            with pytest.raises(OperationError) as error:
                manager.register(extra)
            assert error.value.code == "resource_exhausted"

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(attempt, range(40)))
    assert manager.pending() == (pending,)
    assert manager.approved(pending.advertisement.selector) is not None
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: manager.reset(), range(20)))
    assert manager.pending() == () and manager.approved(args["selector"]) is None


@pytest.mark.usefixtures("private_windows_test_parent")
def test_local_dispatch_requires_initialized_authority_and_exact_shape(
    tmp_path, monkeypatch
):
    root = a._root(tmp_path / "authority", create=True)
    seen = []
    control = c.AuthorityControl(root, lambda *args: seen.append(args) or {})
    value: dict[str, Any] = dict(
        root=str(root), action="enrollment", command="open", arguments={}
    )
    with pytest.raises(a.AuthorityError, match="initialize"):
        control.dispatch(value)
    monkeypatch.setattr(a, "_current_record", lambda _: object())
    for bad in (
        dict(value, extra=True),
        dict(value, root="wrong"),
        dict(value, arguments=[]),
    ):
        with pytest.raises(a.AuthorityError, match="invalid"):
            control.dispatch(bad)
    assert seen == []


@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize("selector", ["A" * 22, "-" + "A" * 21, "--" + "A" * 20])
def test_enrollment_selector_option_accepts_generated_leading_hyphens(action, selector):
    from arbiter_server.main import _build_parser

    parsed = _build_parser().parse_args(
        [
            "enrollment",
            action,
            f"--selector={selector}",
            "--state-dir",
            "/unused-authority",
        ]
    )
    assert parsed.enrollment_command == action and parsed.selector == selector


@pytest.mark.usefixtures("private_windows_test_parent")
def test_installed_local_cli_and_protected_approval_on_native_transport(
    tmp_path, monkeypatch, capsys
):
    app = cast(Any, _client(tmp_path)._app)
    manager = app.state.enrollment_requests
    root = tmp_path / "server" / "authority"
    a.initialize_authority(root, recovery_file=tmp_path / "operator.json")

    async def command(action, selector=None):
        argv = [sys.executable, "-m", "arbiter_server", "enrollment", action]
        if selector:
            argv.append(f"--selector={selector}")
        argv += ["--state-dir", str(root), "--json"]
        result = await asyncio.to_thread(
            subprocess.run, argv, capture_output=True, text=True, timeout=15
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    async def exercise():
        async with app.router.lifespan_context(app):
            assert (
                await _asgi_request(app, "POST", ROUTE, body_chunks=[b"{}"])
            ).status_code == 403
            assert (await command("open"))["open"] is True
            advertised = advertisement()
            advertised["selector"] = "-" + "A" * 21
            raw = json.dumps(advertised).encode()
            response = await _asgi_request(app, "POST", ROUTE, body_chunks=[raw])
            assert response.status_code == 200
            (pending,) = manager.pending()
            selector = pending.advertisement.selector
            assert (await command("list"))["requests"][0]["selector"] == selector
            secret = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
            prompts = []
            with monkeypatch.context() as patch:
                patch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
                patch.setattr(
                    admin.getpass,
                    "getpass",
                    lambda prompt: prompts.append(prompt) or secret,
                )
                code = await asyncio.to_thread(
                    admin.enrollment_cli,
                    "approve",
                    root,
                    selector=selector,
                    grants=["echo/primary"],
                    as_json=True,
                )
            assert code == 0 and prompts == ["Pairing secret (input hidden): "]
            captured = capsys.readouterr()
            assert secret not in captured.out + captured.err
            approved = manager.approved(selector)
            assert approved is not None and approved.grants == (("echo", "primary"),)
            assert (await command("list"))["requests"][0]["status"] == "approved"
            assert (await _asgi_request(app, "GET", "/_health_")).status_code == 200
            assert (await command("reject", selector))["rejected"] == selector
            assert manager.approved(selector) is None
            # Real shutdown/restart clears approved memory as well as pending work.
            _, pending, args = proposed(manager)
            manager.control("approve", args, ACCOUNTS)
        assert (
            manager.pending() == ()
            and manager.approved(pending.advertisement.selector) is None
        )
        async with app.router.lifespan_context(app):
            listing = await command("list")
            assert listing["requests"] == [] and listing["open"] is False
        for path in root.rglob("*"):
            if path.is_file():
                assert secret.encode() not in path.read_bytes()

    asyncio.run(exercise())


def test_cli_refuses_nonterminal_or_echo_fallback_without_sending_secret(
    tmp_path, monkeypatch
):
    row = dict(selector="public", revision="revision", request_digest="digest")
    calls = []
    monkeypatch.setattr(
        admin,
        "enrollment_request",
        lambda *args: calls.append(args) or {"requests": [row]},
    )
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: False))
    assert admin.enrollment_cli("approve", tmp_path, selector="public") == 1
    assert len(calls) == 1 and calls[0][1] == "list"
    assert calls[0][2] == {"selector": "public"}
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))

    def no_hidden_input(_prompt):
        import warnings

        warnings.warn("Cannot control echo", admin.getpass.GetPassWarning)
        raise AssertionError("must stop before echoing fallback reads")

    monkeypatch.setattr(admin.getpass, "getpass", no_hidden_input)
    assert admin.enrollment_cli("approve", tmp_path, selector="public") == 1
    assert len(calls) == 2 and all(item[1] == "list" for item in calls)
