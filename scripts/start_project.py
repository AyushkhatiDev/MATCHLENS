"""Launch the local MatchLens API and frontend; own and clean up both process groups."""
import argparse
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def port(value):
    number = int(value)
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError('Port must be between 1 and 65535.')
    return number


def stop_children(children):
    # Signal whole owned groups, including npm's Next.js descendants.
    for sig, seconds in ((signal.SIGINT, 5), (signal.SIGTERM, 3), (signal.SIGKILL, 1)):
        for child in children:
            try:
                os.killpg(child.pid, sig)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + seconds
        for child in children:
            try:
                child.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
        # A group may retain descendants even after its original leader exits.
        alive = []
        for child in children:
            try:
                os.killpg(child.pid, 0)
                alive.append(child)
            except ProcessLookupError:
                pass
        children = alive
        if not children:
            break


def main():
    parser = argparse.ArgumentParser(description='Start both MatchLens servers in one terminal. Ctrl+C stops both.')
    parser.add_argument('--mock', action='store_true', help='Use offline mock agents instead of the live Foundry hero demo.')
    parser.add_argument('--frontend-port', type=port, default=3002)
    parser.add_argument('--backend-port', type=port, default=8002)
    args = parser.parse_args()
    if args.frontend_port == args.backend_port:
        parser.error('Frontend and backend need different ports.')
    npm = shutil.which('npm')
    if not npm or not (ROOT / 'frontend/node_modules/next/package.json').is_file():
        print('Frontend dependencies missing. Run npm ci in frontend, then retry.', file=sys.stderr)
        return 1
    for number in (args.backend_port, args.frontend_port):
        try:
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(('127.0.0.1', number))
        except OSError:
            print(f'Port {number} is unavailable. Stop the existing server or use --frontend-port / --backend-port.', file=sys.stderr)
            return 1

    # Read only the checked-in example; never source shell code or create a secret file.
    profile = ROOT / 'deploy' / ('demo.env.example' if args.mock else 'foundry-demo.env.example')
    defaults = {}
    for line in profile.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            defaults[key] = value
    env = {**defaults, **os.environ}
    frontend_url = f'http://127.0.0.1:{args.frontend_port}'
    backend_url = f'http://127.0.0.1:{args.backend_port}'
    env.update(APP_ENV='local', APP_PUBLIC_DEMO='true', EVENT_SOURCE='local', APP_DEMO_CONTROLS='true', APP_AUTOSTART='false',
               AGENTIC_MODE='mock' if args.mock else 'foundry',
               APP_FOUNDRY_HERO_ONLY='false' if args.mock else 'true',
               APP_FRONTEND_ORIGINS=frontend_url, NEXT_PUBLIC_API_BASE_URL=backend_url,NEXT_PUBLIC_DEMO_SESSIONS='true',
               PYTHONUNBUFFERED='1')
    children = []
    stopping = False

    def request_stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    print(f"Starting MatchLens ({'MOCK' if args.mock else 'Foundry hero demo; current Azure identity'})", flush=True)
    print(f'Frontend: {frontend_url}/demo\nBackend:  {backend_url}\nCtrl+C stops both servers.', flush=True)
    try:
        backend = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'simulator.production.public_api:app',
                                    '--host', '127.0.0.1', '--port', str(args.backend_port)],
                                   cwd=ROOT, env=env, start_new_session=True)
        children.append(backend)
        # Let the API become available before the frontend's first snapshot request.
        deadline = time.monotonic() + 45
        while not stopping:
            if backend.poll() is not None:
                print('Backend exited during startup.', file=sys.stderr)
                return backend.returncode or 1
            try:
                with urlopen(backend_url + '/health', timeout=1) as response:
                    if response.status == 200:
                        break
            except (URLError, TimeoutError, OSError):
                pass
            if time.monotonic() >= deadline:
                print('Backend did not start within 45 seconds.', file=sys.stderr)
                return 1
            time.sleep(.1)
        if stopping:
            return 0
        children.append(subprocess.Popen([npm, 'run', 'dev', '--', '--port', str(args.frontend_port)],
                                         cwd=ROOT / 'frontend', env=env, start_new_session=True))
        while not stopping:
            for child in children:
                code = child.poll()
                if code is not None:
                    print('A server exited; stopping both servers.', file=sys.stderr)
                    return code or 1
            time.sleep(.2)
        return 0
    except OSError:
        print('Could not launch the servers. Check local dependencies and permissions.', file=sys.stderr)
        return 1
    finally:
        print('\nStopping MatchLens servers...', flush=True)
        stop_children(children)


if __name__ == '__main__':
    raise SystemExit(main())
