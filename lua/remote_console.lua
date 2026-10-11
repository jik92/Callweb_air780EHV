-- Optional authenticated WS transport; no buffered commands or audio survive reconnect.
local M = {}
local loaded, config = pcall(require, "remote_config")
local configured = loaded and type(config) == "table" and type(config.url) == "string"
    and config.url ~= "" and type(config.device_id) == "string" and config.device_id ~= ""
    and type(config.token) == "string" and config.token ~= ""
local client, authenticated, pending, pending_at
local queue, audio_queue = {}, {}
local receive_buffer = ""
local input, lost

function M.enabled() return configured end
function M.send(data, audio)
    if not authenticated then return false end
    local target, limit = audio and audio_queue or queue, audio and 6 or 16
    if #target >= limit then
        if audio then table.remove(target, 1) else return false end
    end
    target[#target+1] = data
    return true
end
local function reset()
    local was_online = authenticated
    authenticated, pending = false, false
    queue, audio_queue, receive_buffer = {}, {}, ""
    if was_online and lost then lost() end
end

function M.init(on_input, on_lost)
    input, lost = on_input, on_lost
    if not configured then return end
    if not websocket or type(websocket.create) ~= "function" then
        log.error("remote_console", "websocket library unavailable")
        return
    end
    sys.taskInit(function()
        while true do
            if not socket.adapter(socket.dft()) then sys.waitUntil("IP_READY", 1000) end
            if socket.adapter(socket.dft()) then
                client = websocket.create(nil, config.url)
                if client then
                    reset()
                    client:on(function(ws, event, data, fin, opcode)
                        if ws ~= client then return end
                        if event == "conack" then
                            ws:send(json.encode({type="device_login", device_id=config.device_id, token=config.token}))
                            pending, pending_at = true, os.time()
                        elseif event == "sent" then
                            pending = false
                        elseif event == "recv" then
                            receive_buffer = receive_buffer .. (data or "")
                            if #receive_buffer > 65536 then reset(); ws:close(); client=nil; return end
                            if fin == 1 then
                                local packet = receive_buffer
                                receive_buffer = ""
                                if not authenticated then
                                    local ok, response = pcall(json.decode, packet)
                                    if ok and type(response) == "table" and response.type == "device_login_ok"
                                        and response.device_id == config.device_id then
                                        authenticated = true
                                    else reset(); ws:close(); client=nil end
                                elseif input then input(packet) end
                            end
                        elseif event == "disconnect" or event == "error" then
                            reset()
                            sys.publish("REMOTE_DISCONNECTED")
                        end
                    end)
                    client:connect()
                    local started = os.time()
                    while client do
                        if (not authenticated and os.time() - started > 15)
                            or (pending and os.time() - pending_at > 5) then break end
                        if authenticated and not pending then
                            local packet
                            if #queue > 0 then packet = table.remove(queue, 1)
                            elseif #audio_queue > 0 then packet = table.remove(audio_queue, 1) end
                            if packet then
                                -- Air780EHV/V2052 websocket API uses fin=1, binary opt=1.
                                pending, pending_at = true, os.time()
                                if client:send(packet, 1, 1) == false then break end
                            end
                        end
                        if sys.waitUntil("REMOTE_DISCONNECTED", 10) then break end
                    end
                    reset()
                    if client then client:close(); client = nil end
                end
            end
            sys.wait(5000)
        end
    end)
end
return M
