-- Benchmark tap: timestamp (in emulated seconds) every DSR reply the terminal
-- transmits, so bench.py can measure render time independent of host wall-clock
-- and MAME's throttle. The terminal answers ESC[6n with ESC[<row>;<col>R; the
-- 'R' (0x52) terminator is only ever emitted as the end of a DSR reply, so a
-- write of 0x52 to the ACIA data register marks "finished rendering everything
-- received so far". Each timestamp is appended to build/bench_ticks.txt.
--
-- MAME wires the Super Serial Card into slot 2, whose 6551 data register is at
-- $C0A8 ($C088 + slot*16). The firmware auto-detects the slot but resolves to
-- the same address here.
local script_source = debug.getinfo(1, "S").source
local script_path = script_source:sub(1, 1) == "@" and script_source:sub(2) or script_source
local script_dir = script_path:match("^(.*)[/\\]") or "."
dofile(script_dir .. "/ssc_irq.lua")

local OUT  = "build/bench_ticks.txt"
local DATA = 0xC0A8

local space = manager.machine.devices[":maincpu"].spaces["program"]

-- Truncate the tick log at startup.
local f0 = io.open(OUT, "w")
if f0 then f0:close() end

_bench_tap = space:install_write_tap(DATA, DATA, "benchR", function(offset, data, mask)
    if (data & 0xff) == 0x52 then
        local t = manager.machine.time:as_double()
        local f = io.open(OUT, "a")
        if f then
            f:write(string.format("%.9f\n", t))
            f:close()
        end
    end
end)
