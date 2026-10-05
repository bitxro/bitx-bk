"""Discover persistent project data and all running Docker consumers."""
import json
import os
import stat
import subprocess
import sys


def discover(project, containers, inspect_volume):
    standalone = project.startswith('container:')
    selected = [c for c in containers if (c.get('Id', '').startswith(project.split(':', 1)[1])
                if standalone else (c.get('Config', {}).get('Labels') or {}).get(
                    'com.docker.compose.project.working_dir') == project)]
    if not selected:
        raise ValueError(f'No containers match project {project}; aborting incomplete backup')
    paths = set() if standalone else {os.path.realpath(project)}
    volumes = {}
    skipped = []
    for container in selected:
        for mount in container.get('Mounts', []):
            kind = mount.get('Type')
            if kind not in ('bind', 'volume'):
                continue
            path = mount.get('Source', '')
            if kind == 'volume':
                name = mount['Name']
                if name not in volumes:
                    volumes[name] = inspect_volume(name)
                volume = volumes[name]
                if volume.get('Driver') != 'local' or volume.get('Options'):
                    raise ValueError(f'Volume {name} uses a driver/options requiring a dedicated backup; aborting')
                path = volume['Mountpoint']
            if not os.path.isabs(path) or '\n' in path or '\r' in path:
                raise ValueError(f'Invalid persistent mount source: {path!r}')
            path = os.path.realpath(path)
            if path == '/':
                raise ValueError('A root filesystem bind mount requires an explicit backup policy')
            mode = os.stat(path).st_mode  # Missing/inaccessible data must fail, never silently skip.
            if stat.S_ISSOCK(mode):
                skipped.append(path)
                continue
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ValueError(f'Unsupported persistent mount source: {path}')
            paths.add(path)
    # Child sources are already covered by the parent source.
    sources = sorted(p for p in paths if not any(
        p.startswith(parent.rstrip('/') + '/') for parent in paths if parent != p))
    running = []
    for container in containers:
        if not container.get('State', {}).get('Running'):
            continue
        consumer = container in selected
        for mount in container.get('Mounts', []):
            source = mount.get('Source')
            if not source or mount.get('Type') not in ('bind', 'volume'):
                continue
            source = os.path.realpath(source)
            if any(source == p or source.startswith(p + '/') or p.startswith(source + '/')
                   for p in sources):
                consumer = True
            if mount.get('Name') in volumes:
                consumer = True
        if consumer:
            running.append(container['Id'])
    return {'project': project, 'sources': sources, 'running_consumers': sorted(set(running)),
            'volumes': volumes, 'skipped_sockets': sorted(set(skipped))}


def main():
    project, inventory, output = sys.argv[1:]
    def inspect_volume(name):
        result = subprocess.run(['docker', 'volume', 'inspect', name], check=True,
                                text=True, capture_output=True)
        return json.loads(result.stdout)[0]
    try:
        with open(inventory) as handle:
            containers = json.load(handle)
        data = discover(project, containers, inspect_volume)
        with open(output, 'w') as handle:
            json.dump(data, handle, indent=2)
        for path in data['skipped_sockets']:
            print(f'[INFO] Runtime socket excluded: {path}', file=sys.stderr)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f'Docker persistent data discovery failed: {exc}')


if __name__ == '__main__':
    main()
