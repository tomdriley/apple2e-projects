-- Pass-through diagnostics for the interrupt-driven 6551 stress harness.
--
-- The taps observe accesses already made by the firmware; they never perform an
-- extra ACIA read while a test is running. Firmware addresses come from the
-- ld65 label file via build/serial_irq_syms.txt, so ordinary layout changes do
-- not require editing this script.
local mem = manager.machine.devices[":maincpu"].spaces["program"]
local cpu = manager.machine.devices[":maincpu"]
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
local lifecycle = nil
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
local tx_max_count = 0
local tx_reserved_xoff_writes = 0
local observed_t_tail = syms.t_tail and mem:read_u8(syms.t_tail) or nil
local observed_reset_low = mem:read_u8(0x03F2)
local observed_reset_high = mem:read_u8(0x03F3)
local observed_pwredup = mem:read_u8(0x03F4)
local reset_publish_tracking = false
local reset_vector_unsafe_writes = 0
local reset_vector_history = {}
local trace = {}

local function read_u16(addr)
    return mem:read_u8(addr) | (mem:read_u8(addr + 1) << 8)
end

local function write_u16(addr, value)
    mem:write_u8(addr, value & 0xFF)
    mem:write_u8(addr + 1, (value >> 8) & 0xFF)
end

local function write_lines(lines)
    local output = io.open(output_path, "w")
    if output then
        output:write(table.concat(lines, "\n") .. "\n")
        output:close()
    end
end

