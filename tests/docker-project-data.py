import importlib.util
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('data', Path(__file__).parents[1] / 'lib/docker_project_data.py')
data = importlib.util.module_from_spec(spec)
spec.loader.exec_module(data)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.project = base / 'docker/app'
        self.volume = base / 'custom-docker-root/volumes/db/_data'
        self.bind = base / 'external-data'
        for path in (self.project, self.volume, self.bind):
            path.mkdir(parents=True)
        (self.volume / 'important.db').write_text('persistent')
        self.volumes = {'db': {'Name': 'db', 'Driver': 'local', 'Options': None,
                               'Mountpoint': str(self.volume)}}
        self.containers = [
            {'Id': 'app', 'Config': {'Labels': {'com.docker.compose.project.working_dir': str(self.project)}},
             'State': {'Running': True}, 'Mounts': [
                 {'Type': 'volume', 'Name': 'db', 'Source': str(self.volume)},
                 {'Type': 'bind', 'Source': str(self.bind)}]},
            {'Id': 'shared', 'Config': {'Labels': {}}, 'State': {'Running': True},
             'Mounts': [{'Type': 'volume', 'Name': 'db', 'Source': str(self.volume)}]},
            {'Id': 'stopped', 'State': {'Running': False}, 'Mounts': [{'Type': 'bind', 'Source': str(self.bind)}]},
            {'Id': 'unrelated', 'State': {'Running': True}, 'Mounts': []}]

    def discover(self):
        return data.discover(str(self.project), self.containers, self.volumes.__getitem__)

    def test_persistent_data_and_shared_consumers(self):
        result = self.discover()
        self.assertEqual(set(result['sources']), {str(self.project), str(self.volume), str(self.bind)})
        self.assertEqual(result['running_consumers'], ['app', 'shared'])
        self.assertEqual(result['volumes']['db']['Mountpoint'], str(self.volume))

    def test_missing_bind_aborts(self):
        self.containers[0]['Mounts'].append({'Type': 'bind', 'Source': str(self.bind / 'missing')})
        with self.assertRaises(FileNotFoundError): self.discover()

    def test_remote_volume_aborts(self):
        self.volumes['db']['Options'] = {'type': 'nfs', 'device': ':/data'}
        with self.assertRaises(ValueError): self.discover()

    def test_runtime_socket_excluded(self):
        path = self.project / 'runtime.sock'
        self.containers[0]['Mounts'].append({'Type': 'bind', 'Source': str(path)})
        real_stat = data.os.stat
        def fixture_stat(p, *args, **kwargs):
            if str(p) == str(path):
                return type('SocketStat', (), {'st_mode': stat.S_IFSOCK})()
            return real_stat(p, *args, **kwargs)
        with patch.object(data.os, 'stat', side_effect=fixture_stat):
            self.assertEqual(self.discover()['skipped_sockets'], [str(path)])

    def test_standalone_volume_captured(self):
        result = data.discover('container:shared', self.containers, self.volumes.__getitem__)
        self.assertEqual(result['sources'], [str(self.volume)])
        self.assertEqual(result['running_consumers'], ['app', 'shared'])

    def test_short_container_id_matches_full_inspect_id(self):
        self.containers[1]['Id'] = 'abcdef1234567890'
        result = data.discover('container:abcdef123456', self.containers, self.volumes.__getitem__)
        self.assertEqual(result['sources'], [str(self.volume)])

    def test_overlapping_bind_consumer_stopped(self):
        self.containers[3]['Mounts'] = [{'Type': 'bind', 'Source': str(self.bind / 'child')}]
        self.assertIn('unrelated', self.discover()['running_consumers'])

    def test_unmatched_project_aborts(self):
        with self.assertRaises(ValueError): data.discover('/missing/project', self.containers, self.volumes.__getitem__)


if __name__ == '__main__':
    unittest.main()
