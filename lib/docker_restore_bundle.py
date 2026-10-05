"""Publish a verified Docker snapshot without replacing existing data."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


def run(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout


def canonical(path):
    if (not isinstance(path, str) or not path.startswith('/') or path == '/'
            or os.path.normpath(path) != path or any(c in path for c in '\n\r*?[]')):
        raise ValueError(f'Unsupported source path: {path!r}')
    return path


def validate(data, roots=None):
    if not isinstance(data, dict) or not isinstance(data.get('project'), str):
        raise ValueError('Invalid Docker project manifest')
    sources = data.get('sources')
    if not isinstance(sources, list) or not sources:
        raise ValueError('Snapshot has no persistent data sources')
    for source in sources:
        canonical(source)
        if roots is not None and not any(source == p or source.startswith(p.rstrip('/') + '/') for p in roots):
            raise ValueError(f'Snapshot does not include {source}; select a Docker data snapshot')
    if len(set(sources)) != len(sources) or any(
            a != b and a.startswith(b + '/') for a in sources for b in sources):
        raise ValueError('Overlapping/duplicate sources are not supported')
    volumes = data.get('volumes', {})
    if not isinstance(volumes, dict):
        raise ValueError('Invalid Docker volume manifest')
    points = set()
    for name, volume in volumes.items():
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name):
            raise ValueError('Invalid Docker volume name')
        if re.fullmatch(r'[a-f0-9]{64}', name):
            raise ValueError(f'Anonymous volume {name} requires explicit Compose remapping; use selective recovery')
        if volume.get('Name') != name or volume.get('Driver') != 'local' or volume.get('Options'):
            raise ValueError(f'Unsupported volume configuration: {name}')
        point = canonical(volume.get('Mountpoint'))
        if point not in sources or point in points:
            raise ValueError(f'Volume {name} has an ambiguous or uncovered data path')
        points.add(point)
        labels = volume.get('Labels') or {}
        if not isinstance(labels, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in labels.items()):
            raise ValueError(f'Invalid labels for {name}')
    return [p for p in sources if p not in points], volumes


def fingerprint(path):
    """Compare existing recovered trees without following symbolic links."""
    result = []
    def visit(p, relative):
        info = p.lstat()
        mode = info.st_mode
        metadata = (relative, stat.S_IFMT(mode), stat.S_IMODE(mode), info.st_uid, info.st_gid)
        if stat.S_ISLNK(mode):
            result.append(metadata + (os.readlink(p),))
        elif stat.S_ISREG(mode):
            digest = hashlib.sha256()
            with p.open('rb') as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b''):
                    digest.update(block)
            result.append(metadata + (digest.hexdigest(),))
        elif stat.S_ISDIR(mode):
            result.append(metadata)
            for child in sorted(p.iterdir()):
                visit(child, str(Path(relative) / child.name))
        else:
            raise ValueError(f'Unsupported restored file type: {p}')
    visit(Path(path), '.')
    return result


def safe_destination(path):
    canonical(path)
    if os.path.realpath(path) != path:
        raise ValueError(f'Destination traverses symlinks: {path}')


def staged_path(stage, source):
    path = Path(stage) / source.lstrip('/')
    if os.path.commonpath([os.path.realpath(stage), os.path.realpath(path)]) != os.path.realpath(stage):
        raise ValueError(f'Source escapes restored directory: {source}')
    if not path.exists() or path.is_symlink():
        raise ValueError(f'Missing or symlinked restored data root: {source}')
    return path


def inspect_volume(name):
    # List first so authentication/daemon errors are not confused with absence.
    names = run('docker', 'volume', 'ls', '--format', '{{.Name}}').splitlines()
    if name not in names:
        return None
    return json.loads(run('docker', 'volume', 'inspect', name))[0]


def volume_destination(name, volume):
    if volume.get('Name') != name or volume.get('Driver') != 'local' or volume.get('Options'):
        raise ValueError(f'Existing volume has incompatible storage settings: {name}')
    path = canonical(volume['Mountpoint'])
    safe_destination(path)
    if not Path(path).is_dir() or any(Path(path).iterdir()):
        raise ValueError(f'Volume {name} must have an existing empty data directory')
    return path


def preflight(stage, data):
    ordinary, volumes = validate(data)
    restored = {p: staged_path(stage, p) for p in data['sources']}
    ids = run('docker', 'ps', '-a', '-q').splitlines()
    containers = json.loads(run('docker', 'inspect', *ids)) if ids else []
    current = {name: inspect_volume(name) for name in volumes}
    destinations = list(ordinary)
    for name, volume in current.items():
        if volume:
            destinations.append(volume_destination(name, volume))
    for container in containers:
        if not container.get('State', {}).get('Running'):
            continue
        group = data.get('project', '')
        working_dir = (container.get('Config', {}).get('Labels') or {}).get(
            'com.docker.compose.project.working_dir')
        if working_dir == group or (group.startswith('container:') and
                                   container.get('Id', '').startswith(group.split(':', 1)[1])):
            raise ValueError(f'Project container is running: {container.get("Name", container["Id"])}')
        for mount in container.get('Mounts', []):
            source = mount.get('Source', '')
            if mount.get('Type') not in ('bind', 'volume'):
                continue
            if mount.get('Name') in volumes or any(
                    source == p or source.startswith(p + '/') or p.startswith(source.rstrip('/') + '/')
                    for p in destinations if source):
                raise ValueError(f'Running container uses recovery data: {container.get("Name", container["Id"])}')
    skipped = set()
    for path in ordinary:
        safe_destination(path)
        if os.path.lexists(path):
            if fingerprint(path) != fingerprint(restored[path]):
                raise ValueError(f'Destination exists with different data: {path}; nothing overwritten')
            skipped.add(path)
    for name, volume in volumes.items():
        if not restored[volume['Mountpoint']].is_dir():
            raise ValueError(f'Restored volume data is not a directory: {name}')
    return ordinary, volumes, restored, current, skipped


def apply(stage, data):
    ordinary, volumes, restored, current, skipped = preflight(stage, data)
    for name, source_volume in volumes.items():
        volume = inspect_volume(name)  # Recheck immediately before creation/population.
        if volume is None:
            args = ['docker', 'volume', 'create', '--driver', 'local']
            for key, value in sorted((source_volume.get('Labels') or {}).items()):
                args += ['--label', key + '=' + value]
            run(*args, name)
            volume = inspect_volume(name)
            if volume is None:
                raise ValueError(f'Docker did not register volume {name}')
        destination = volume_destination(name, volume)
        source = restored[source_volume['Mountpoint']]
        run('cp', '-a', '--', str(source) + '/.', destination + '/')
        if fingerprint(source) != fingerprint(destination):
            raise ValueError(f'Copied volume verification failed: {name}')
        print(f'[OK] Volume recovered: {name} -> {destination}', flush=True)
    for path in ordinary:
        source = restored[path]
        if path in skipped:
            if fingerprint(path) != fingerprint(source):
                raise ValueError(f'Destination changed during recovery: {path}')
            print(f'[OK] Already recovered, identical: {path}', flush=True)
            continue
        safe_destination(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            os.mkdir(path, 0o700)  # Exclusive creation; do not merge existing data.
            run('cp', '-a', '--', str(source) + '/.', path + '/')
        else:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            run('cp', '-a', '--', str(source), path)
        if fingerprint(path) != fingerprint(source):
            raise ValueError(f'Copied data verification failed: {path}')
        print(f'[OK] Path recovered: {path}', flush=True)


def main():
    mode, manifest, argument = sys.argv[1:]
    try:
        data = json.loads(Path(manifest).read_text())
        if mode == 'plan':
            rows = [json.loads(line) for line in Path(argument).read_text().splitlines()]
            roots = next(row['paths'] for row in rows if row.get('struct_type') == 'snapshot')
            ordinary, volumes = validate(data, roots)
            for path in ordinary:
                print(f'[INFO] Restore path: {path}')
            for name, volume in volumes.items():
                print(f'[INFO] Restore Docker volume: {name} (source {volume["Mountpoint"]})')
            print('[INFO] Snapshot metadata remains in the recovery directory; no live staging configuration replaced.')
        elif mode == 'apply':
            apply(argument, data)
        else:
            raise ValueError('Invalid bundle operation')
    except (OSError, ValueError, KeyError, TypeError, StopIteration, subprocess.CalledProcessError) as exc:
        raise SystemExit(f'Docker bundle recovery failed: {exc}')


if __name__ == '__main__':
    main()
