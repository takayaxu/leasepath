"""Private offline task environments and checks from the actual Agent shell."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess


def prepare_environment(work, template, destination, repo):
    work, template, destination = Path(work).absolute(), Path(template).absolute(), Path(destination).absolute()
    shutil.copytree(template, destination, symlinks=True)
    # Copied console scripts retain template shebangs. Replace all Python CLI
    # entry points we allow with module wrappers pointing into the private copy.
    python = destination / 'bin/python'
    wrappers = destination.parent / 'tool-bin'
    wrappers.mkdir()
    # rg is supplied as a standalone Codex binary on this host, outside /usr/bin.
    # Copy only this inspected executable, never a whole host Python bin path.
    rg = shutil.which('rg')
    if rg:
        shutil.copy2(rg, wrappers / 'rg')
    for command, module in [('python', None), ('python3', None), ('pytest', 'pytest'),
                            ('py.test', 'pytest'), ('flask', 'flask'), ('pip', 'pip')]:
        script = wrappers / command
        script.write_text('#!/bin/sh\nexec ' + shlex.quote(str(python)) +
                          ((' -m ' + module) if module else '') + ' "$@"\n')
        script.chmod(0o755)
    env = {key: value for key, value in os.environ.items()
           if key not in {'PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'CONDA_PREFIX', 'CONDA_DEFAULT_ENV',
                          'PYTEST_ADDOPTS', 'PYTEST_PLUGINS', 'LD_PRELOAD', 'LD_LIBRARY_PATH', 'BASH_ENV', 'ENV'}}
    env.update(PATH=str(wrappers) + ':/usr/bin:/bin', PYTHONPATH=str(work / 'src'),
               PYTHONNOUSERSITE='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
               OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1',
               PAGER='cat', PYTHONHASHSEED='23', PIP_NO_INDEX='1')
    if repo == 'pytest-dev/pytest':
        env['SETUPTOOLS_SCM_PRETEND_VERSION'] = '6.3.0.dev0'
        built = subprocess.run([str(python), '-m', 'pip', 'install', '--no-deps', '--no-build-isolation',
                                '--no-index', '.'], cwd=work, env=env, capture_output=True, text=True, timeout=120)
        (destination.parent / 'package-build.log').write_text(built.stdout + built.stderr)
        if built.returncode:
            raise ValueError('private pytest build failed')
    else:
        env['PYTEST_ADDOPTS'] = shlex.join([part for api in ['get_loader', 'find_loader']
            for part in ['-W', f"ignore:'pkgutil.{api}' is deprecated:DeprecationWarning"]])
    return env, wrappers


def preflight_environment(work, env, wrappers, repo, *, cpus):
    work, wrappers = Path(work).absolute(), Path(wrappers).absolute()
    module = 'flask' if repo == 'pallets/flask' else '_pytest'
    code = ('import sys,json,importlib,importlib.metadata as m; '
            f'mod=importlib.import_module({module!r}); '
            'print(json.dumps({"executable":sys.executable,"prefix":sys.prefix,"source":mod.__file__, '
            '"python_version":sys.version,"dependencies":{d.metadata["Name"]:d.version for d in m.distributions()}, '
            f'"dependency_imports":{{n:importlib.import_module(n).__file__ for n in {(["click","werkzeug","jinja2"] if repo=="pallets/flask" else ["pluggy","packaging"])!r}}}}}))')
    command = 'command -v python pytest flask; python -c ' + shlex.quote(code)
    result = subprocess.run(['taskset', '-c', ','.join(map(str, cpus)), '/bin/bash', '-c', command],
                            cwd=work, env=env, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError('actual tool shell preflight failed: ' + result.stderr)
    lines = result.stdout.splitlines()
    expected = [str(wrappers / c) for c in ['python', 'pytest', 'flask']]
    if lines[:3] != expected:
        raise ValueError('tool entry resolved outside frozen wrappers')
    evidence = json.loads(lines[3])
    if not Path(evidence['source']).is_relative_to(work / 'src'):
        raise ValueError('imported source outside task checkout')
    private_prefix = wrappers.parent / 'environment'
    if Path(evidence['prefix']) != private_prefix or Path(evidence['executable']) != private_prefix / 'bin/python':
        raise ValueError('Python interpreter escaped private environment')
    if any(not Path(p).is_relative_to(private_prefix) for p in evidence['dependency_imports'].values()):
        raise ValueError('dependency import escaped private environment')
    for name in ['pytest'] + (['flask'] if repo == 'pallets/flask' else []):
        check = subprocess.run(['/bin/bash', '-c', name + ' --version'], cwd=work, env=env,
                               capture_output=True, text=True, timeout=30)
        if check.returncode:
            raise ValueError('frozen module CLI failed: ' + check.stdout + check.stderr)
    return {'cwd': str(work), 'shell': '/bin/bash -c', 'resolved_entries': dict(zip(['python', 'pytest', 'flask'], expected)),
            **evidence, 'PATH': env['PATH'], 'PYTHONPATH': env['PYTHONPATH'], 'cpu_affinity': cpus,
            'wrapper_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in wrappers.iterdir()},
            'offline': True, 'system_tools': {c: shutil.which(c, path=env['PATH'])
                                            for c in ['git', 'sed', 'rg', 'find', 'bash', 'taskset']}}
