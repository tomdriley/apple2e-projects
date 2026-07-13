-- Pass-through diagnostics for the interrupt-driven 6551 stress harness.
--
-- The taps observe accesses already made by the firmware; they never perform an
-- extra ACIA read while a test is running. Firmware addresses come from the
-- ld65 label file via build/serial_irq_syms.txt, so ordinary layout changes do
-- not require editing this script.
local mem = manager.machine.devices[":maincpu"].spaces["program"]
local request_path = "build/serial_irq_diag_request"
local output_path = "build/serial_irq_diag.txt"
local syms_path = "build/serial_irq_syms.txt"

local function read_syms()
    local syms = {}
    local f = io.open(syms_path, "r")
    if not f then return syms end
    for line in f:lines() do
        local name, addr = line:match("^%s*([%w_]+)%s*=%s*%$?(%x+)%s*$")
        if name and addr then syms[name] = tonumber(addr, 16) end
    end
    f:close()
    return syms
end

local syms = read_syms()
local expected_isr = nil
local status_reads = 0
local last_status = 0
local error_bits_seen = 0
local command_05_writes = 0
local command_09_writes = 0
local command_other_writes = 0
local command_sequence = {}
local baseline_r_head = 0
local rx_publications = 0
local rx_publication_bytes = {}
local rx_page2_aux_publications = 0
local tx_xoff_writes = 0
local tx_xon_writes = 0
local trace = {}

