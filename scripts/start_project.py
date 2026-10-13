"""Launch the local MatchLens API and frontend; own and clean up both process groups."""
import argparse
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
# Running this file directly puts scripts/, not the repository root, on the import path.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
FOUNDRY_SETTINGS = ('FOUNDRY_PROJECT_ENDPOINT', 'FOUNDRY_MODEL')


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


def read_env_file(path):
    values = {}
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        key = key.strip()
        value = value.strip()
        if not separator or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
            raise ValueError(f'Invalid {path.name} format on line {line_number}; expected KEY=VALUE.')
        if value[:1] in ('"', "'"):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f'Invalid {path.name} format on line {line_number}; values must use matching quotes.')
            value = value[1:-1]
        values[key] = value
    return values


def launch_environment(mock, frontend_port, backend_port, process_env=None, root=ROOT):
    profile = root / 'deploy' / ('demo.env.example' if mock else 'foundry-demo.env.example')
    env = read_env_file(profile)
    local_config = root / 'deploy' / 'foundry-demo.env'
    if not mock and local_config.is_file():
        env.update(read_env_file(local_config))
    env.update(os.environ if process_env is None else process_env)
    frontend_url = f'http://127.0.0.1:{frontend_port}'
    backend_url = f'http://127.0.0.1:{backend_port}'
    env.update(APP_ENV='local', APP_PUBLIC_DEMO='true', EVENT_SOURCE='local', APP_DEMO_CONTROLS='true', APP_AUTOSTART='false',
               AGENTIC_MODE='mock' if mock else 'foundry',
               APP_FOUNDRY_HERO_ONLY='false' if mock else 'true',
               APP_FRONTEND_ORIGINS=frontend_url, NEXT_PUBLIC_API_BASE_URL=backend_url, NEXT_PUBLIC_DEMO_SESSIONS='true',
               PYTHONUNBUFFERED='1')
    return env


def foundry_configuration_error(env):
    missing = [key for key in FOUNDRY_SETTINGS if not env.get(key, '').strip()]
    if missing:
        return ('Foundry demo configuration is missing: ' + ', '.join(missing) +
                '. Set these in deploy/foundry-demo.env or export them; values are not displayed.')
    from simulator.agentic.config import AgenticConfig
    try:
        AgenticConfig.from_env(env)
    except ValueError:
        return ('Foundry demo configuration is invalid; check FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL, '
                'and leave FOUNDRY_AGENT_ID unset. Values are not displayed.')
    return None


def main():
    parser = argparse.ArgumentParser(description='Start both MatchLens servers in one terminal. Ctrl+C stops both.')
    parser.add_argument('--mock', action='store_true', help='Use offline mock agents instead of the live Foundry hero demo.')
    parser.add_argument('--frontend-port', type=port, default=3002)
    parser.add_argument('--backend-port', type=port, default=8002)
    args = parser.parse_args()
    if args.frontend_port == args.backend_port:
        parser.error('Frontend and backend need different ports.')
    env = launch_environment(args.mock, args.frontend_port, args.backend_port)
    if not args.mock:
        config_error = foundry_configuration_error(env)
        if config_error:
            print(config_error, file=sys.stderr)
            return 2
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

    frontend_url = f'http://127.0.0.1:{args.frontend_port}'
    backend_url = f'http://127.0.0.1:{args.backend_port}'
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
