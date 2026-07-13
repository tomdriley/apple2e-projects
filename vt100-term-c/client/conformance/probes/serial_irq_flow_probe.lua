dofile("client/conformance/probes/conformance_probe.lua")

local port = manager.machine.ioport.ports[":sl2:ssc:rs232:null_modem:FLOW_CONTROL"]
assert(port, "MAME null-modem FLOW_CONTROL port not found")
local field = port.fields["Flow Control"]
assert(field, "MAME null-modem Flow Control field not found")
field.user_value = 4 -- Consume XOFF/XON inside MAME and pause/resume host input.
assert(field.user_value == 4, "failed to enable MAME null-modem XON/XOFF")

dofile("client/serial_irq_diag.lua")