local function trace_event(kind, data)
    if #trace < 4096 then
        trace[#trace + 1] = string.format("%s:%02X", kind, data & 0xFF)
    end
end

local function vector()
    return mem:read_u8(0x03FE) | (mem:read_u8(0x03FF) << 8)
end

local vector_history = { string.format("INIT:%04X", vector()) }

_serial_irq_status_read_tap = mem:install_read_tap(
    0xC0A9,
    0xC0A9,
    "serial IRQ ACIA status reads",
    function(_offset, data, _mask)
        last_status = data & 0xFF
        status_reads = status_reads + 1
        error_bits_seen = error_bits_seen | (last_status & 0x07)
        trace_event("ST", data)
    end
)

_serial_irq_data_read_tap = mem:install_read_tap(
    0xC0A8,
    0xC0A8,
    "serial IRQ ACIA RX reads",
    function(_offset, data, _mask) trace_event("RX", data) end
)

_serial_irq_data_write_tap = mem:install_write_tap(
    0xC0A8,
    0xC0A8,
    "serial IRQ ACIA TX writes",
    function(_offset, data, _mask)
        local value = data & 0xFF
        if value == 0x13 then
            tx_xoff_writes = tx_xoff_writes + 1
        elseif value == 0x11 then
            tx_xon_writes = tx_xon_writes + 1
        end
        trace_event("TX", data)
    end
)

_serial_irq_command_write_tap = mem:install_write_tap(
    0xC0AA,
    0xC0AA,
    "serial IRQ ACIA command writes",
    function(_offset, data, _mask)
        local value = data & 0xFF
        command_sequence[#command_sequence + 1] = string.format("%02X", value)
        if value == 0x05 then
            command_05_writes = command_05_writes + 1
        elseif value == 0x09 then
            command_09_writes = command_09_writes + 1
        else
            command_other_writes = command_other_writes + 1
        end
        trace_event("CM", data)
    end
)

_serial_irq_vector_write_tap = mem:install_write_tap(
    0x03FE,
    0x03FF,
    "serial IRQ vector writes",
    function(offset, data, _mask)
        vector_history[#vector_history + 1] =
            string.format("%04X:%02X", offset, data & 0xFF)
    end
)

if syms.r_head then
    _serial_irq_rhead_write_tap = mem:install_write_tap(
        syms.r_head,
        syms.r_head,
        "serial IRQ RX head writes",
        function(_offset, data, _mask)
            rx_publications = rx_publications + 1
            trace_event("RH", data)
        end
    )
end

if syms.rx_ring then
    _serial_irq_rxring_write_tap = mem:install_write_tap(
        syms.rx_ring,
        syms.rx_ring + 0xFF,
        "serial IRQ RX ring writes",
        function(_offset, data, _mask)
            rx_publication_bytes[#rx_publication_bytes + 1] =
                string.format("%02X", data & 0xFF)
            if (mem:read_u8(0xC01C) & 0x80) ~= 0 then
                rx_page2_aux_publications = rx_page2_aux_publications + 1
            end
        end
    )
end

local function read_sym(name)
    local addr = syms[name]
    if not addr then return -1 end
    return mem:read_u8(addr)
end

local function count(head, tail)
    if head < 0 or tail < 0 then return -1 end
    return (head - tail) & 0xFF
end

local function reset_diagnostics()
    expected_isr = vector()
    status_reads = 0
    last_status = 0
    error_bits_seen = 0
    command_05_writes = 0
    command_09_writes = 0
    command_other_writes = 0
    command_sequence = {}
    baseline_r_head = read_sym("r_head")
    rx_publications = 0
    rx_publication_bytes = {}
    rx_page2_aux_publications = 0
    tx_xoff_writes = 0
    tx_xon_writes = 0
    trace = {}
end

local function write_reset_ack()
    local output = io.open(output_path, "w")
    if output then
        output:write("reset=1\n")
        output:close()
    end
end

local function snapshot()
    local irq_vector = vector()
    local r_head = read_sym("r_head")
    local r_tail = read_sym("r_tail")
    local t_head = read_sym("t_head")
    local t_tail = read_sym("t_tail")
    local xoff_sent = read_sym("xoff_sent")
    local tx_irq_active = read_sym("tx_irq_active")
    local old_irq_addr = syms.serial_old_irq
    local old_irq = old_irq_addr and
        (mem:read_u8(old_irq_addr) | (mem:read_u8(old_irq_addr + 1) << 8)) or -1
    local old_irq_bytes = ""
    if old_irq >= 0 then
        local bytes = {}
        for i = 0, 7 do
            bytes[#bytes + 1] = string.format("%02X", mem:read_u8(old_irq + i))
        end
        old_irq_bytes = table.concat(bytes)
    end
    local switch_port = manager.machine.ioport.ports[":sl2:ssc:DSWX"]
    local switch_field = switch_port and switch_port.fields["Interrupts"]
    local switch_value = switch_field and switch_field.user_value or -1
    local flow_port = manager.machine.ioport.ports[":sl2:ssc:rs232:null_modem:FLOW_CONTROL"]
    local flow_field = flow_port and flow_port.fields["Flow Control"]
    local flow_value = flow_field and flow_field.user_value or -1

    trace[#trace + 1] = "SNAP"
    local live_status = mem:read_u8(0xC0A9) -- The serial operation is complete.
    local command = mem:read_u8(0xC0AA)
    local control = mem:read_u8(0xC0AB)
    local lines = {
        string.format("irq_vector=%04X", irq_vector),
        string.format("irq_vector_expected=%04X", expected_isr or 0),
        string.format(
            "irq_vector_active=%d",
            expected_isr and irq_vector == expected_isr and 1 or 0
        ),
        string.format("ssc_interrupt_switch=%d", switch_value),
        string.format("null_modem_flow_control=%d", flow_value),
        string.format("acia_live_status=%02X", live_status),
        string.format("acia_command=%02X", command),
        string.format("acia_control=%02X", control),
        string.format("status_reads=%d", status_reads),
        string.format("last_status=%02X", last_status),
        string.format("error_bits_seen=%02X", error_bits_seen),
        string.format("command_05_writes=%d", command_05_writes),
        string.format("command_09_writes=%d", command_09_writes),
        string.format("command_other_writes=%d", command_other_writes),
        "command_sequence=" .. table.concat(command_sequence, ","),
        string.format("tx_xoff_writes=%d", tx_xoff_writes),
        string.format("tx_xon_writes=%d", tx_xon_writes),
        string.format("r_head=%d", r_head),
        string.format("r_tail=%d", r_tail),
        string.format("rx_count=%d", count(r_head, r_tail)),
        string.format("rx_published=%d", count(r_head, baseline_r_head)),
        string.format("rx_publications=%d", rx_publications),
        "rx_publication_bytes=" .. table.concat(rx_publication_bytes),
        string.format(
            "rx_page2_aux_publications=%d",
            rx_page2_aux_publications
        ),
        string.format("t_head=%d", t_head),
        string.format("t_tail=%d", t_tail),
        string.format("tx_count=%d", count(t_head, t_tail)),
        string.format("xoff_sent=%d", xoff_sent),
        string.format("tx_irq_active=%d", tx_irq_active),
        string.format("old_irq_vector=%04X", old_irq),
        "old_irq_bytes=" .. old_irq_bytes,
        string.format("chain_valid=%d", read_sym("serial_chain_valid")),
        string.format("isr_installed=%d", read_sym("serial_isr_installed")),
        string.format("page2=%02X", mem:read_u8(0xC01C)),
        "vector_history=" .. table.concat(vector_history, ","),
        "trace=" .. table.concat(trace, ","),
        "",
    }
    local output = io.open(output_path, "w")
    if output then
        output:write(table.concat(lines, "\n"))
        output:close()
    end
end

local function snapshot_if_requested()
    local request = io.open(request_path, "r")
    if not request then return end
    local action = request:read("*l") or "snapshot"
    request:close()
    os.remove(request_path)
    if action == "reset" then
        reset_diagnostics()
        write_reset_ack()
    else
        snapshot()
    end
end

_serial_irq_diag_notifier = emu.add_machine_frame_notifier(function()
    pcall(snapshot_if_requested)
end)
