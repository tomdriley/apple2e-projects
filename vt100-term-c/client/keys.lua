-- Inject a known key sequence once the terminal is up, so the host-side test can
-- verify what the Apple transmits over serial for each key.
--
-- 'A' and Return go through the natural keyboard (they map cleanly to ASCII).
-- The arrow keys are not in the natural-keyboard table, so we press their input
-- ports directly. Field names are the Unicode arrows, built from bytes here to
-- avoid any source-encoding surprises.
local script_source = debug.getinfo(1, "S").source
local script_path = script_source:sub(1, 1) == "@" and script_source:sub(2) or script_source
local script_dir = script_path:match("^(.*)[/\\]") or "."
dofile(script_dir .. "/ssc_irq.lua")

local LEFT  = string.char(0xE2, 0x86, 0x90) -- U+2190
local UP    = string.char(0xE2, 0x86, 0x91) -- U+2191
local RIGHT = string.char(0xE2, 0x86, 0x92) -- U+2192
local DOWN  = string.char(0xE2, 0x86, 0x93) -- U+2193

-- order: left, right, up, down
local arrows = {
    { ":X7", LEFT },
    { ":X7", RIGHT },
    { ":X6", UP },
    { ":X7", DOWN },
}

local frames = 0
local typed = false

_keys_sub = emu.add_machine_frame_notifier(function()
    frames = frames + 1

    if frames == 1000 and not typed then
        typed = true
        local nk = manager.machine.natkeyboard
        nk:post("A")
        nk:post(string.char(13)) -- Return / CR
    end

    if frames >= 1100 then
        local rel = frames - 1100
        local which = math.floor(rel / 24) -- 24 frames per arrow
        local step = rel % 24
        if which < #arrows then
            local port = manager.machine.ioport.ports[arrows[which + 1][1]]
            local field = port.fields[arrows[which + 1][2]]
            if field then
                if step == 0 then
                    field:set_value(1) -- press
                elseif step == 6 then
                    field:set_value(0) -- release
                end
            end
        end
    end
end)
