from scripts.start_project import (
    foundry_configuration_error,
    launch_environment,
)
from simulator.production.config import AppConfig


def project_root(tmp_path):
    deploy = tmp_path / 'deploy'
    deploy.mkdir()
    (deploy / 'foundry-demo.env.example').write_text(
        'APP_ENV=demo\nEVENT_SOURCE=local\nAPP_DEMO_CONTROLS=true\n'
        'AGENTIC_MODE=foundry\nFOUNDRY_PROJECT_ENDPOINT=\nFOUNDRY_MODEL=\n'
    )
    (deploy / 'demo.env.example').write_text(
        'APP_ENV=demo\nEVENT_SOURCE=local\nAPP_DEMO_CONTROLS=true\nAGENTIC_MODE=mock\n'
    )
    return tmp_path


def test_foundry_local_file_loads_and_exported_values_take_precedence(tmp_path):
    root = project_root(tmp_path)
    (root / 'deploy/foundry-demo.env').write_text(
        'FOUNDRY_PROJECT_ENDPOINT=https://local.services.ai.azure.com/api/projects/local\n'
        'FOUNDRY_MODEL=local-deployment\n'
    )
    env = launch_environment(False, 3002, 8002, {
        'FOUNDRY_PROJECT_ENDPOINT': 'https://exported.services.ai.azure.com/api/projects/exported',
        'FOUNDRY_MODEL': 'exported-deployment',
    }, root)
    assert env['AGENTIC_MODE'] == 'foundry'
    assert env['APP_FRONTEND_ORIGINS'] == 'http://127.0.0.1:3002'
    assert env['NEXT_PUBLIC_API_BASE_URL'] == 'http://127.0.0.1:8002'
    assert env['FOUNDRY_PROJECT_ENDPOINT'].endswith('/exported')
    assert env['FOUNDRY_MODEL'] == 'exported-deployment'
    assert foundry_configuration_error(env) is None
    public_env = {**env, 'APP_ENV': 'demo', 'APP_FOUNDRY_HERO_ONLY': 'true',
                  'APP_DIAGNOSTICS': 'false'}
    config = AppConfig.from_env(public_env)
    assert config.environment == 'demo'
    assert config.agent.mode == 'foundry'
    assert config.foundry_hero_only


def test_example_template_alone_is_not_a_valid_foundry_deployment(tmp_path):
    env = launch_environment(False, 3002, 8002, {}, project_root(tmp_path))
    error = foundry_configuration_error(env)
    assert error is not None
    assert 'FOUNDRY_PROJECT_ENDPOINT' in error
    assert 'FOUNDRY_MODEL' in error


def test_foundry_missing_setting_error_names_only_missing_variables():
    error = foundry_configuration_error({
        'AGENTIC_MODE': 'foundry',
        'FOUNDRY_PROJECT_ENDPOINT': 'https://example.services.ai.azure.com/api/projects/test',
    })
    assert error is not None
    assert 'FOUNDRY_MODEL' in error
    assert 'FOUNDRY_PROJECT_ENDPOINT' not in error
    assert 'https://' not in error


def test_invalid_foundry_values_are_not_exposed_in_startup_error():
    sentinel = 'do-not-print-this-token'
    error = foundry_configuration_error({
        'AGENTIC_MODE': 'foundry',
        'FOUNDRY_PROJECT_ENDPOINT': f'https://example.invalid/?token={sentinel}',
        'FOUNDRY_MODEL': 'test-deployment',
    })
    assert error is not None
    assert sentinel not in error
    assert 'FOUNDRY_PROJECT_ENDPOINT' in error
    assert 'FOUNDRY_MODEL' in error


def test_mock_launcher_keeps_its_profile_and_ignores_foundry_file(tmp_path):
    root = project_root(tmp_path)
    (root / 'deploy/foundry-demo.env').write_text(
        'FOUNDRY_PROJECT_ENDPOINT=https://example.invalid/api/projects/private\n'
        'FOUNDRY_MODEL=private-deployment\n'
    )
    env = launch_environment(True, 3002, 8002, {}, root)
    assert env['AGENTIC_MODE'] == 'mock'
    assert env['APP_FRONTEND_ORIGINS'] == 'http://127.0.0.1:3002'
    assert 'FOUNDRY_PROJECT_ENDPOINT' not in env
    assert 'FOUNDRY_MODEL' not in env
    assert AppConfig.from_env(env).agent.mode == 'mock'


def test_local_foundry_profile_is_gitignored():
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ['git', 'check-ignore', '--quiet', 'deploy/foundry-demo.env'],
        cwd=root,
        check=False,
    )
    assert result.returncode == 0


def test_direct_script_run_can_import_simulator_for_configuration_check(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    # Mirror start.sh: the repository root is not on the import path.
    script = Path(__file__).resolve().parents[1] / 'scripts/start_project.py'
    probe = (
        'import runpy, sys\n'
        f'check = runpy.run_path({str(script)!r}, run_name="probe")["foundry_configuration_error"]\n'
        'print(check({"AGENTIC_MODE": "foundry",\n'
        '             "FOUNDRY_PROJECT_ENDPOINT": "https://example.services.ai.azure.com/api/projects/test",\n'
        '             "FOUNDRY_MODEL": "test-deployment"}))\n'
    )
    result = subprocess.run([sys.executable, '-I', '-c', probe], cwd=tmp_path,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'None'


def test_import_failure_is_not_reported_as_invalid_configuration(monkeypatch):
    import sys

    import pytest

    monkeypatch.setitem(sys.modules, 'simulator.agentic.config', None)
    with pytest.raises(ImportError):
        foundry_configuration_error({
            'AGENTIC_MODE': 'foundry',
            'FOUNDRY_PROJECT_ENDPOINT': 'https://example.services.ai.azure.com/api/projects/test',
            'FOUNDRY_MODEL': 'test-deployment',
        })
