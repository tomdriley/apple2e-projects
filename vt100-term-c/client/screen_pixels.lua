-- Shared helpers for reading the Apple IIe 80-column text screen as pixels.
--
-- In 80-column mode the emulated screen is exactly 560x192 px and renders two
-- levels only (black 0xFF000000 / white 0xFFFFFFFF), so each character cell is a
-- clean 7x8 = 56-pixel on/off bitmap. Two firmware properties make this a
-- reliable substitute for reading the old $7000 shadow buffer:
--   * it never uses the flashing character range ($40-$7F display codes), so a
--     cell's pixels are identical every frame, and
--   * it draws no cursor into video RAM, so the pixels are exactly the glyphs.
-- A cell's 56-bit fingerprint therefore uniquely and stably identifies its
-- glyph; client/glyphs80.lua maps each fingerprint to its ASCII character.
--
-- The pixels() buffer is width*height little-endian 32-bit BGRA words; the first
-- byte of each word (blue) is 0x00 for a black pixel and 0xFF for a white one,
-- so a single byte read per pixel decides on/off.
local M = {}

M.COLS, M.ROWS = 80, 24
M.CW, M.CH = 7, 8            -- pixels per cell (width, height)
M.WIDTH, M.HEIGHT = 560, 192

-- 56-bit fingerprint of the cell at row r, col c (0-based) from a pixels()
-- buffer whose row stride is w pixels. Bit index runs y*CW + x, y down, x right.
function M.cell_fp(buf, w, r, c)
    local base = (r * M.CH * w + c * M.CW) * 4 + 1  -- 1-based blue byte of (x0,y0)
    local stride = w * 4
    local fp, bit = 0, 0
    for _ = 0, M.CH - 1 do
        local p = base
        for _ = 0, M.CW - 1 do
            if string.byte(buf, p) ~= 0 then fp = fp | (1 << bit) end
            p = p + 4
            bit = bit + 1
        end
        base = base + stride
    end
    return fp
end

-- Return the active screen device (80-column text output).
function M.screen()
    return manager.machine.screens[":screen"]
end

-- Decode the whole screen to 24 right-trimmed text lines using a fingerprint->
-- ASCII map. Unknown fingerprints (should not happen) render as space.
function M.decode(buf, w, glyphs)
    local lines = {}
    for r = 0, M.ROWS - 1 do
        local s = {}
        for c = 0, M.COLS - 1 do
            s[c + 1] = string.char(glyphs[M.cell_fp(buf, w, r, c)] or 0x20)
        end
        lines[#lines + 1] = (table.concat(s)):gsub("%s+$", "")
    end
    return table.concat(lines, "\n") .. "\n"
end

return M
