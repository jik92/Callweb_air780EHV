#!/usr/bin/env python3
"""Read-only AT / reference-project LuatOS SIM status queries."""
import argparse
import json
import re
import sys
import time

import serial
from serial.tools import list_ports


def exchange(port, payload, timeout=3, framed=False):
    with serial.Serial(port, 115200, timeout=0.1, write_timeout=2) as conn:
        conn.reset_input_buffer()
        conn.write(payload)
        result = bytearray()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result.extend(conn.read(4096))
            if framed:
                match = re.search(rb'SMS_START:(.*?):SMS_END', result, re.S)
                if match:
                    message = json.loads(match[1])
                    if message.get('type') == 'status_response':
                        return message
                    del result[:match.end()]
            elif re.search(rb'\r\n(?:OK|ERROR|\+CME ERROR:[^\r\n]*)\r\n', result):
                break
        return bytes(result).decode('utf-8', errors='replace')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['ports', 'at', 'status'])
    parser.add_argument('--port')
    parser.add_argument('--timeout', type=float, default=5)
    args = parser.parse_args()
    ports = [p for p in list_ports.comports() if p.vid == 0x19D1]
    if args.mode == 'ports':
        print(json.dumps([{'port': p.device, 'description': p.description,
                          'hwid': p.hwid} for p in ports], ensure_ascii=False, indent=2))
        return 0
    if args.mode == 'at':
        if not args.port:
            parser.error('AT mode requires --port (avoid the binary diagnostic port)')
        replies = {cmd: exchange(args.port, (cmd + '\r\n').encode())
                   for cmd in ['AT', 'ATI', 'AT+CPIN?', 'AT+CNUM', 'AT+CEREG?', 'AT+CSQ']}
        match = re.search(r'\+CNUM:\s*"[^"\r\n]*",\s*"([^"\r\n]+)"', replies['AT+CNUM'])
        print(json.dumps({'port': args.port, 'number': match[1] if match else None,
                          'replies': replies}, ensure_ascii=False, indent=2))
        return 0 if match else 2
    candidates = [args.port] if args.port else [p.device for p in ports]
    errors = {}
    for port in candidates:
        try:
            result = exchange(port, b'CMD_START:{"action":"get_status"}:CMD_END\r\n',
                              args.timeout, framed=True)
            if isinstance(result, dict):
                print(json.dumps({'port': port, **result}, ensure_ascii=False, indent=2))
                return 0 if result.get('mobile', {}).get('number') else 2
            errors[port] = 'No status_response (wrong port or firmware)'
        except (serial.SerialException, OSError, ValueError) as exc:
            errors[port] = str(exc)
    print(json.dumps({'error': 'No LuatOS status response', 'ports': errors},
                     ensure_ascii=False, indent=2))
    return 1


if __name__ == '__main__':
    sys.exit(main())
