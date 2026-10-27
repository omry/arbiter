"""Test-only human terminal driver for an unmodified installed executable."""

import json
import os
import re
import select
import socket
import struct
import subprocess
import sys
import time
from typing import Any


def run():
    config = json.loads(sys.stdin.readline())
    master = slave = input_fd = output_fd = -1
    terminal_options: dict[str, Any]
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        import msvcrt

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        read = kernel.ReadConsoleOutputCharacterW
        read.argtypes = [
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        write = kernel.WriteConsoleInputW
        write.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        input_fd = os.open("CONIN$", os.O_RDWR)
        output_fd = os.open("CONOUT$", os.O_RDWR)

        def terminal_read():
            buffer = ctypes.create_unicode_buffer(8192)
            size = wintypes.DWORD()
            if not read(
                msvcrt.get_osfhandle(output_fd), buffer, 8192, 0, ctypes.byref(size)
            ):
                raise OSError("could not inspect test console")
            return buffer.value.encode()

        def enter():
            # INPUT_RECORD(KEY_EVENT): down/up, one RETURN, no secret input.
            records = b"".join(
                struct.pack("<H2xiHHHHI", 1, down, 1, 13, 0, 13, 0) for down in (1, 0)
            )
            buffer = ctypes.create_string_buffer(records)
            size = wintypes.DWORD()
            if not write(msvcrt.get_osfhandle(input_fd), buffer, 2, ctypes.byref(size)):
                raise OSError("could not confirm test console")

        terminal_options = {}
    else:
        import fcntl
        import pty
        import termios

        master, slave = pty.openpty()

        def terminal_read():
            if select.select([master], [], [], 0)[0]:
                return os.read(master, 4096)
            return b""

        def enter():
            os.write(master, b"\n")

        terminal_options = dict(
            start_new_session=True,
            pass_fds=(slave,),
            preexec_fn=lambda: fcntl.ioctl(slave, termios.TIOCSCTTY, 0),
        )
    command = [
        config["command"],
        "--config-dir",
        config["directory"],
        "arbiter.connect_timeout=15s",
        "enrol",
        "url=" + config["url"],
        "name=Installed CLI",
    ]
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            **terminal_options,
        )
        terminal, handed_off = b"", False
        selector = secret = ""
        deadline = time.monotonic() + 45
        with socket.create_connection(
            ("127.0.0.1", config["control"]), timeout=10
        ) as control:
            control.settimeout(15)
            with control.makefile("rwb") as stream:
                while process.poll() is None and time.monotonic() < deadline:
                    data = terminal_read()
                    terminal = data if sys.platform == "win32" else terminal + data
                    if len(terminal) > 32768:
                        raise RuntimeError("unexpected terminal output volume")
                    match = re.search(
                        rb"Request: ([A-Za-z0-9_-]{22})\s+Secret[^:]*: ([A-Za-z0-9_-]{43})",
                        terminal,
                    )
                    if (
                        not handed_off
                        and match
                        and b"press Enter to continue: " in terminal
                    ):
                        selector, secret = (part.decode() for part in match.groups())
                        stream.write(
                            json.dumps(dict(selector=selector, secret=secret)).encode()
                            + b"\n"
                        )
                        stream.flush()
                        if stream.readline(16) != b"true\n":
                            raise RuntimeError("operator did not approve")
                        enter()
                        handed_off = True
                    time.sleep(0.01)
        if process.poll() is None:
            raise TimeoutError("installed enrollment did not settle")
        stdout, stderr = process.communicate(timeout=5)
        if not handed_off or secret.encode() in stdout + stderr:
            raise RuntimeError("invalid protected terminal handoff")
        confirmed = (
            process.returncode == 0 and b"Enrollment activated and saved" in stdout
        )
        if not confirmed and not (
            config["recover"]
            and process.returncode == 1
            and ("resume=" + selector).encode() in stderr
        ):
            raise RuntimeError(
                "installed enrollment did not confirm or retain recovery"
            )
        # Only public completion metadata leaves the synthetic human driver.
        print(json.dumps(dict(selector=selector, activated=confirmed)))
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for fd in (input_fd, output_fd, master, slave):
            if fd >= 0:
                os.close(fd)


if __name__ == "__main__":
    run()
