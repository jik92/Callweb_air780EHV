-- Explicit single-cell location using the official free LuatOS service.
local M = {}
local lbsLoc2 = require("lbsLoc2")
local busy = false
function M.start(command, reply, call_status)
    if type(command.request_id) ~= "string" then return end
    local function emit(result)
        result.type = "location_result"
        result.request_id = command.request_id
        reply(result)
    end
    if busy or call_status.busy then
        emit({success=false, error="请结束通话或等待当前定位完成"})
        return
    end
    busy = true
    sys.taskInit(function()
        local ok, result = pcall(function()
            if not socket.adapter(socket.LWIP_GP) then
                return {success=false, error="4G 数据网络未就绪"}
            end
            mobile.reqCellInfo(15)
            sys.waitUntil("CELL_INFO_UPDATE", 3000)
            local lat, lng = lbsLoc2.request(5000)
            lat, lng = tonumber(lat), tonumber(lng)
            if not lat or not lng or lat < -90 or lat > 90 or lng < -180 or lng > 180 then
                return {success=false, error="基站定位服务未返回有效坐标"}
            end
            return {success=true, latitude=lat, longitude=lng, coordinate_system="WGS84"}
        end)
        busy = false
        emit(ok and result or {success=false, error="设备基站定位执行异常"})
    end)
end
return M
