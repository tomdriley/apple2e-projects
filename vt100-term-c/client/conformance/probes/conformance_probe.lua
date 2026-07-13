-- Conformance probe for the Apple IIe VT100 firmware under MAME.
--
-- Extends client/screen_watch.lua into the automated read-back channel the
-- conformance runner needs (issue #13). Every snapshot writes ONE file,
-- build/conf_probe.txt, containing three planes so the Python MameTarget can
-- reconstruct a model.Screen with no human in the loop:
--
--   SEQ <n>            monotonically increasing; lets the runner wait for a
--                      snapshot taken *after* the input settled (freshness).
--   SCREEN             24 rows x 80 cols, the glyph plane (folded to ASCII).
--   ATTR               24 rows x 80 cols of '0'/'1', the inverse-video plane
--                      (a raw video byte < 0x80 is an inverse cell).
--   STATE              "name value" per firmware variable, for symbols listed
--                      in build/conf_syms.txt (name=hexaddr). Absent file or
--                      symbol => the STATE section is simply shorter; the
--                      runner degrades gracefully. This includes the cursor_*
--                      group (cursor_visible/shown/col/row/saved) that backs
--                      DECTCEM verification and the visible-cursor overlay strip
--                      done in target_mame.py; symbols are read generically, so
--                      no change is needed here to expose them.
--   END                sentinel so a partial read is detected and retried.
--
-- Rows are emitted at full width (NOT right-trimmed) so a cell/attr assertion
-- at a trailing-space column still lines up. Banking mirrors screen_watch.lua:
-- the 80-col page is split AUX(even)/MAIN(odd) via PAGE2, toggled atomically
-- from a machine-frame notifier (CPU paused between frames).
dofile("client/ssc_irq.lua")

local ROWBASE = {
    0x0400, 0x0480, 0x0500, 0x0580, 0x0600, 0x0680, 0x0700, 0x0780,
    0x0428, 0x04A8, 0x0528, 0x05A8, 0x0628, 0x06A8, 0x0728, 0x07A8,
    0x0450, 0x04D0, 0x0550, 0x05D0, 0x0650, 0x06D0, 0x0750, 0x07D0,
}
local P2ON, P2OFF, RDPAGE2 = 0xC055, 0xC054, 0xC01C
local mem = manager.machine.devices[":maincpu"].spaces["program"]

local OUT  = "build/conf_probe.txt"
local SYMS = "build/conf_syms.txt"

-- Turn a raw video byte into a printable ASCII code, matching the firmware's
-- glyph encoding (see screen_watch.lua): high bit set = normal ASCII; $00-$1F
-- = inverse @A-Z; $20-$3F = inverse space/digit/symbol. Non-printables fold to
-- a space so the plane is always parseable text.
local function to_ascii(raw)
    local b
    if raw >= 0x80 then
        b = raw & 0x7f
    elseif raw < 0x20 then
        b = raw + 0x40
    else
        b = raw
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

-- Parse build/conf_syms.txt ("name=hexaddr" per line) into an ordered list.
-- Re-read each snapshot so a rebuild/rerun picks up new addresses without
-- restarting MAME; cheap (a handful of lines).
local function read_syms()
    local syms = {}
    local f = io.open(SYMS, "r")
    if not f then return syms end
    for line in f:lines() do
        local name, addr = line:match("^%s*([%w_]+)%s*=%s*%$?(%x+)%s*$")
        if name and addr then
            syms[#syms + 1] = { name = name, addr = tonumber(addr, 16) }
        end
    end
    f:close()
    return syms
end

local seq = 0

local function snapshot()
    -- Read both banks with the firmware's PAGE2 restored afterwards.
    local was_on = mem:read_u8(RDPAGE2) >= 0x80
    mem:write_u8(P2ON, 0)          -- AUX: even columns
    local aux = read_bank()
    mem:write_u8(P2OFF, 0)         -- MAIN: odd columns
    local main = read_bank()
    mem:write_u8(was_on and P2ON or P2OFF, 0)   -- restore

    seq = seq + 1
    local out = { "SEQ " .. seq, "SCREEN" }

    -- Glyph plane: full 80 columns, no trimming.
    for r = 1, 24 do
        local a, m = aux[r], main[r]
        local s = {}
        for i = 0, 39 do
            s[#s + 1] = string.char(to_ascii(a[i]))   -- even column 2i
            s[#s + 1] = string.char(to_ascii(m[i]))   -- odd column 2i+1
        end
        out[#out + 1] = table.concat(s)
    end

    -- Inverse plane: '1' where the raw video byte is inverse (< 0x80).
    out[#out + 1] = "ATTR"
    for r = 1, 24 do
        local a, m = aux[r], main[r]
        local s = {}
        for i = 0, 39 do
            s[#s + 1] = (a[i] < 0x80) and "1" or "0"
            s[#s + 1] = (m[i] < 0x80) and "1" or "0"
        end
        out[#out + 1] = table.concat(s)
    end

    -- State plane: read each known firmware variable as a byte.
    out[#out + 1] = "STATE"
    for _, sym in ipairs(read_syms()) do
        out[#out + 1] = sym.name .. " " .. tostring(mem:read_u8(sym.addr))
    end

    out[#out + 1] = "END"
    out[#out + 1] = ""   -- trailing newline

    local f = io.open(OUT, "w")
    if f then
        f:write(table.concat(out, "\n"))
        f:close()
    end
end

local frames = 0
_conf_sub = emu.add_machine_frame_notifier(function()
    frames = frames + 1
    -- pcall so a transient file-lock error can never kill the notifier.
    if frames % 6 == 0 then pcall(snapshot) end   -- ~10 Hz
end)
