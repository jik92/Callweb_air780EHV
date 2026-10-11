#!/usr/bin/env python3
"""Read the device inbox and listen for incoming SMS; never sends SMS."""
import argparse
import datetime
import json
from pathlib import Path
import re
import time

import serial

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--port', default='/dev/cu.usbmodem0000000000017')
parser.add_argument('--listen', type=float, default=0, help='Seconds to listen after querying inbox')
args = parser.parse_args()
log = Path(__file__).resolve().parent.parent / 'logs' / 'sms-inbox.jsonl'
log.parent.mkdir(exist_ok=True)
with serial.Serial(args.port, 115200, timeout=0.2, write_timeout=3) as conn:
    conn.write(b'CMD_START:{"action":"get_inbox"}:CMD_END\r\n')
    buffer = bytearray()
    deadline = time.monotonic() + max(5, args.listen)
    found = False
    while time.monotonic() < deadline:
        buffer.extend(conn.read(4096))
        while True:
            match = re.search(rb'SMS_START:(.*?):SMS_END', buffer, re.S)
            if not match:
                break
            message = json.loads(match[1])
            del buffer[:match.end()]
            if message.get('type') not in ('inbox_response', 'incoming_sms'):
                continue
            found = True
            record = {'captured_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), **message}
            with log.open('a') as file:
                file.write(json.dumps(record, ensure_ascii=False) + '\n')
            print(json.dumps(record, ensure_ascii=False, indent=2), flush=True)
            if args.listen <= 0 and message.get('type') == 'inbox_response':
                raise SystemExit(0)
        if len(buffer) > 1048576:
            raise RuntimeError('Serial response exceeded 1 MB')
    if not found:
        raise SystemExit('No SMS/inbox response from the device')
