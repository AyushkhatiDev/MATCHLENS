"""Installed dependency metadata inventory, not a legal compliance certification."""
import importlib.metadata,json
from pathlib import Path
python=[]
for distribution in importlib.metadata.distributions():
    metadata=distribution.metadata
    license_name=metadata.get('License-Expression') or metadata.get('License') or 'not_declared'
    python.append({'name':metadata['Name'],'version':distribution.version,'license':license_name.splitlines()[0][:250],
        'license_files':[str(p) for p in distribution.files or [] if 'license' in str(p).lower() or str(p).endswith('COPYING')]})
node=[]
for path in Path('frontend/node_modules').glob('**/package.json'):
    try:package=json.loads(path.read_text())
    except (OSError,ValueError):continue
    if package.get('name') and package.get('version'):node.append({'name':package['name'],'version':package['version'],'license':package.get('license','not_declared')})
report={'scope':'Currently installed Python and Node dependencies, including dev tooling. Retain their license files in distributions. Unknown declarations require manual review.','python':sorted(python,key=lambda p:p['name'].lower()),'node':sorted(node,key=lambda p:p['name'].lower())}
Path('docs/submission/third-party-licenses.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'python_distributions':len(python),'node_packages':len(node)}))

notices=['Third-party notices from installed Node dependencies. Python license files remain inside the packaged distribution metadata.']
for package_file in sorted(Path('frontend/node_modules').glob('**/package.json')):
    try:package=json.loads(package_file.read_text())
    except (OSError,ValueError):continue
    for pattern in ('LICENSE*','LICENCE*','COPYING*','NOTICE*'):
        for notice in sorted(package_file.parent.glob(pattern)):
            if not notice.is_file():continue
            try:body=notice.read_text()
            except (OSError,UnicodeError):continue
            notices.append('\n--- '+package.get('name','unnamed')+' '+package.get('version','')+' / '+notice.name+' ---\n'+body)
Path('docs/submission/third-party-notices.txt').write_text('\n'.join(notices)+'\n')
