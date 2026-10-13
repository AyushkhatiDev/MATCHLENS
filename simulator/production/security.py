"""Offline secret signatures; reports locations only, never matched values."""
import re
from pathlib import Path
PATTERNS={
    'private_key':re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'github_token':re.compile(r'\bgh[pousr]_[A-Za-z0-9]{30,}\b'),
    'eventhub_key':re.compile(r'SharedAccessKey=[A-Za-z0-9+/]{20,}={0,2}'),
    'jwt':re.compile(r'\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\b'),
    'secret_assignment':re.compile(r'(?:AZURE_CLIENT_SECRET|FOUNDRY_API_KEY|OPENAI_API_KEY)\s*[=:]\s*[\"\x27]?([A-Za-z0-9_+/=-]{16,})'),
}
EXCLUDED={'.git','.venv','venv','node_modules','__pycache__','.pytest_cache'}
def scan(root):
    findings=[];count=0;env_files=[]
    for path in Path(root).rglob('*'):
        if not path.is_file() or EXCLUDED.intersection(path.parts):continue
        relative=str(path.relative_to(root))
        if (path.name=='.env' or path.name.endswith(('.env','.env.local')) or path.name.startswith('.env.')) and not path.name.endswith('example'):
            env_files.append(relative)
        if path.stat().st_size>20_000_000:continue
        try:text=path.read_text()
        except (UnicodeError,OSError):continue
        count+=1
        for name,pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                findings.append(dict(path=relative,line=text[:match.start()].count('\n')+1,signature=name))
    return dict(files_scanned=count,findings=findings,local_env_files=env_files,
        status='pass' if not findings and not env_files else 'review_required',
        scope='Signature scan of first-party source, tests, generated text and build output. Dependency directories excluded. Does not prove absence of every possible credential or IP issue.')
