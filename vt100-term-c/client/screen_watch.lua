-- Snapshot the Apple IIe 80-column screen to build/screen.txt by reading the
-- real video page directly from RAM -- no firmware $7000 shadow buffer required.
--
-- The 80-column text page is split across two banks selected by the PAGE2 soft
-- switch: even columns live in AUX, odd columns in MAIN, 40 bytes per bank per
-- row (see docs/80COLUMN.md). An earlier version believed this page could not be
-- read externally without racing the terminal, so the firmware mirrored every
-- glyph into a linear $7000 shadow. That shadow cost real 6502 time on every
-- render (it roughly doubled every scroll), so it was removed.
--
-- We can read both banks safely from a machine-frame notifier: the notifier runs
-- with the CPU paused between frames, so toggling PAGE2 here is atomic with
-- respect to the running firmware. With 80STORE on, PAGE2 only steers the CPU's
-- view of $0400-$07FF -- it does NOT change what is displayed -- so flipping it
-- to read AUX then restoring the firmware's prior PAGE2 state disturbs neither
-- the display nor the firmware. Reading RAM is synchronous with the CPU, so
-- (unlike screen:pixels(), which lags under -video none) the snapshot always
-- reflects exactly what the firmware has written so far.
local script_source = debug.getinfo(1, "S").source
local script_path = script_source:sub(1, 1) == "@" and script_source:sub(2) or script_source
local script_dir = script_path:match("^(.*)[/\\]") or "."
dofile(script_dir .. "/ssc_irq.lua")

local ROWBASE = {
    0x0400, 0x0480, 0x0500, 0x0580, 0x0600, 0x0680, 0x0700, 0x0780,
    0x0428, 0x04A8, 0x0528, 0x05A8, 0x0628, 0x06A8, 0x0728, 0x07A8,
    0x0450, 0x04D0, 0x0550, 0x05D0, 0x0650, 0x06D0, 0x0750, 0x07D0,
}
local P2ON, P2OFF, RDPAGE2 = 0xC055, 0xC054, 0xC01C
local mem = manager.machine.devices[":maincpu"].spaces["program"]

-- Turn a raw video byte into a printable ASCII code, matching the firmware's
-- glyph encoding: normal text has the high bit set; $00-$1F are inverse
-- upper-case/@, $20-$3F are inverse space/digit/symbol.
local function to_ascii(raw)
    local b
    if raw >= 0x80 then
        b = raw & 0x7f          -- normal high-bit ASCII
    elseif raw < 0x20 then
        b = raw + 0x40          -- inverse @A-Z ($00-$1F)
    else
        b = raw                 -- inverse space/digit/symbol ($20-$3F)
    end
    if b < 0x20 or b == 0x7f then b = 0x20 end
    return b
end

local function read_bank()
    local rows = {}
    for r = 1, 24 do
        local base = ROWBASE[r]
        local row = {}
        for i = 0, 39 do row[i] = mem:read_u8(base + i) end
        rows[r] = row
    end
    return rows
end

local function snapshot()
    -- Remember the firmware's current PAGE2 so we can put it back; during a busy
    -- stream the notifier may fire while the firmware has AUX banked in mid-glyph.
    local was_on = mem:read_u8(RDPAGE2) >= 0x80
    mem:write_u8(P2ON, 0)          -- AUX: even columns
    local aux = read_bank()
    mem:write_u8(P2OFF, 0)         -- MAIN: odd columns
    local main = read_bank()
    mem:write_u8(was_on and P2ON or P2OFF, 0)   -- restore

    local parts = {}
    for r = 1, 24 do
        local a, m = aux[r], main[r]
        local s = {}
        for i = 0, 39 do
            s[#s + 1] = string.char(to_ascii(a[i]))   -- even column 2i
            s[#s + 1] = string.char(to_ascii(m[i]))   -- odd column 2i+1
        end
        parts[#parts + 1] = (table.concat(s)):gsub("%s+$", "")
    end
    local text = table.concat(parts, "\n") .. "\n"
    -- Write in one shot (truncating). A rare partial read is harmless: the
    -- harness waits for the screen to settle and retries.
    local f = io.open("build/screen.txt", "w")
    if f then
        f:write(text)
        f:close()
    end
end

local frames = 0
_watch_sub = emu.add_machine_frame_notifier(function()
    frames = frames + 1
    -- pcall so a transient file-lock error can never kill the notifier (which
    -- would freeze screen.txt for the rest of the run).
    if frames % 15 == 0 then pcall(snapshot) end   -- ~4 Hz
end)
