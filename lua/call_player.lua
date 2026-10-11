-- One explicitly requested outgoing call, with one TTS playback to the peer.
local M = {}
local exaudio = require("exaudio")
local ready, audio_ready = false, false
local active, last_id, last_result
local emit, emit_pcm
local rx_queue, tx_queue = {}, {}
local rx_sequence = 0
local up = {zbuff.create(1280,0), zbuff.create(1280,0)}
local down = {zbuff.create(1280,0), zbuff.create(1280,0)}
local function capture(buff)
    if active and active.live and active.connected and buff:used() > 0 then
        local raw = buff:query()
        buff:del()
        active.down_sequence = (active.down_sequence or 0) + 1
        if #tx_queue >= 6 then table.remove(tx_queue, 1) end
        tx_queue[#tx_queue+1] = {id=active.request_id, raw=raw,
            rate=cc.quality() == 2 and 16000 or 8000, sequence=active.down_sequence}
        return
    end
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
        recording_overflow=ended.recording_overflow == true,
        up_bytes=ended.up_bytes or 0, dropped_bytes=ended.dropped_bytes or 0}
    local result = last_result
    active = nil
    rx_queue, tx_queue = {}, {}
    cc.extern_source(nil)
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
    if active.live then
        local rate = cc.quality() == 2 and 16000 or 8000
        cc.extern_source(nil)
        local ok, result = pcall(cc.extern_source, true, true,
            audio_v2.DATA_CODEC_TYPE_RAW, true, rate, 16, 1, true)
        if ok and result then
            active.last_input = os.time()
            event("stream_started", {sample_rate=rate})
        else
            active.reason = "stream_start_failed"
            event("stream_failed", {error=tostring(result)})
            cc.hangUp(0)
        end
        return
    end
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
function M.init(reply, audio_reply)
    emit, emit_pcm = reply, audio_reply
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
        elseif state == "EXT_SRC_DONE" and active.started and not active.live then
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
    -- Bound both queues; never do JSON/hex conversion in the audio callback.

function M.status()
    return {ready=ready, audio_ready=audio_ready, busy=active ~= nil,
        live_supported=type(cc.input) == "function", request_id=active and active.request_id,
        up_bytes=active and active.up_bytes or 0, dropped_bytes=active and active.dropped_bytes or 0}
end
function M.input(id, sequence, raw)
    if not active or not active.live or not active.started or active.request_id ~= id
        or sequence <= rx_sequence or #raw % 2 ~= 0 or #raw > 1280 then return end
    rx_sequence = sequence
    active.last_input = os.time()
    if #rx_queue >= 6 then
        local dropped = table.remove(rx_queue, 1)
        active.dropped_bytes = (active.dropped_bytes or 0) + #dropped
    end
    rx_queue[#rx_queue+1] = raw
end
function M.hangup(id)
    if active and active.request_id == id then
        active.reason = "user_hangup"
        cc.hangUp(0)
    end
end
sys.taskInit(function()
    while true do
        if active and active.live and active.started then
            if active.last_input and os.time() - active.last_input > 5 then
                active.reason = "audio_timeout"
                cc.hangUp(0)
            end
            local raw = rx_queue[1]
            if raw then
                local ok, written = cc.input(true, raw, false)
                if ok and written and written > 0 then
                    active.up_bytes = (active.up_bytes or 0) + written
                    if written >= #raw then table.remove(rx_queue, 1)
                    else rx_queue[1] = raw:sub(written + 1) end
                end
            end
        end
        local frame = table.remove(tx_queue, 1)
        if frame and active and active.request_id == frame.id and emit_pcm then
            emit_pcm(frame.id, frame.sequence, frame.rate, frame.raw)
        end
        sys.wait(10)
    end
end)
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
        or (not command.live and (type(command.content) ~= "string" or #command.content == 0 or #command.content > 512)) then
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
    rx_queue, tx_queue, rx_sequence = {}, {}, -1
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
function M.live(command)
    command.live = true
    M.start(command)
end
return M
