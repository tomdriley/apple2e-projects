"""Shared transport to the Apple IIe VT100 terminal.

  TcpLink    - listen for MAME's null_modem to connect out to us (emulator).
  SerialLink - a USB/RS-232 adapter wired to the Super Serial Card (hardware).

Both expose write(bytes) and a non-blocking read(n) -> bytes (b"" if idle).
"""
from __future__ import annotations

import socket
import sys


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


def open_link(transport: str, host: str = "127.0.0.1", port: int = 6551,
              device: str | None = None, baud: int = 9600):
    if transport == "tcp":
        return TcpLink(host, port)
    return SerialLink(device, baud)
