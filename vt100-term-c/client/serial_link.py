"""Shared transport to the Apple IIe VT100 terminal.

  TcpLink    - listen for MAME's null_modem to connect out to us (emulator).
  SerialLink - a USB/RS-232 adapter wired to the Super Serial Card (hardware).
  PtyLink    - a POSIX pseudo-terminal endpoint (Linux/macOS, stdlib only, no
               pywinpty) so a stock Unix host can drive the wire; see
               docs/PROTOCOL.md.

All expose write(bytes) and a non-blocking read(n) -> bytes (b"" if idle).
"""
from __future__ import annotations

import socket
import sys
import time


class TcpLink:
    def __init__(self, host: str = "127.0.0.1", port: int = 6551) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, port))
        srv.listen(1)
        print(f"[waiting for MAME on {host}:{port} ...]", file=sys.stderr)
        self.conn, addr = srv.accept()
        srv.close()
        print(f"[connected: {addr[0]}:{addr[1]}]", file=sys.stderr)
        self.conn.setblocking(False)

    def write(self, data: bytes) -> None:
        self.conn.sendall(data)

    def read(self, n: int = 1024) -> bytes:
        try:
            return self.conn.recv(n)
        except BlockingIOError:
            return b""
        except OSError:
            return b""

    def close(self) -> None:
        try:
            self.conn.close()
        except OSError:
            pass


class SerialLink:
    def __init__(self, device: str | None = None, baud: int = 9600) -> None:
        import serial
        from serial.tools import list_ports
        if not device:
            ports = [p for p in list_ports.comports() if getattr(p, "vid", None)]
            ports = ports or list(list_ports.comports())
            if not ports:
                sys.exit("No serial ports found - is the USB/RS-232 adapter in?")
            device = ports[0].device
            print(f"[auto-detected {device}]", file=sys.stderr)
        self.ser = serial.Serial(device, baud, timeout=0)
        print(f"[open {device} @ {baud} 8N1]", file=sys.stderr)

    def write(self, data: bytes) -> None:
        self.ser.write(data)

    def read(self, n: int = 1024) -> bytes:
        return self.ser.read(n)

    def close(self) -> None:
        self.ser.close()


class PtyLink:
    """A POSIX pseudo-terminal endpoint -- an alternative host binding that needs
    no Windows/pywinpty and no serial hardware.

    Opens a PTY pair with the stdlib alone (`os.openpty`). The harness drives the
    *master* through the same write()/read() interface as the other links, while
    any stock Unix program attaches to the *slave* device printed at startup:

      * `socat FILE:/dev/ttyUSB0,raw,b9600 FILE:<slave>,raw`  bridges the PTY to a
        real serial line to the Apple -- so the harness reaches hardware from Linux.
      * `agetty 9600 <slave>` runs a login on it -- the Apple as a literal Unix TTY.
      * a plain `bash <>"<slave>"` echoes bytes back for a smoke test.

    This proves the wire contract is a standard serial TTY: nothing here is
    Windows- or pywinpty-specific. See docs/PROTOCOL.md.
    """

    def __init__(self, link_path: str | None = None) -> None:
        import os   # os.openpty/ttyname are POSIX-only; import lazily so this
        import tty  # module still imports on Windows (mirrors SerialLink).
        self._os = os
        self.master, self._slave = os.openpty()
        self.slave_name = os.ttyname(self._slave)
        tty.setraw(self.master)          # 8-bit clean: no echo, no CR/LF fixups
        os.set_blocking(self.master, False)
        if link_path:                     # optional stable symlink for tooling
            try:
                if os.path.islink(link_path) or os.path.exists(link_path):
                    os.remove(link_path)
                os.symlink(self.slave_name, link_path)
                self.slave_name = link_path
            except OSError:
                pass
        print(f"[pty ready: attach a host to {self.slave_name} @ 9600 8N1]",
              file=sys.stderr)

    def write(self, data: bytes) -> None:
        mv = memoryview(data)
        while mv:
            try:
                mv = mv[self._os.write(self.master, mv):]
            except BlockingIOError:
                time.sleep(0.001)
            except OSError:
                return

    def read(self, n: int = 1024) -> bytes:
        try:
            return self._os.read(self.master, n)
        except BlockingIOError:
            return b""
        except OSError:
            return b""

    def close(self) -> None:
        for fd in (self.master, self._slave):
            try:
                self._os.close(fd)
            except OSError:
                pass


def open_link(transport: str, host: str = "127.0.0.1", port: int = 6551,
              device: str | None = None, baud: int = 9600):
    if transport == "tcp":
        return TcpLink(host, port)
    if transport == "posix":
        # `device` doubles as the optional slave symlink path for POSIX PTYs.
        return PtyLink(device)
    return SerialLink(device, baud)
