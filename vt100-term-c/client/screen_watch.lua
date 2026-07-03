-- Continuously snapshot the Apple IIe terminal screen to build/screen.txt so the
-- Python test harness can assert what the terminal rendered.
--
-- The real 80-column text page is split across two memory banks selected by the
-- PAGE2 soft switch, which cannot be read externally without toggling PAGE2 and
-- racing the running terminal. Instead, the terminal firmware mirrors every
-- glyph into a plain, linear, non-banked buffer at $7000 (80 bytes per row, 24
-- rows). We just read that -- no bank switching, no side effects on the machine.
-- Written to a temp file then renamed so readers never see a partial file.
local SHADOW = 0x7000

local function snapshot()
    local mem = manager.machine.devices[":maincpu"].spaces["program"]
    local parts = {}
    for r = 0, 23 do
        local base = SHADOW + r * 80
        local s = {}
        for c = 0, 79 do
            local b = mem:read_u8(base + c) & 0x7f
            if b < 0x20 or b == 0x7f then b = 0x20 end
            s[#s + 1] = string.char(b)
        end
        parts[#parts + 1] = (table.concat(s)):gsub("%s+$", "")
    end
    local text = table.concat(parts, "\n") .. "\n"
    -- Write in one shot (truncating). We deliberately do NOT use a temp file +
    -- os.rename: on Windows rename won't overwrite, and os.remove throws if the
    -- reader has the file open. A rare partial read is harmless (the harness
    -- waits for the screen to settle and retries).
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
    if frames % 15 == 0 then pcall(snapshot) end  -- ~4 Hz
end)
