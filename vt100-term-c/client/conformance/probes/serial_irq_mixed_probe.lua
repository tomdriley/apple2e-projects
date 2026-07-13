dofile("client/conformance/probes/serial_irq_flow_probe.lua")

local request_path = "build/serial_irq_keys_request"

local function inject_keys_if_requested()
    local request = io.open(request_path, "r")
    if not request then return end
    request:close()
    os.remove(request_path)
    manager.machine.natkeyboard:post("K" .. string.char(13))
end

_serial_irq_keys_notifier = emu.add_machine_frame_notifier(function()
    pcall(inject_keys_if_requested)
end)
