-- Explicit USB tethering; never enable automatically at boot.
local M = {}
local enabled, busy, mode = false, false, "ecm"
mobile.config(mobile.CONF_USB_ETHERNET, 0)
function M.status()
    return {enabled=enabled, switching=busy, mode=mode}
end
function M.start(command, reply, call_status)
    if type(command.request_id) ~= "string" or type(command.enabled) ~= "boolean" then return end
    local target_mode = command.mode or "ecm"
    if target_mode ~= "ecm" and target_mode ~= "rndis" then return end
    local function emit(success, error)
        reply({type="usb_network_result", request_id=command.request_id,
            success=success, enabled=enabled, mode=mode, error=error})
    end
    if busy or call_status.busy then emit(false, "请结束通话或等待网卡切换完成"); return end
    if enabled == command.enabled and (not enabled or mode == target_mode) then emit(true); return end
    if enabled and command.enabled and mode ~= target_mode then
        emit(false, "请先关闭网卡，再切换模式")
        return
    end
    busy = true
    sys.taskInit(function()
        local ok, result = pcall(function()
            mobile.flymode(0, true)
            sys.wait(500)
            local value = command.enabled and (target_mode == "ecm" and 7 or 3) or 0
            return mobile.config(mobile.CONF_USB_ETHERNET, value)
        end)
        -- Always leave flight mode, including configuration failures.
        mobile.flymode(0, false)
        if not ok or result ~= true then
            busy = false
            emit(false, "设备 USB 网卡配置失败")
            return
        end
        enabled, mode = command.enabled, target_mode
        emit(true)
        -- Flush the result before disconnecting/re-enumerating USB.
        sys.wait(500)
        pm.power(pm.USB, false)
        pm.power(pm.USB, true)
        busy = false
    end)
end
return M
