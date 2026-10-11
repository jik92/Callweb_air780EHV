-- One explicitly requested outgoing call, with one TTS playback to the peer.
local M = {}
local exaudio = require("exaudio")
local ready, audio_ready = false, false
local active, last_id, last_result
local emit
local up = {zbuff.create(48000,0), zbuff.create(48000,0)}
local down = {zbuff.create(48000,0), zbuff.create(48000,0)}
local function capture(buff)
    if active and active.connected and active.recording and buff:used() > 0 then
        local quality = cc.quality()
        if quality ~= 0 then active.sample_rate = quality == 2 and 16000 or 8000 end
        local raw = buff:query()
        if active.recording:used() + #raw <= 2048000 then
            active.recording:write(raw)
        else
            active.recording_overflow = true
        end
    end
    buff:del()
end

local function event(state, extra)
    if not active then return end
    local data = extra or {}
    data.type = "call_event"
    data.request_id = active.request_id
    data.to = active.to
    data.state = state
    emit(data)
end
local function finish(reason)
    if not active then return end
    capture(down[1]); capture(down[2])
    local ended = active
    last_result = {type="call_result", request_id=ended.request_id, to=ended.to,
        connected=ended.connected == true, played=ended.played == true, reason=reason,
        recording_overflow=ended.recording_overflow == true}
    local result = last_result
    active = nil
    exaudio.pm(exaudio.SHUTDOWN)
    sys.taskInit(function()
        if ended.recording then
            local offset, sequence = 0, 0
            while offset < ended.recording:used() do
                local raw = ended.recording:query(offset, math.min(4096, ended.recording:used()-offset))
                local hex = raw:gsub(".", function(c) return string.format("%02x", string.byte(c)) end)
                sequence = sequence + 1
                emit({type="call_audio", request_id=ended.request_id, sequence=sequence,
                    sample_rate=ended.sample_rate or 16000, hex=hex})
                offset = offset + #raw
                sys.wait(20)
            end
        end
        emit(result)
    end)
end

local function play()
    if not active or active.started or not active.connected or not active.audio then return end
    active.started = true
    local id = active.request_id
    sys.timerStart(function()
        if not active or active.request_id ~= id then return end
        cc.extern_source(nil)
        local ok, result = pcall(cc.extern_source, active.content)
        if ok and result then
            event("play_started", {quality=cc.quality()})
        else
            event("play_failed", {error=tostring(result)})
            active.reason = "play_failed"
            cc.hangUp(0)
        end
    end, 1200)
end
function M.init(reply)
    emit = reply
    sys.subscribe("CC_IND", function(state)
        log.info("call_player", state)
        if state == "READY" then ready = true; sys.publish("CALL_READY") end
        if not active then return end
        event(state)
        if state == "CONNECTED" or state == "SPEECH_START" then
            active.connected = true
            play()
        elseif state == "AUDIO_START" then
            active.audio = true
            play()
        elseif state == "EXT_SRC_DONE" and active.started then
            active.played = true
            active.reason = "play_completed"
            local id = active.request_id
            sys.timerStart(function()
                if active and active.request_id == id then cc.hangUp(0) end
            end, active.listen_seconds and active.listen_seconds * 1000 or 2000)
        elseif state == "DISCONNECTED" or state == "HANGUP_CALL_DONE" or state == "MAKE_CALL_FAILED" then
            finish(active.reason or state)
        end
    end)
    sys.taskInit(function()
        audio_ready = exaudio.setup({model="es8311", i2c_id=0,
            pa_ctrl=gpio.AUDIOPA_EN, dac_ctrl=20, dac_delay=6,
            pa_delay=100, dac_time_delay=100, bits_per_sample=16,
            pa_on_level=1, audio_mode="new"}) == true
        if not ready then sys.waitUntil("CALL_READY", 60000) end
        if audio_ready then
            audio_ready = cc.init(0) == true
            cc.record(true, up[1], up[2], down[1], down[2])
            cc.on("record", function(is_dl, point)
                if is_dl then capture(down[point+1]) else up[point+1]:del() end
            end)
        end
        log.info("call_player", "initialized", ready, audio_ready)
    end)
end
function M.status() return {ready=ready, audio_ready=audio_ready, busy=active ~= nil} end
function M.start(command)
    if type(command.request_id) ~= "string" or command.request_id == "" then return end
    if command.request_id == last_id then
        if last_result then emit(last_result) end
        return
    end
    if active or not ready or not audio_ready then
        emit({type="call_result",request_id=command.request_id,connected=false,played=false,reason="busy_or_not_ready"})
        return
    end
    if type(command.to) ~= "string" or not command.to:match("^%+?%d+$")
        or type(command.content) ~= "string" or #command.content == 0 or #command.content > 512 then
        emit({type="call_result",request_id=command.request_id,reason="invalid_input"})
        return
    end
    if command.listen_seconds ~= nil then
        if type(command.listen_seconds) ~= "number" or command.listen_seconds < 1
            or command.listen_seconds > 60 then
            emit({type="call_result",request_id=command.request_id,reason="invalid_listen_seconds"})
            return
        end
    end
    for i=1,2 do up[i]:del(); down[i]:del() end
    active = command
    if command.listen_seconds then active.recording = zbuff.create(2048000,0,zbuff.HEAP_AUTO) end
    last_id = command.request_id
    last_result = {type="call_accepted",request_id=command.request_id,to=command.to}
    emit(last_result)
    local id = command.request_id
    sys.timerStart(function()
        if active and active.request_id == id and not active.connected then
            active.reason = "answer_timeout"; cc.hangUp(0)
        end
    end, 45000)
    sys.timerStart(function()
        if active and active.request_id == id then
            active.reason = "call_timeout"; cc.extern_source(nil); cc.hangUp(0)
        end
    end, 90000)
    local ok, result = pcall(cc.dial, 0, command.to)
    if not ok or result == false then finish("dial_failed") end
end
return M