local function trace_event(kind, data)
    if #trace < 4096 then
        trace[#trace + 1] = string.format("%s:%02X", kind, data & 0xFF)
    end
end

local function vector()
    return read_u16(0x03FE)
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

local function record_reset_event(kind, value)
    if #reset_vector_history < 32 then
        reset_vector_history[#reset_vector_history + 1] =
            string.format("%s:%02X", kind, value & 0xFF)
    end
end

_serial_irq_reset_low_write_tap = mem:install_write_tap(
    0x03F2,
    0x03F2,
    "serial IRQ reset vector low writes",
    function(_offset, data, _mask)
        local value = data & 0xFF
        if syms.exit and value == (syms.exit & 0xFF) then
            reset_publish_tracking = true
        end
        if reset_publish_tracking then
            record_reset_event("LO", value)
            if value ~= observed_reset_low
                and ((observed_reset_high ~ 0xA5) & 0xFF) == observed_pwredup then
                reset_vector_unsafe_writes = reset_vector_unsafe_writes + 1
            end
        end
        observed_reset_low = value
    end
)

_serial_irq_reset_high_write_tap = mem:install_write_tap(
    0x03F3,
    0x03F3,
    "serial IRQ reset vector high writes",
    function(_offset, data, _mask)
        local value = data & 0xFF
        if syms.exit
            and value == ((syms.exit >> 8) & 0xFF)
            and observed_reset_low == (syms.exit & 0xFF) then
            reset_publish_tracking = true
        end
        if reset_publish_tracking then
            record_reset_event("HI", value)
            if value ~= observed_reset_high
                and ((value ~ 0xA5) & 0xFF) == observed_pwredup then
                reset_vector_unsafe_writes = reset_vector_unsafe_writes + 1
            end
        end
        observed_reset_high = value
    end
)

_serial_irq_pwredup_write_tap = mem:install_write_tap(
    0x03F4,
    0x03F4,
    "serial IRQ reset validity writes",
    function(_offset, data, _mask)
        local value = data & 0xFF
        local vector_addr = observed_reset_low | (observed_reset_high << 8)
        local valid = ((observed_reset_high ~ 0xA5) & 0xFF) == value
        if syms.exit and vector_addr == syms.exit and not valid then
            reset_publish_tracking = true
        end
        if reset_publish_tracking then
            record_reset_event("PW", value)
        end
        observed_pwredup = value
        if reset_publish_tracking and valid then
            reset_publish_tracking = false
        end
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

if syms.t_head and syms.t_tail then
    _serial_irq_thead_write_tap = mem:install_write_tap(
        syms.t_head,
        syms.t_head,
        "serial IRQ TX head writes",
        function(_offset, data, _mask)
            local occupancy = count(data & 0xFF, mem:read_u8(syms.t_tail))
            tx_max_count = math.max(tx_max_count, occupancy)
        end
    )
    _serial_irq_ttail_write_tap = mem:install_write_tap(
        syms.t_tail,
        syms.t_tail,
        "serial IRQ TX tail writes",
        function(_offset, data, _mask)
            local new_tail = data & 0xFF
            local occupancy = count(mem:read_u8(syms.t_head), new_tail)
            if observed_t_tail
                and new_tail == ((observed_t_tail - 1) & 0xFF)
                and occupancy == 255 then
                tx_reserved_xoff_writes = tx_reserved_xoff_writes + 1
            end
            observed_t_tail = new_tail
            tx_max_count = math.max(tx_max_count, occupancy)
        end
    )
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
    tx_max_count = 0
    tx_reserved_xoff_writes = 0
    observed_t_tail = read_sym("t_tail")
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
    local old_irq = old_irq_addr and read_u16(old_irq_addr) or -1
    local old_irq_bytes = ""
    if old_irq >= 0 then
        local bytes = {}
        for i = 0, 7 do
            bytes[#bytes + 1] = string.format("%02X", mem:read_u8(old_irq + i))
        end
        old_irq_bytes = table.concat(bytes)
    end
    local reset_vector = read_u16(0x03F2)
    local reset_expected = syms.exit or 0
    local reset_check = mem:read_u8(0x03F4)
    local reset_valid =
        ((((reset_vector >> 8) ~ 0xA5) & 0xFF) == reset_check) and 1 or 0
    local old_reset_addr = syms.serial_old_reset
    local old_reset = old_reset_addr and string.format(
        "%02X%02X%02X",
        mem:read_u8(old_reset_addr),
        mem:read_u8(old_reset_addr + 1),
        mem:read_u8(old_reset_addr + 2)
    ) or ""
    local tx_store_addr = syms.serial_tx_data_store
    local tx_store_opcode = tx_store_addr and mem:read_u8(tx_store_addr) or -1
    local tx_store_operand = tx_store_addr and read_u16(tx_store_addr + 1) or -1
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
        string.format("reset_vector=%04X", reset_vector),
        string.format("reset_vector_expected=%04X", reset_expected),
        string.format(
            "reset_vector_active=%d",
            reset_vector == reset_expected and 1 or 0
        ),
        string.format("reset_vector_valid=%d", reset_valid),
        string.format("reset_vector_unsafe_writes=%d", reset_vector_unsafe_writes),
        "reset_vector_history=" .. table.concat(reset_vector_history, ","),
        "old_reset_bytes=" .. old_reset,
        string.format("tx_store_opcode=%02X", tx_store_opcode),
        string.format("tx_store_operand=%04X", tx_store_operand),
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
        string.format("tx_max_count=%d", tx_max_count),
        string.format("tx_reserved_xoff_writes=%d", tx_reserved_xoff_writes),
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
    }
    write_lines(lines)
end

local function cpu_entry(primary, alternate)
    return cpu.state[primary] or (alternate and cpu.state[alternate])
end

-- $7000-$77FF is free RAM between the alternate-screen save and C stack.
local FOREIGN_ENTRY = 0x7000
local FOREIGN_DRIVER = 0x7020
local FOREIGN_PREDECESSOR = 0x7060
local FOREIGN_CONTINUATION = 0x70A0
local FOREIGN_LOOP = 0x70E0
local FOREIGN_CAPTURE = 0x7140

local function begin_foreign_irq()
    local required = {
        "serial_isr_entry",
        "serial_chain_target",
        "serial_chain_valid",
    }
    for _, name in ipairs(required) do
        if not syms[name] then
            write_lines({ "foreign_irq=1", "foreign_ok=0", "error=missing_" .. name })
            return
        end
    end

    local status = mem:read_u8(0xC0A9)
    if (status & 0x88) ~= 0 then
        write_lines({
            "foreign_irq=1",
            string.format("foreign_irq_status=%02X", status),
            "foreign_ok=0",
            "error=ACIA_not_idle",
        })
        return
    end

    local p = assert(cpu_entry("P"))
    local pc = assert(cpu_entry("PC"))

    -- The driver builds a hardware-compatible IRQ frame with real 6502 pushes,
    -- then enters the ISR with deterministic registers and flags. The predecessor
    -- records its entry ABI; the continuation records the post-RTI state.
    local test_a = 0x5A
    local test_x = 0x3C
    local test_y = 0xC3
    local live_p = 0x2D
    local stack_p = 0xA5
    local driver = {
        0x78,
        0xBA, 0x8E, 0x4C, 0x71,
        0xA9, (FOREIGN_CONTINUATION >> 8) & 0xFF, 0x48,
        0xA9, FOREIGN_CONTINUATION & 0xFF, 0x48,
        0xA9, stack_p, 0x48,
        0xBA, 0x8E, 0x4D, 0x71,
        0xA9, test_a, 0x85, 0x45,
        0xA2, test_x,
        0xA0, test_y,
        0xA9, live_p, 0x48, 0x28,
        0x4C, syms.serial_isr_entry & 0xFF,
        (syms.serial_isr_entry >> 8) & 0xFF,
    }
    local predecessor = {
        0x48, 0x08, 0x68, 0x8D, 0x44, 0x71,
        0x68, 0x8D, 0x40, 0x71,
        0x8E, 0x41, 0x71,
        0x8C, 0x42, 0x71,
        0xBA, 0x8E, 0x43, 0x71,
        0xBD, 0x01, 0x01, 0x8D, 0x50, 0x71,
        0xBD, 0x02, 0x01, 0x8D, 0x51, 0x71,
        0xBD, 0x03, 0x01, 0x8D, 0x52, 0x71,
        0xEE, 0x45, 0x71,
        0xAE, 0x41, 0x71,
        0xAD, 0x40, 0x71,
        0x40,
    }
    local continuation = {
        0x48, 0x08, 0x68, 0x8D, 0x49, 0x71,
        0x68, 0x8D, 0x46, 0x71,
        0x8E, 0x47, 0x71,
        0x8C, 0x48, 0x71,
        0xBA, 0x8E, 0x4A, 0x71,
        0xA9, 0x01, 0x8D, 0x4B, 0x71,
        0x4C, 0xE0, 0x70,
    }
    for addr = FOREIGN_ENTRY, FOREIGN_DRIVER - 1 do
        mem:write_u8(addr, 0xEA)
    end
    for i, value in ipairs(driver) do
        mem:write_u8(FOREIGN_DRIVER - 1 + i, value)
    end
    for i, value in ipairs(predecessor) do
        mem:write_u8(FOREIGN_PREDECESSOR - 1 + i, value)
    end
    for i, value in ipairs(continuation) do
        mem:write_u8(FOREIGN_CONTINUATION - 1 + i, value)
    end
    mem:write_u8(FOREIGN_LOOP, 0x4C)
    mem:write_u8(FOREIGN_LOOP + 1, FOREIGN_LOOP & 0xFF)
    mem:write_u8(FOREIGN_LOOP + 2, (FOREIGN_LOOP >> 8) & 0xFF)
    for addr = FOREIGN_CAPTURE, FOREIGN_CAPTURE + 18 do
        mem:write_u8(addr, 0)
    end

    local chain_addr = syms.serial_chain_target
    local saved_p = p.value & 0xFF
    lifecycle = {
        kind = "foreign",
        frames = 0,
        status = status,
        chain_addr = chain_addr,
        chain_low = mem:read_u8(chain_addr + 1),
        chain_high = mem:read_u8(chain_addr + 2),
        test_a = test_a,
        test_x = test_x,
        test_y = test_y,
        live_p = live_p,
        stack_p = stack_p,
    }

    write_u16(chain_addr + 1, FOREIGN_PREDECESSOR)
    p.value = (saved_p | 0x04) & 0xFF
    pc.value = FOREIGN_ENTRY
end

local function finish_foreign_irq(test)
    local count = mem:read_u8(FOREIGN_CAPTURE + 5)
    local chain_a = mem:read_u8(FOREIGN_CAPTURE)
    local chain_x = mem:read_u8(FOREIGN_CAPTURE + 1)
    local chain_y = mem:read_u8(FOREIGN_CAPTURE + 2)
    local chain_s = mem:read_u8(FOREIGN_CAPTURE + 3)
    local chain_p = mem:read_u8(FOREIGN_CAPTURE + 4)
    local post_a = mem:read_u8(FOREIGN_CAPTURE + 6)
    local post_x = mem:read_u8(FOREIGN_CAPTURE + 7)
    local post_y = mem:read_u8(FOREIGN_CAPTURE + 8)
    local post_p = mem:read_u8(FOREIGN_CAPTURE + 9)
    local post_s = mem:read_u8(FOREIGN_CAPTURE + 10)
    local driver_start_s = mem:read_u8(FOREIGN_CAPTURE + 12)
    local driver_frame_s = mem:read_u8(FOREIGN_CAPTURE + 13)
    local frame_p = mem:read_u8(FOREIGN_CAPTURE + 16)
    local frame_low = mem:read_u8(FOREIGN_CAPTURE + 17)
    local frame_high = mem:read_u8(FOREIGN_CAPTURE + 18)
    local frame_ok =
        frame_p == test.stack_p and
        frame_low == (FOREIGN_CONTINUATION & 0xFF) and
        frame_high == ((FOREIGN_CONTINUATION >> 8) & 0xFF)
    local chain_regs =
        chain_a == test.test_a and chain_x == test.test_x and
        chain_y == test.test_y and chain_s == driver_frame_s and
        (chain_p & 0xCF) == (test.live_p & 0xCF)
    local rti_regs =
        post_a == test.test_a and post_x == test.test_x and
        post_y == test.test_y and post_s == driver_start_s and
        (post_p & 0xCF) == (test.stack_p & 0xCF)
    local ok = count == 1 and frame_ok and chain_regs and rti_regs
    local lines = {
        "foreign_irq=1",
        string.format("foreign_irq_status=%02X", test.status),
        string.format("foreign_chain_count=%d", count),
        string.format("foreign_entry_a=%02X", chain_a),
        string.format("foreign_entry_x=%02X", chain_x),
        string.format("foreign_entry_y=%02X", chain_y),
        string.format("foreign_entry_s=%02X", chain_s),
        string.format("foreign_entry_p=%02X", chain_p),
        string.format("foreign_rti_a=%02X", post_a),
        string.format("foreign_rti_x=%02X", post_x),
        string.format("foreign_rti_y=%02X", post_y),
        string.format("foreign_rti_p=%02X", post_p),
        string.format("foreign_rti_s=%02X", post_s),
        string.format("foreign_expected_a=%02X", test.test_a),
        string.format("foreign_expected_x=%02X", test.test_x),
        string.format("foreign_expected_y=%02X", test.test_y),
        string.format("foreign_expected_entry_s=%02X", driver_frame_s),
        string.format("foreign_driver_start_s=%02X", driver_start_s),
        string.format("foreign_driver_frame_s=%02X", driver_frame_s),
        string.format("foreign_expected_entry_p=%02X", test.live_p),
        string.format("foreign_expected_rti_p=%02X", test.stack_p),
        string.format("foreign_expected_rti_s=%02X", driver_start_s),
        string.format(
            "foreign_frame=%02X,%02X,%02X",
            frame_p,
            frame_low,
            frame_high
        ),
        string.format("foreign_frame_valid=%d", frame_ok and 1 or 0),
        string.format("foreign_chain_registers=%d", chain_regs and 1 or 0),
        string.format("foreign_rti_registers=%d", rti_regs and 1 or 0),
        "foreign_resumed=1",
        string.format("foreign_ok=%d", ok and 1 or 0),
    }
    mem:write_u8(test.chain_addr + 1, test.chain_low)
    mem:write_u8(test.chain_addr + 2, test.chain_high)
    write_lines(lines)
    lifecycle = nil
end

local function begin_ctrl_reset()
    local port = manager.machine.ioport.ports[":keyb_special"]
    local control = port and port.fields["Control"]
    local reset = port and port.fields["RESET"]
    local old_irq_addr = syms.serial_old_irq
    local old_reset_addr = syms.serial_old_reset
    if not control or not reset or not old_irq_addr or not old_reset_addr then
        write_lines({
            "ctrl_reset=1",
            "ctrl_reset_ok=0",
            "error=missing_reset_input_or_symbol",
        })
        return
    end

    lifecycle = {
        kind = "ctrl_reset",
        frames = 0,
        control = control,
        reset = reset,
        expected_irq = read_u16(old_irq_addr),
        expected_reset = read_u16(old_reset_addr),
        expected_pwredup = mem:read_u8(old_reset_addr + 2),
    }
    -- Observe every restore write even if the firmware omits or misorders the
    -- PWREDUP invalidation that would normally arm publication tracking.
    reset_publish_tracking = true
    control:set_value(1)
    reset:set_value(1)
end

local function finish_ctrl_reset(test)
    local irq_vector = vector()
    local reset_vector = read_u16(0x03F2)
    local pwredup = mem:read_u8(0x03F4)
    local command = mem:read_u8(0xC0AA)
    local status = mem:read_u8(0xC0A9)
    local irq_restored = irq_vector == test.expected_irq
    local reset_restored =
        reset_vector == test.expected_reset and pwredup == test.expected_pwredup
    local reset_valid =
        ((((reset_vector >> 8) ~ 0xA5) & 0xFF) == pwredup)
    local irq_clear = (status & 0x80) == 0
    local removed = read_sym("serial_isr_installed") == 0
    local ok =
        irq_restored and reset_restored and reset_valid and irq_clear and
        removed and command == 0x0A and reset_vector_unsafe_writes == 0

    write_lines({
        "ctrl_reset=1",
        string.format("irq_vector=%04X", irq_vector),
        string.format("irq_vector_expected=%04X", test.expected_irq),
        string.format("irq_vector_restored=%d", irq_restored and 1 or 0),
        string.format("reset_vector=%04X", reset_vector),
        string.format("reset_vector_expected=%04X", test.expected_reset),
        string.format("reset_vector_restored=%d", reset_restored and 1 or 0),
        string.format("reset_vector_valid=%d", reset_valid and 1 or 0),
        string.format("reset_vector_unsafe_writes=%d", reset_vector_unsafe_writes),
        "reset_vector_history=" .. table.concat(reset_vector_history, ","),
        string.format("acia_command=%02X", command),
        string.format("acia_status=%02X", status),
        string.format("acia_irq_clear=%d", irq_clear and 1 or 0),
        string.format("isr_removed=%d", removed and 1 or 0),
        string.format("ctrl_reset_ok=%d", ok and 1 or 0),
    })
    lifecycle = nil
end

local function progress_lifecycle()
    if not lifecycle then return end
    lifecycle.frames = lifecycle.frames + 1
    if lifecycle.kind == "foreign" then
        if mem:read_u8(FOREIGN_CAPTURE + 11) ~= 0 or lifecycle.frames >= 10 then
            finish_foreign_irq(lifecycle)
        end
    elseif lifecycle.kind == "ctrl_reset" then
        if lifecycle.frames == 4 then
            lifecycle.reset:set_value(0)
            lifecycle.control:set_value(0)
        elseif lifecycle.frames >= 90 then
            finish_ctrl_reset(lifecycle)
        end
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
    elseif action == "foreign_irq" then
        begin_foreign_irq()
    elseif action == "ctrl_reset" then
        begin_ctrl_reset()
    else
        snapshot()
    end
end

_serial_irq_diag_notifier = emu.add_machine_frame_notifier(function()
    pcall(snapshot_if_requested)
    pcall(progress_lifecycle)
end)
