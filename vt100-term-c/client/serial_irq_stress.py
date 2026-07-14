#!/usr/bin/env python3
"""Repeated ROM-backed MAME regressions for the shared 6551 RX/TX ISR."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import time
import traceback

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = ROOT.parent
BUILD = ROOT / "build"
CONF = HERE / "conformance"
PORT = int(os.environ.get("MAME_PORT", "6572"))
os.environ["MAME_PORT"] = str(PORT)

CONF_PROBE = CONF / "probes" / "serial_irq_probe.lua"
FLOW_PROBE = CONF / "probes" / "serial_irq_flow_probe.lua"
MIXED_PROBE = CONF / "probes" / "serial_irq_mixed_probe.lua"
SCREEN_PROBE = HERE / "serial_irq_screen.lua"
DIAG_REQUEST = BUILD / "serial_irq_diag_request"
DIAG_REQUEST_TMP = BUILD / "serial_irq_diag_request.tmp"
DIAG_OUTPUT = BUILD / "serial_irq_diag.txt"
DIAG_SYMS = BUILD / "serial_irq_syms.txt"
KEYS_REQUEST = BUILD / "serial_irq_keys_request"
LBL = BUILD / "vt100.lbl"
BIN = BUILD / "VT100.BIN"

sys.path.insert(0, str(CONF))
sys.path.insert(0, str(HERE))

import target_mame  # noqa: E402
import shell_test  # noqa: E402

target_mame.PROBE_LUA = CONF_PROBE
shell_test.PORT = PORT
shell_test.WATCH_LUA = str(SCREEN_PROBE)

DA_SENTINEL = b"DA-FOLLOW-SENTINEL"
DA_PAYLOAD = b"\x1b[c\x1b[?1004h" + DA_SENTINEL
DA_REPLY = b"\x1b[?1;0c"
CPR_PAYLOAD = b"\x1b[10;30H\x1b[6n\x1b[6n"
CPR_REPLY = b"\x1b[10;30R\x1b[10;30R"
FLOW_SENTINEL = b"FLOW-CONTROL-DONE"
FLOW_PAYLOAD = b"\x1b[2J" * 100 + FLOW_SENTINEL
FLOW_SETTLE_TIMEOUT = 30.0
MIXED_SENTINEL = b"MIXED-DUPLEX-DONE"
MIXED_GROUP = b"\x1b[2J\x1b[HMIX\x1b[c\x1b[6n\x05"
MIXED_PAYLOAD = MIXED_GROUP * 50 + b"\x1b[2J\x1b[H" + MIXED_SENTINEL
MIXED_REPLY = (b"\x1b[?1;0c\x1b[1;4RA2VT100") * 50
DIAG_SYMBOLS = (
    "r_head",
    "r_tail",
    "rx_ring",
    "t_head",
    "t_tail",
    "xoff_sent",
    "tx_irq_active",
    "serial_old_irq",
    "serial_chain_valid",
    "serial_isr_installed",
    "serial_isr_entry",
    "serial_chain_target",
    "serial_tx_data_store",
    "serial_old_reset",
    "exit",
)
_LBL_RE = re.compile(r"^al\s+([0-9A-Fa-f]+)\s+\._(\w+)\s*$")


def write_diag_syms() -> dict[str, int]:
    if not LBL.exists():
        raise FileNotFoundError(f"{LBL} not found -- run `make` first")
    found: dict[str, int] = {}
    for line in LBL.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _LBL_RE.match(line.strip())
        if match and match.group(2) in DIAG_SYMBOLS:
            found[match.group(2)] = int(match.group(1), 16)
    required = set(DIAG_SYMBOLS)
    missing = sorted(required - found.keys())
    if missing:
        raise RuntimeError(f"missing firmware labels: {', '.join(missing)}")
    image = BIN.read_bytes()
    store_offset = found["serial_tx_data_store"] - 0x0800
    if image[store_offset : store_offset + 3] != b"\x8d\xff\xff":
        actual = image[store_offset : store_offset + 3].hex()
        raise RuntimeError(
            "TX data store is not an absolute STA $ffff in the linked image: "
            f"{actual}"
        )
    DIAG_SYMS.write_text(
        "".join(f"{name}={address:04x}\n" for name, address in found.items()),
        encoding="ascii",
    )
    return found


def clean_diag() -> None:
    for path in (DIAG_REQUEST, DIAG_REQUEST_TMP, DIAG_OUTPUT, KEYS_REQUEST):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def request_diag(action: str, timeout: float = 3.0) -> tuple[dict[str, str], str]:
    try:
        DIAG_OUTPUT.unlink()
    except FileNotFoundError:
        pass
    DIAG_REQUEST_TMP.write_text(action + "\n", encoding="ascii")
    os.replace(DIAG_REQUEST_TMP, DIAG_REQUEST)
    deadline = time.time() + timeout
    raw = ""
    marker = {
        "reset": "reset=1",
        "snapshot": "page2=",
        "foreign_irq": "foreign_irq=",
        "ctrl_reset": "ctrl_reset=",
    }[action]
    while time.time() < deadline:
        try:
            raw = DIAG_OUTPUT.read_text(encoding="ascii", errors="replace")
        except OSError:
            raw = ""
        if marker in raw:
            break
        time.sleep(0.02)
    parsed: dict[str, str] = {}
    for line in raw.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            parsed[key] = value
    return parsed, raw


def reset_diag() -> None:
    parsed, raw = request_diag("reset")
    if parsed.get("reset") != "1":
        raise RuntimeError(f"diagnostic reset failed: {raw!r}")
    try:
        DIAG_OUTPUT.unlink()
    except FileNotFoundError:
        pass


def visible_rows(rows: list[str]) -> list[str]:
    return [
        f"{index + 1}:{row.rstrip()}"
        for index, row in enumerate(rows)
        if row.rstrip()
    ]


def capture_target_screen(
    target: target_mame.MameTarget,
) -> tuple[list[str], dict]:
    time.sleep(0.35)
    sequence = target._current_seq()
    text, _inverse, state = target._wait_fresh(sequence, 4.0)
    return text, target._fix_state(state)


def validate_diag(
    result: dict, diag: dict[str, str], expected_rx_bytes: bytes | None = None
) -> None:
    if diag.get("irq_vector_active") != "1":
        result["errors"].append(f"IRQ vector inactive: {diag!r}")
    if diag.get("ssc_interrupt_switch") != "0":
        result["errors"].append(f"SSC IRQ switch not On: {diag!r}")
    if diag.get("chain_valid") != "1":
        result["errors"].append(f"predecessor IRQ vector is not chainable: {diag!r}")
    if diag.get("isr_installed") != "1":
        result["errors"].append(f"serial ISR is not marked installed: {diag!r}")
    if diag.get("tx_store_opcode") != "8D":
        result["errors"].append(f"TX store is not absolute STA: {diag!r}")
    if diag.get("tx_store_operand") != "C0A8":
        result["errors"].append(f"TX store was not patched for slot 2: {diag!r}")
    if diag.get("reset_vector_active") != "1":
        result["errors"].append(f"Ctrl-Reset cleanup vector inactive: {diag!r}")
    if diag.get("reset_vector_valid") != "1":
        result["errors"].append(f"Ctrl-Reset vector checksum invalid: {diag!r}")
    try:
        expected_vector = int(diag.get("irq_vector_expected", ""), 16)
    except ValueError:
        result["errors"].append(f"invalid expected IRQ vector: {diag!r}")
    else:
        expected_writes = (
            f"03FE:{expected_vector & 0xFF:02X},"
            f"03FF:{expected_vector >> 8:02X}"
        )
        if expected_writes not in diag.get("vector_history", ""):
            result["errors"].append(f"IRQ vector installation not observed: {diag!r}")
    if diag.get("acia_command") != "09":
        result["errors"].append(f"unexpected ACIA command: {diag!r}")
    if diag.get("error_bits_seen") != "00":
        result["errors"].append(f"ACIA error bits observed: {diag!r}")
    if diag.get("rx_count") != "0":
        result["errors"].append(f"RX ring did not drain: {diag!r}")
    if diag.get("tx_count") != "0":
        result["errors"].append(f"TX ring did not drain: {diag!r}")
    if diag.get("tx_irq_active") != "0":
        result["errors"].append(f"TX IRQ remained active: {diag!r}")
    if diag.get("command_other_writes") != "0":
        result["errors"].append(f"unexpected ACIA command writes: {diag!r}")
    if diag.get("command_05_writes") != diag.get("command_09_writes"):
        result["errors"].append(f"unpaired TX IRQ transitions: {diag!r}")
    command_sequence = [
        value
        for value in diag.get("command_sequence", "").split(",")
        if value
    ]
    if any(
        value != ("05" if index % 2 == 0 else "09")
        for index, value in enumerate(command_sequence)
    ) or len(command_sequence) % 2:
        result["errors"].append(
            f"invalid TX IRQ command sequence: {command_sequence!r}"
        )
    try:
        page2_on = int(diag.get("page2", ""), 16) & 0x80
    except ValueError:
        result["errors"].append(f"invalid PAGE2 status: {diag!r}")
    else:
        if page2_on:
            result["errors"].append(
                f"PAGE2 not restored to main memory: {diag!r}"
            )
    if (
        expected_rx_bytes is not None
        and diag.get("rx_publications") != str(len(expected_rx_bytes))
    ):
        result["errors"].append(
            "RX publication count expected "
            f"{len(expected_rx_bytes)}, got {diag.get('rx_publications', '?')}"
        )
    if (
        expected_rx_bytes is not None
        and diag.get("rx_publication_bytes") != expected_rx_bytes.hex().upper()
    ):
        result["errors"].append(
            "RX publication bytes differ from the transmitted payload"
        )


def run_wire_once(mode: str, index: int) -> dict:
    target = target_mame.MameTarget(
        port=PORT, boot_timeout=45.0, settle=4.0, ack_to=3.0
    )
    result = {"run": index, "mode": mode, "ok": False, "errors": []}
    clean_diag()
    write_diag_syms()
    try:
        target.open()
        target.reset()
        reset_diag()
        target.term.clear_buf()

        if mode == "da":
            payload = DA_PAYLOAD
            expected_reply = DA_REPLY
            expected_rows = [DA_SENTINEL.decode("ascii")]
            expected_cursor = (1, len(DA_SENTINEL) + 1)
        else:
            payload = CPR_PAYLOAD
            expected_reply = CPR_REPLY
            expected_rows = []
            expected_cursor = (10, 30)

        target.term.send(payload, chunk=len(payload))
        text, state = capture_target_screen(target)
        time.sleep(0.10)
        reply = target.term.peek()
        rows = visible_rows(text)
        cursor = (state.get("cur_row"), state.get("cur_col"))
        result.update(
            {
                "payload_hex": payload.hex(),
                "reply_hex": reply.hex(),
                "reply_repr": repr(reply),
                "expected_reply_hex": expected_reply.hex(),
                "screen_nonblank": rows,
                "cursor": list(cursor),
                "state": {
                    key: state.get(key)
                    for key in (
                        "cur_row",
                        "cur_col",
                        "scroll_top",
                        "scroll_bot",
                        "attr_inverse",
                        "cursor_visible",
                        "cursor_shown",
                    )
                },
                "mame_returncode": target.proc.poll(),
            }
        )
        if reply != expected_reply:
            result["errors"].append(
                f"wire expected {expected_reply!r}, got {reply!r}"
            )
        actual_rows = [row.split(":", 1)[1] for row in rows]
        if actual_rows != expected_rows:
            result["errors"].append(
                f"screen expected {expected_rows!r}, got {actual_rows!r}"
            )
        if cursor != expected_cursor:
            result["errors"].append(
                f"cursor expected {expected_cursor!r}, got {cursor!r}"
            )
        if target.proc.poll() is not None:
            result["errors"].append(
                f"MAME exited early with {target.proc.returncode}"
            )

        diag, diag_raw = request_diag("snapshot")
        result["diag"] = diag
        result["diag_raw"] = diag_raw
        validate_diag(result, diag, payload)
        result["ok"] = not result["errors"]
    except Exception as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")
        result["traceback"] = traceback.format_exc()
        if target.proc is not None and target.proc.poll() is None:
            try:
                diag, diag_raw = request_diag("snapshot")
                result["diag"] = diag
                result["diag_raw"] = diag_raw
            except Exception as diag_exc:
                result["diag_error"] = repr(diag_exc)
    finally:
        target.close()
        clean_diag()
        time.sleep(0.10)
    return result


def shell_wrap_case() -> tuple[str, str, list]:
    for label, command, checks in shell_test.SHELL_TESTS:
        if label == "wrap":
            return label, command, checks
    raise RuntimeError("shell wrap case not found")


class RecordingShellTerminal(shell_test.Terminal):
    def __init__(self, conn):
        super().__init__(conn)
        self.sent = bytearray()

    def send(self, data: bytes):
        self.sent.extend(data)
        super().send(data)


def run_wrap_once(index: int) -> dict:
    _label, command, checks = shell_wrap_case()
    result = {
        "run": index,
        "mode": "wrap",
        "ok": False,
        "errors": [],
        "command": command,
    }
    clean_diag()
    write_diag_syms()
    try:
        shell_test.SCREEN.unlink()
    except FileNotFoundError:
        pass

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", PORT))
    server.listen(1)
    server.settimeout(60)
    mame = None
    mame_log = None
    mame_log_path = BUILD / f"serial_irq_wrap_mame_{index:03d}.log"
    conn = None
    term = None
    try:
        mame_log = mame_log_path.open("wb")
        mame = shell_test.launch_mame(
            stdout=mame_log, stderr=subprocess.STDOUT
        )
        conn, _ = server.accept()
        server.close()
        if not shell_test.wait_ready(conn):
            raise RuntimeError("terminal never answered ESC[6n")
        term = RecordingShellTerminal(conn)
        term.clear()
        shell_test.wait_settle(min_stable=0.6, timeout=6)
        reset_diag()
        term.sent.clear()

        shell_test.run_command(term, command, "vt100")
        lines, raw = shell_test.wait_settle()
        failures = shell_test.apply_checks(lines, checks)
        nonblank = [
            f"{index + 1}:{line.rstrip()}"
            for index, line in enumerate(lines)
            if line.rstrip()
        ]
        result.update(
            {
                "screen_nonblank": nonblank,
                "screen_raw": raw,
                "check_failures": failures,
                "payload_hex": bytes(term.sent).hex(),
                "mame_returncode": mame.poll(),
            }
        )
        result["errors"].extend(failures)
        if mame.poll() is not None:
            result["errors"].append(f"MAME exited early with {mame.returncode}")

        diag, diag_raw = request_diag("snapshot")
        result["diag"] = diag
        result["diag_raw"] = diag_raw
        validate_diag(result, diag, bytes(term.sent))
        result["ok"] = not result["errors"]
    except Exception as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")
        result["traceback"] = traceback.format_exc()
        if mame is not None and mame.poll() is None:
            try:
                diag, diag_raw = request_diag("snapshot")
                result["diag"] = diag
                result["diag_raw"] = diag_raw
            except Exception as diag_exc:
                result["diag_error"] = repr(diag_exc)
    finally:
        if term is not None:
            term.close()
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass
        try:
            server.close()
        except OSError:
            pass
        if mame is not None and mame.poll() is None:
            mame.terminate()
            try:
                mame.wait(timeout=10)
            except subprocess.TimeoutExpired:
                mame.kill()
                mame.wait(timeout=5)
        if mame_log is not None:
            mame_log.close()
        try:
            log_text = mame_log_path.read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            log_text = ""
        if result["errors"] and log_text:
            result["mame_log"] = log_text
        elif not result["errors"]:
            try:
                mame_log_path.unlink()
            except FileNotFoundError:
                pass
        clean_diag()
        time.sleep(0.50)
    return result


def run_flow_once(mode: str, index: int) -> dict:
    if mode == "mixed":
        payload = MIXED_PAYLOAD
        sentinel = MIXED_SENTINEL
        expected_reply = MIXED_REPLY
    else:
        payload = FLOW_PAYLOAD
        sentinel = FLOW_SENTINEL
        expected_reply = b""
    target_mame.PROBE_LUA = MIXED_PROBE if mode == "mixed" else FLOW_PROBE
    target = target_mame.MameTarget(
        port=PORT, boot_timeout=45.0, settle=6.0, ack_to=3.0
    )
    result = {"run": index, "mode": mode, "ok": False, "errors": []}
    clean_diag()
    write_diag_syms()
    try:
        target.open()
        target.reset()
        reset_diag()
        target.term.clear_buf()
        target.term.send(payload, chunk=len(payload))
        if mode == "mixed":
            KEYS_REQUEST.write_text("inject\n", encoding="ascii")
        expected_cursor = (1, len(sentinel) + 1)
        deadline = time.monotonic() + FLOW_SETTLE_TIMEOUT
        while True:
            text, state = capture_target_screen(target)
            rows = visible_rows(text)
            cursor = (state.get("cur_row"), state.get("cur_col"))
            actual_rows = [row.split(":", 1)[1] for row in rows]
            reply = target.term.peek()
            if mode == "mixed":
                wire_ok = (
                    reply.count(b"K") == 1
                    and reply.count(b"\r") == 1
                    and reply.replace(b"K", b"").replace(b"\r", b"")
                    == expected_reply
                )
            else:
                wire_ok = reply == expected_reply
            if (
                actual_rows == [sentinel.decode("ascii")]
                and cursor == expected_cursor
                and wire_ok
            ):
                break
            if target.proc.poll() is not None or time.monotonic() >= deadline:
                break
        result.update(
            {
                "payload_len": len(payload),
                "reply_hex": reply.hex(),
                "reply_repr": repr(reply),
                "expected_reply_hex": expected_reply.hex(),
                "screen_nonblank": rows,
                "cursor": list(cursor),
                "state": state,
                "mame_returncode": target.proc.poll(),
            }
        )
        if not wire_ok:
            if mode == "mixed":
                result["errors"].append(
                    "wire did not contain the exact replies plus keyboard K/CR"
                )
            else:
                result["errors"].append(
                    f"wire expected {expected_reply!r}, got {reply!r}"
                )
        if actual_rows != [sentinel.decode("ascii")]:
            result["errors"].append(
                f"screen expected flow sentinel, got {actual_rows!r}"
            )
        if cursor != expected_cursor:
            result["errors"].append(
                f"cursor expected {expected_cursor!r}, got {cursor!r}"
            )

        diag, diag_raw = request_diag("snapshot")
        result["diag"] = diag
        result["diag_raw"] = diag_raw
        validate_diag(result, diag, payload)
        if diag.get("null_modem_flow_control") != "4":
            result["errors"].append(f"MAME flow control inactive: {diag!r}")
        try:
            xoff_count = int(diag.get("tx_xoff_writes", "0"))
            xon_count = int(diag.get("tx_xon_writes", "0"))
        except ValueError:
            xoff_count = xon_count = -1
        if xoff_count < 1 or xon_count != xoff_count:
            result["errors"].append(
                f"expected paired XOFF/XON crossings, got {xoff_count}/{xon_count}"
            )
        if diag.get("xoff_sent") != "0":
            result["errors"].append(f"flow-control state did not resume: {diag!r}")
        if int(diag.get("rx_page2_aux_publications", "0")) < 1:
            result["errors"].append(
                "flow traffic never interrupted AUX-selected rendering"
            )
        result["ok"] = not result["errors"]
    except Exception as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")
        result["traceback"] = traceback.format_exc()
    finally:
        target.close()
        target_mame.PROBE_LUA = CONF_PROBE
        clean_diag()
        time.sleep(0.10)
    return result


def run_lifecycle_once(index: int) -> dict:
    target_mame.PROBE_LUA = CONF_PROBE
    target: target_mame.MameTarget | None = None
    result = {"run": index, "mode": "lifecycle", "ok": False, "errors": []}
    clean_diag()
    write_diag_syms()
    try:
        target = target_mame.MameTarget(
            port=PORT, boot_timeout=45.0, settle=4.0, ack_to=3.0
        )
        target.open()
        target.reset()
        reset_diag()
        target.term.clear_buf()

        diag, diag_raw = request_diag("snapshot")
        result["diag"] = diag
        result["diag_raw"] = diag_raw
        validate_diag(result, diag)

        foreign, foreign_raw = request_diag("foreign_irq", timeout=5.0)
        result["foreign_irq"] = foreign
        result["foreign_irq_raw"] = foreign_raw
        for field in (
            "foreign_chain_count",
            "foreign_frame_valid",
            "foreign_chain_registers",
            "foreign_rti_registers",
            "foreign_resumed",
            "foreign_ok",
        ):
            if foreign.get(field) != "1":
                result["errors"].append(
                    f"foreign IRQ invariant {field} failed: {foreign!r}"
                )

        # The synthetic probe deliberately owns the CPU until this fresh MAME
        # instance closes. Exercise the real reset path on an unmodified boot.
        target.close()
        target = None
        clean_diag()
        time.sleep(0.10)

        target = target_mame.MameTarget(
            port=PORT, boot_timeout=45.0, settle=4.0, ack_to=3.0
        )
        target.open()
        target.reset()
        reset_diag()
        target.term.clear_buf()
        reset_diag_before, reset_diag_raw = request_diag("snapshot")
        result["reset_diag"] = reset_diag_before
        result["reset_diag_raw"] = reset_diag_raw
        validate_diag(result, reset_diag_before)

        target.term.send(b"\x1b[c", chunk=3)
        deadline = time.monotonic() + 2.0
        while target.term.peek() != DA_REPLY and time.monotonic() < deadline:
            time.sleep(0.02)
        pre_reset_reply = target.term.peek()
        result["pre_reset_reply_hex"] = pre_reset_reply.hex()
        if pre_reset_reply != DA_REPLY:
            result["errors"].append(
                f"terminal did not answer before Ctrl-Reset: {pre_reset_reply!r}"
            )

        reset, reset_raw = request_diag("ctrl_reset", timeout=10.0)
        result["ctrl_reset"] = reset
        result["ctrl_reset_raw"] = reset_raw
        for field in (
            "irq_vector_restored",
            "reset_vector_restored",
            "reset_vector_valid",
            "acia_irq_clear",
            "isr_removed",
            "ctrl_reset_ok",
        ):
            if reset.get(field) != "1":
                result["errors"].append(
                    f"Ctrl-Reset invariant {field} failed: {reset!r}"
                )
        if reset.get("acia_command") != "0A":
            result["errors"].append(
                f"Ctrl-Reset left ACIA command {reset.get('acia_command')!r}"
            )
        if target.proc.poll() is not None:
            result["errors"].append(
                f"MAME exited early with {target.proc.returncode}"
            )
        result["ok"] = not result["errors"]
    except Exception as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")
        result["traceback"] = traceback.format_exc()
    finally:
        if target is not None:
            target.close()
        target_mame.PROBE_LUA = CONF_PROBE
        clean_diag()
        time.sleep(0.10)
    return result


def write_results(path: pathlib.Path, mode: str, runs: list[dict]) -> None:
    document = {
        "mode": mode,
        "port": PORT,
        "head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "runs": runs,
        "failures": sum(not run["ok"] for run in runs),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode", choices=("da", "cpr", "wrap", "flow", "mixed", "lifecycle")
    )
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()
    output = args.output or BUILD / f"serial_irq_{args.mode}_results.json"

    if args.mode == "wrap":
        subprocess.run(
            ["wsl.exe", "-e", "bash", "-c", "true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )

    results = []
    for index in range(1, args.runs + 1):
        if args.mode == "wrap":
            result = run_wrap_once(index)
        elif args.mode in ("flow", "mixed"):
            result = run_flow_once(args.mode, index)
        elif args.mode == "lifecycle":
            result = run_lifecycle_once(index)
        else:
            result = run_wire_once(args.mode, index)
        results.append(result)
        write_results(output, args.mode, results)
        diag = result.get("diag", {})
        reply_display = result.get("reply_repr", "-")
        if len(reply_display) > 100:
            reply_display = f"<{len(bytes.fromhex(result['reply_hex']))} bytes>"
        print(
            f"[{args.mode.upper()} {index:03d}/{args.runs:03d}] "
            f"{'PASS' if result['ok'] else 'FAIL'} "
            f"reply={reply_display} "
            f"screen={result.get('screen_nonblank', [])!r} "
            f"errors={diag.get('error_bits_seen', '?')} "
            f"cmd05={diag.get('command_05_writes', '?')} "
            f"cmd09={diag.get('command_09_writes', '?')}",
            flush=True,
        )
        for error in result["errors"]:
            print(f"  - {error}", flush=True)

    failures = sum(not result["ok"] for result in results)
    print(
        f"{args.mode}: {len(results) - failures}/{len(results)} passed; "
        f"failures={failures}; results={output}",
        flush=True,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
