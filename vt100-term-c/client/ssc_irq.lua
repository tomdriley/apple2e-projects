-- Enable the Super Serial Card's interrupt output (SW2:6).
--
-- MAME models the real SSC switch and defaults it to Off. Interrupt-driven
-- firmware therefore cannot receive the 6551's IRQ until the switch is turned
-- On. The card is installed in slot 2 by every launcher in this project.
local port_tag = ":sl2:ssc:DSWX"
local port = manager.machine.ioport.ports[port_tag]
if not port then
    error("Super Serial Card switch port not found: " .. port_tag)
end

local field = port.fields["Interrupts"]
if not field then
    error("Super Serial Card Interrupts switch not found")
end

field.user_value = 0 -- On; MAME's source defines Off as mask value $04.
if field.user_value ~= 0 then
    error("failed to enable Super Serial Card interrupts")
end
