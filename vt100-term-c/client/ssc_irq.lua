-- Enable the Super Serial Card's IRQ output (physical switch SW2:6).
--
-- MAME models the switch and defaults it to Off. The firmware can enable the
-- 6551 receiver interrupt internally only when this external gate is On.
local tag = ":sl2:ssc:DSWX"
local port = manager.machine.ioport.ports[tag]
if not port then
    error("Super Serial Card switch port not found: " .. tag)
end

local field = port.fields["Interrupts"]
if not field then
    error("Super Serial Card Interrupts switch not found")
end

field.user_value = 0 -- On; MAME defines Off as mask value $04.
if field.user_value ~= 0 then
    error("failed to enable Super Serial Card interrupts")
end
