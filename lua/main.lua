PROJECT = "air780ehv_sim_query"
VERSION = "001.999.011"

sys = require("sys")
local call_player = require("call_player")
local network_test = require("network_test")
local uartid = uart.VUART_0
local buffer = ""
local sms_ready = false
local sms_busy = false
local last_request_id
local last_result
local inbox = {}
local inbox_path = "/sms_inbox.json"
do
    local file = io.open(inbox_path, "r")
    if file then
        local raw = file:read("*a")
        file:close()
        local ok, saved = pcall(json.decode, raw)
        if ok and type(saved) == "table" then inbox = saved end
    end
end

sys.subscribe("SMS_READY", function() sms_ready = true end)
-- Some EC7xx firmware versions signal SMS readiness through CC_IND.
sys.subscribe("CC_IND", function(state)
    if state == "READY" then sms_ready = true end
end)

-- Match the reference project's USB framing, using official LuatOS APIs.
mobile.setAuto(0)
uart.setup(uartid, 115200, 8, 1)

local function status()
    local ready = mobile.simPin() == true
    local number = ready and mobile.number(0) or nil
    return {
        type = "status_response",
        project = PROJECT,
        version = VERSION,
        network_test_supported = true,
        call = call_player.status(),
        mobile = {
            sim_ready = ready,
            sms_ready = sms_ready,
            number = number or "",
            number_state = not ready and "sim_not_ready"
                or (number and number ~= "" and "available" or "unavailable"),
            iccid = mobile.iccid() or "",
            imsi = mobile.imsi() or "",
            net_status = mobile.status(),
            sim_slot = mobile.simid(),
            csq = mobile.csq(),
            rssi = mobile.rssi(),
            rsrp = mobile.rsrp(),
            rsrq = mobile.rsrq(),
            snr = mobile.snr(),
            serving_cell = mobile.scell()
        }
    }
end

local function reply(data)
    uart.write(uartid, "SMS_START:" .. json.encode(data) .. ":SMS_END\r\n")
end

local function audio_reply(id, sequence, rate, pcm)
    uart.write(uartid, "AUD_START:" .. id .. string.pack("<I2I2I4", #pcm, rate, sequence)
        .. pcm .. ":AUD_END")
end
call_player.init(reply, audio_reply)

local function send_status()
    reply(status())
end

sms.setNewSmsCb(function(phone, content, metas)
    local message = {type = "incoming_sms", from = phone, content = content,
        received_at = os.time(), metas = metas}
    table.insert(inbox, message)
    while #inbox > 20 do table.remove(inbox, 1) end
    local ok, saved = pcall(function()
        local file = io.open(inbox_path, "w")
        if not file then return false end
        local result = file:write(json.encode(inbox))
        file:close()
        return result ~= nil
    end)
    message.saved = ok and saved == true
    log.info("sms_rx", phone, content)
    reply(message)
end)

local function send_sms(command)
    if type(command.request_id) ~= "string" or command.request_id == "" then return end
    if command.request_id == last_request_id then
        if last_result then reply(last_result) end
        return
    end
    if sms_busy or not sms_ready or not mobile.simPin() or not sms then
        reply({type = "sms_send_result", request_id = command.request_id,
            success = false, error = sms_busy and "busy" or "sms_not_ready"})
        return
    end
    if type(command.to) ~= "string" or not command.to:match("^%+?%d+$")
        or type(command.content) ~= "string" or #command.content == 0
        or #command.content > 512 then
        reply({type = "sms_send_result", request_id = command.request_id,
            success = false, error = "invalid_recipient_or_content"})
        return
    end
    sms_busy = true
    last_request_id = command.request_id
    last_result = {type = "sms_send_accepted", request_id = command.request_id, to = command.to}
    reply(last_result)
    sys.taskInit(function()
        local ok, result = pcall(function()
            return sms.sendLong(command.to, command.content).wait()
        end)
        last_result = {type = "sms_send_result", request_id = command.request_id,
            to = command.to, success = ok and result == true,
            error = not ok and tostring(result) or nil}
        sms_busy = false
        reply(last_result)
    end)
end

uart.on(uartid, "receive", function(id, len)
    buffer = buffer .. uart.read(id, len)
    while true do
        local command_start = buffer:find("CMD_START:", 1, true)
        local pcm_start = buffer:find("PCM_START:", 1, true)
        if not command_start and not pcm_start then buffer = buffer:sub(-9); break end
        if pcm_start and (not command_start or pcm_start < command_start) then
            buffer = buffer:sub(pcm_start)
            if #buffer < 52 then break end
            local length, sequence = string.unpack("<I2I4", buffer, 47)
            if length > 1280 or length % 2 ~= 0 then
                buffer = buffer:sub(11)
            elseif #buffer < 60 + length then break
            elseif buffer:sub(53 + length, 60 + length) ~= ":PCM_END" then
                buffer = buffer:sub(11)
            else
                call_player.input(buffer:sub(11,46), sequence, buffer:sub(53,52+length))
                buffer = buffer:sub(61+length)
            end
        else
        local first = command_start
        local last = buffer:find(":CMD_END", first + 10, true)
        if not last then buffer = buffer:sub(first); break end
        local payload = buffer:sub(first + 10, last - 1)
        buffer = buffer:sub(last + 8)
        local ok, command = pcall(json.decode, payload)
        if ok and type(command) == "table" then
            if command.action == "get_status" then send_status()
            elseif command.action == "network_test" then
                network_test.start(command, reply, call_player.status())
            elseif command.action == "send_sms" then send_sms(command)
            elseif command.action == "call_play" then call_player.start(command)
            elseif command.action == "call_live" then call_player.live(command)
            elseif command.action == "call_answer" then call_player.answer(command.request_id)
            elseif command.action == "call_hangup" then call_player.hangup(command.request_id) end
            if command.action == "restore_inbox" and type(command.messages) == "table"
                and #command.messages <= 20 and #inbox == 0 then
                local valid = true
                for _, message in ipairs(command.messages) do
                    if type(message) ~= "table" or type(message.from) ~= "string"
                        or type(message.content) ~= "string" then valid = false end
                end
                if valid then
                    local file = io.open(inbox_path, "w")
                    if file then
                        local saved = file:write(json.encode(command.messages))
                        file:close()
                        if saved then inbox = command.messages end
                    end
                end
                reply({type="inbox_restored", count=#inbox})
            end
            if command.action == "get_inbox" then
                reply({type = "inbox_response", messages = inbox, version = VERSION})
            end
        end
    end
        end
    if #buffer > 16384 then buffer = "" end
end)

sys.taskInit(function()
    for _ = 1, 30 do
        if mobile.simPin() then break end
        sys.wait(1000)
    end
    send_status()
    log.info("sim_query", "ready; request get_status via USB VUART")
end)

sys.run()
