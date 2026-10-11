-- On-demand network diagnostics. Never run a request during boot or a call.
local M = {}
local busy = false
function M.start(command, reply, call_status)
    local id, url = command.request_id, command.url
    if type(id) ~= "string" or type(url) ~= "string" then return end
    local function fail(reason)
        reply({type="network_test_result", request_id=id, reachable=false,
            elapsed_ms=0, error=reason})
    end
    if busy then fail("已有网络测试进行中"); return end
    if call_status.busy then fail("请结束通话后再测试网络"); return end
    if #url > 1024 or not url:match("^https?://[^/]+")
        or url:find("%s") or url:find(":CMD_END",1,true) then
        fail("测试地址无效"); return
    end
    busy = true
    sys.taskInit(function()
        local started = mcu.ticks()
        local ok, result = pcall(function()
            if not socket.isReady(socket.LWIP_GP) then
                sys.waitUntil("IP_READY", 5000)
            end
            if not socket.isReady(socket.LWIP_GP) then
                return {reachable=false, error="4G 数据网络未就绪"}
            end
            local code, headers = http.request("HEAD", url, nil, nil,
                {timeout=15000, adapter=socket.LWIP_GP}).wait()
            local selected = {}
            for key, value in pairs(headers or {}) do
                local lower = tostring(key):lower()
                if lower == "server" or lower == "content-type" or lower == "location" then
                    selected[lower] = tostring(value):sub(1,256)
                end
            end
            return {reachable=type(code)=="number" and code>=100,
                status_code=code, headers=selected,
                error=(not code or code<100) and "设备 HTTP 请求失败，错误码："..tostring(code) or nil}
        end)
        busy = false
        if not ok then result = {reachable=false, error="设备网络测试异常"} end
        result.type = "network_test_result"
        result.request_id = id
        result.elapsed_ms = (mcu.ticks() - started) % 4294967296
        reply(result)
    end)
end
return M
