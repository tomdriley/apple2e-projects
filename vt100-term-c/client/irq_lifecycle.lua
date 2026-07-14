-- ROM-backed IRQ lifecycle probe. A slot-1 Mockingboard VIA timer supplies a
-- non-SSC bus IRQ, then Control+Reset exercises the IIe SOFTEV cleanup path.
local source = debug.getinfo(1, "S").source
local script_path = source:sub(1, 1) == "@" and source:sub(2) or source
local script_dir = script_path:match("^(.*)[/\\]") or "."
dofile(script_dir .. "/ssc_irq.lua")

local output_path = os.getenv("IRQ_LIFECYCLE_OUT")
local labels_path = os.getenv("IRQ_LIFECYCLE_LABELS")
if not output_path or not labels_path then
    error("IRQ_LIFECYCLE_OUT and IRQ_LIFECYCLE_LABELS are required")
end

local symbols = {}
for line in io.lines(labels_path) do
    local address, name = line:match("^al%s+([0-9A-Fa-f]+)%s+%.(_[%w_]+)$")
    if address and name then
        symbols[name] = tonumber(address, 16)
    end
end

local required = {
    "_serial_irq_active",
    "_serial_irq_chained",
    "_serial_irq_handler",
    "_serial_irq_reset_handler",
    "_serial_irq_saved_irqloc",
    "_serial_irq_saved_softev",
    "_serial_irq_saved_pwredup",
    "_serial_irq_saved_command",
    "_serial_irq_saved_control",
}
for _, name in ipairs(required) do
    if not symbols[name] then
        error("missing linker symbol " .. name)
    end
end

local memory = manager.machine.devices[":maincpu"].spaces["program"]
local special = manager.machine.ioport.ports[":keyb_special"]
local control = special and special.fields["Control"]
local reset = special and special.fields["RESET"]
if not control or not reset then
    error("Apple IIe Control/RESET input fields are unavailable")
end

local function read_word(address)
    return memory:read_u8(address) + memory:read_u8(address + 1) * 256
end

local function write_result(result)
    local file = assert(io.open(output_path, "w"))
    file:write(result, "\n")
    file:close()
end

local function fail(message)
    write_result("FAIL " .. message)
    manager.machine:exit()
end

local phase = "install"
local frames = 0
local held_frames = 0
local expected = {}

_irq_lifecycle_sub = emu.add_machine_frame_notifier(function()
    frames = frames + 1
    if frames > 2400 then
        fail("timeout in phase " .. phase)
        return
    end

    if phase == "install" then
        if memory:read_u8(symbols["_serial_irq_active"]) ~= 1 then
            return
        end
        if read_word(0x03FE) ~= symbols["_serial_irq_handler"] then
            return
        end
        if read_word(0x03F2) ~= symbols["_serial_irq_reset_handler"] then
            return
        end

        expected.irqloc = read_word(symbols["_serial_irq_saved_irqloc"])
        expected.softev = read_word(symbols["_serial_irq_saved_softev"])
        expected.pwredup = memory:read_u8(symbols["_serial_irq_saved_pwredup"])
        expected.command = memory:read_u8(symbols["_serial_irq_saved_command"])
        expected.control = memory:read_u8(symbols["_serial_irq_saved_control"])

        if memory:read_u8(0x03F4) ~= ((math.floor(symbols["_serial_irq_reset_handler"] / 256) ~ 0xA5) & 0xFF) then
            fail("PWREDUP does not validate the cleanup hook")
            return
        end

        memory:write_u8(symbols["_serial_irq_chained"], 0)
        memory:write_u8(0xC10E, 0x40) -- slot-1 VIA: disable Timer 1 IRQ
        memory:write_u8(0xC10D, 0x7F) -- clear pending VIA interrupt flags
        memory:write_u8(0xC10E, 0xC0) -- enable Timer 1 IRQ
        memory:write_u8(0xC104, 0x20)
        memory:write_u8(0xC105, 0x00) -- start one-shot Timer 1
        phase = "foreign_irq"
        return
    end

    if phase == "foreign_irq" then
        if memory:read_u8(symbols["_serial_irq_chained"]) == 0 then
            return
        end
        memory:write_u8(0xC10E, 0x40) -- disable Timer 1 IRQ
        memory:write_u8(0xC10D, 0x40) -- acknowledge Timer 1
        control:set_value(1)
        reset:set_value(1)
        phase = "reset_held"
        return
    end

    if phase == "reset_held" then
        held_frames = held_frames + 1
        if held_frames >= 2 then
            reset:set_value(0)
            control:set_value(0)
            phase = "restored"
        end
        return
    end

    if phase == "restored" then
        if memory:read_u8(symbols["_serial_irq_active"]) ~= 0 then
            return
        end
        if read_word(0x03FE) ~= expected.irqloc then
            fail("IRQLOC was not restored")
            return
        end
        if read_word(0x03F2) ~= expected.softev then
            fail("SOFTEV was not restored")
            return
        end
        if memory:read_u8(0x03F4) ~= expected.pwredup then
            fail("PWREDUP was not restored")
            return
        end
        if memory:read_u8(0xC0AA) ~= expected.command then
            fail("6551 command register was not restored")
            return
        end
        if memory:read_u8(0xC0AB) ~= expected.control then
            fail("6551 control register was not restored")
            return
        end

        write_result("PASS non-SSC IRQ chained; Ctrl-Reset restored vectors and ACIA")
        manager.machine:exit()
    end
end)
