#!/usr/bin/env python3
"""Submit one SMS once; wait for the matching module result, without retries."""
import argparse
import datetime
import json
from pathlib import Path
import re
import time
import uuid

import serial

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--port', required=True)
parser.add_argument('--to', required=True)
parser.add_argument('--content', required=True)
parser.add_argument('--timeout', type=float, default=90)
args = parser.parse_args()
request = {'action': 'send_sms', 'request_id': str(uuid.uuid4()),
           'to': args.to, 'content': args.content}
record = {'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'request': request, 'state': 'prepared', 'events': []}
log = Path(__file__).resolve().parent.parent / 'logs' / ('sms-' + request['request_id'] + '.json')
log.parent.mkdir(exist_ok=True)


def save():
    log.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')


save()
try:
    with serial.Serial(args.port, 115200, timeout=0.2, write_timeout=3) as conn:
        record['state'] = 'submitting'
        save()
        payload = ('CMD_START:' + json.dumps(request, ensure_ascii=False) + ':CMD_END\r\n').encode()
        conn.write(payload)
        conn.flush()
        record['state'] = 'awaiting_result'
        save()
        print('SMS command sent once; request_id=' + request['request_id'], flush=True)
        buffer = bytearray()
        deadline = time.monotonic() + args.timeout
        finished = False
        while time.monotonic() < deadline and not finished:
            buffer.extend(conn.read(4096))
            while True:
                match = re.search(rb'SMS_START:(.*?):SMS_END', buffer, re.S)
                if not match:
                    break
                message = json.loads(match[1])
                del buffer[:match.end()]
                if message.get('request_id') != request['request_id']:
                    continue
                record['events'].append(message)
                print(json.dumps(message, ensure_ascii=False), flush=True)
                if message.get('type') == 'sms_send_result':
                    record['state'] = 'sent_to_smsc' if message.get('success') else 'failed'
                    finished = True
                save()
            if len(buffer) > 65536:
                del buffer[:-4096]
        if not finished:
            record['state'] = 'unknown_timeout'
            save()
except (serial.SerialException, OSError, ValueError) as exc:
    record['state'] = 'unknown_error'
    record['error'] = str(exc)
    save()
print(json.dumps({'state': record['state'], 'log': str(log)}, ensure_ascii=False), flush=True)
raise SystemExit(0 if record['state'] == 'sent_to_smsc' else 1)
