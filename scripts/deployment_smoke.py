"""Smoke-test a deployed public demo through the browser-facing origin; prints no cookies or secrets.

Usage: python scripts/deployment_smoke.py https://YOUR-FRONTEND [--api https://YOUR-API] [--allow-foundry]

Starting a session against a Foundry-mode backend makes real model requests, so the script refuses
unless /health reports MOCK or --allow-foundry is given.
"""
import argparse
import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit


class Client:
    def __init__(self, origin):
        self.origin = origin
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, path, method='GET', body=None, cookies=True):
        headers = {'Origin': self.origin, 'Accept': 'application/json'}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(self.origin + path, data=data, method=method, headers=headers)
        opener = self.opener if cookies else urllib.request.build_opener()
        try:
            with opener.open(request, timeout=30) as response:
                return response.status, json.loads(response.read() or b'null')
        except urllib.error.HTTPError as error:
            return error.code, None

    def stream(self, path, seconds):
        request = urllib.request.Request(self.origin + path, headers={'Origin': self.origin, 'Accept': 'text/event-stream'})
        ids, types, arrivals = [], {}, []
        started = time.monotonic()
        with self.opener.open(request, timeout=30) as response:
            assert response.headers.get_content_type() == 'text/event-stream', 'stream is not text/event-stream'
            while time.monotonic() - started < seconds:
                line = response.readline().decode()
                if not line:
                    break
                if line.startswith('id: '):
                    ids.append(int(line[4:]))
                    arrivals.append(time.monotonic() - started)
                elif line.startswith('data: '):
                    kind = json.loads(line[6:]).get('type')
                    types[kind] = types.get(kind, 0) + 1
        return ids, types, arrivals


def check(condition, message):
    print(('PASS ' if condition else 'FAIL ') + message)
    if not condition:
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('frontend', help='Origin that serves the API to browsers (the frontend origin in proxy mode).')
    parser.add_argument('--api', help='Direct API origin for /health; defaults to the frontend origin.')
    parser.add_argument('--allow-foundry', action='store_true')
    parser.add_argument('--stream-seconds', type=float, default=20)
    args = parser.parse_args()
    origin = args.frontend.rstrip('/')
    check(urlsplit(origin).path in ('', '/'), 'frontend argument is a bare origin')
    client = Client(origin)

    with urllib.request.urlopen((args.api or origin).rstrip('/') + '/health', timeout=30) as response:
        health = json.loads(response.read())
    foundry = health.get('foundry', {}).get('status')
    print(f'INFO /health foundry={foundry} verified={health.get("foundry", {}).get("verified")} '
          f'sessions={health.get("active_sessions")}/{health.get("session_capacity")}')
    if foundry != 'MOCK' and not args.allow_foundry:
        sys.exit('Backend is not in mock mode; rerun with --allow-foundry to accept real model requests.')

    status, session = client.call('/api/sessions', 'POST')
    check(status == 201 and session and len(session['session_id']) == 32, f'session created (HTTP {status})')
    check(session['runtime'] == ('mock' if foundry == 'MOCK' else 'foundry'), f'session runtime is {session["runtime"]}')
    base = session['api_path']
    status, _ = client.call(base + '/api/matches', cookies=False)
    check(status == 403, f'session refuses requests without the ownership cookie (HTTP {status})')
    status, matches = client.call(base + '/api/matches')
    check(status == 200 and matches, 'session cookie accepted through this origin')
    mid = matches[0]['match_id']
    check(matches[0]['runtime_mode'] == session['runtime'], f'match runtime_mode is {matches[0]["runtime_mode"]}')

    status, state = client.call(f'{base}/api/matches/{mid}/demo', 'POST', {'action': 'start', 'speed': 100})
    check(status == 200 and state['status'] == 'running', f'replay started (HTTP {status})')
    ids, types, arrivals = client.stream(f'{base}/api/matches/{mid}/stream?cursor=0', args.stream_seconds)
    check(len(ids) > 10 and ids == sorted(set(ids)), f'{len(ids)} stream messages, strictly increasing ids')
    check(arrivals and arrivals[-1] - arrivals[0] > 1, 'messages arrive incrementally (no proxy buffering)')
    print('INFO message types ' + json.dumps(types, sort_keys=True))

    status, state = client.call(f'{base}/api/matches/{mid}/demo', 'POST', {'action': 'pause', 'speed': 100})
    check(status == 200 and state['status'] == 'paused', 'replay paused for a stable checkpoint')
    status, moments = client.call(f'{base}/api/matches/{mid}/moments')
    check(status == 200 and moments, f'{len(moments or [])} moments available')
    with_evidence = next((m for m in moments if m['evidence']), None)
    check(with_evidence is not None, 'a moment carries evidence')
    evidence_id = with_evidence['evidence'][0]['evidence_id']
    status, _ = client.call(f'{base}/api/evidence/{quote(evidence_id)}?insight_id={quote(with_evidence["insight_id"])}')
    check(status == 200, 'evidence lookup')
    labels = sorted({(m.get('execution') or {}).get('status', 'NO EXECUTION RECORD') for m in moments})
    print('INFO execution labels ' + ', '.join(labels))
    if foundry == 'MOCK':
        check(not any('FOUNDRY' in label for label in labels), 'mock backend never labels a moment as Foundry')

    status, before = client.call(f'{base}/api/matches/{mid}')
    clock = before['state']['simulation_time']
    status, sequences = client.call(f'{base}/api/matches/{mid}/sequences?timestamp={clock}')
    check(status == 200 and sequences['sequences'], 'Story Stress Test sequences available')
    sequence_id = sequences['sequences'][0]['sequence_id']
    status, _ = client.call(f'{base}/api/matches/{mid}/sensitivity?sequence_id={quote(sequence_id)}&timestamp={clock}')
    check(status == 200, 'Story Stress Test sensitivity computed')
    status, after = client.call(f'{base}/api/matches/{mid}')
    keys = ('home_score', 'away_score', 'total_events', 'simulation_time', 'status')
    check(all(before['state'][k] == after['state'][k] for k in keys), 'stress test left canonical score and state unchanged')

    status, _ = client.call('/api/sessions/' + session['session_id'], 'DELETE')
    check(status == 200, 'session closed')
    print('Smoke test passed.')


if __name__ == '__main__':
    main()
