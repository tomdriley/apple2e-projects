-- Snapshot the Apple IIe 80-column screen to build/screen.txt by decoding the
-- emulated video PIXELS -- no firmware $7000 shadow buffer required.
--
-- In 80-column text mode the screen is a clean 560x192 on/off image (7x8 px per
-- cell). The firmware draws no cursor and never uses the flashing character
-- range, so every cell is a stable bitmap: we fingerprint each cell
-- (client/screen_pixels.lua) and map it to a character with the generated
-- client/glyphs80.lua table. This replaces the old shadow read, so the firmware
-- no longer has to mirror every glyph into RAM at $7000.
--
-- MAME's pixel bitmap can briefly lag the CPU during a repaint, but the Python
-- harness waits for build/screen.txt to stop changing before it asserts, so a
-- transient half-drawn frame is superseded before it is read. We therefore just
-- publish every snapshot (like the old shadow reader did) -- crucially we must
-- NOT suppress writes while the screen is in motion, or a fast scroll would leave
-- screen.txt stale and the harness would "settle" on old content.
local M = dofile("client/screen_pixels.lua")
local GLYPHS = dofile("client/glyphs80.lua")

local function snapshot()
    local scr = M.screen()
    if not scr then return end
    local buf = scr:pixels()
    local text = M.decode(buf, scr.width, GLYPHS)
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
    if frames % 15 == 0 then pcall(snapshot) end   -- ~4 Hz, matches shadow reader
end)
