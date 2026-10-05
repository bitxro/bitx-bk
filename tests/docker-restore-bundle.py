import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('bundle', Path(__file__).parents[1] / 'lib/docker_restore_bundle.py')
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.stage = self.work / 'stage'
        self.project = str(self.work / 'live/docker/bitx-media')
        self.bind = str(self.work / 'live/external-data')
        self.source_points = {name: str(self.work / 'old-docker/volumes' / name / '_data')
                              for name in ('bitx-media_cookies', 'bitx-media_database', 'bitx-media_downloads')}
        self.data = {'project':self.project, 'sources':[self.project,self.bind,*self.source_points.values()],
                     'volumes':{name:{'Name':name,'Driver':'local','Options':None,'Mountpoint':point,
                                     'Labels':{'com.docker.compose.project':'bitx-media'}}
                                for name,point in self.source_points.items()}}
        for index, source in enumerate(self.data['sources']):
            path = self.stage / source.lstrip('/')
            path.mkdir(parents=True)
            (path / '.hidden').write_text(f'fixture-{index}')
            (path / '.hidden').chmod(0o640)
            (path / 'link').symlink_to('.hidden')
        self.volumes = {}
        self.created = []
        self.containers = []
        self.real_run = bundle.run
        self.patcher = patch.object(bundle, 'run', side_effect=self.docker_command)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def docker_command(self, *args):
        if args[0] != 'docker':
            return self.real_run(*args)
        if args[1:3] == ('volume','ls'):
            return '\n'.join(self.volumes)
        if args[1:3] == ('volume','inspect'):
            return json.dumps([self.volumes[args[3]]])
        if args[1:3] == ('volume','create'):
            name = args[-1]
            destination = self.work / 'new-docker/volumes' / name / '_data'
            destination.mkdir(parents=True)
            self.volumes[name] = {'Name':name,'Driver':'local','Options':None,'Mountpoint':str(destination)}
            self.created.append(args)
            return name
        if args[1] == 'ps':
            return '\n'.join(c['Id'] for c in self.containers)
        if args[1] == 'inspect':
            return json.dumps(self.containers)
        raise AssertionError(args)

    def test_restore_project_three_volumes_and_bind(self):
        bundle.validate(self.data, self.data['sources'])
        bundle.apply(str(self.stage), self.data)
        self.assertEqual(len(self.created), 3)
        for name,point in self.source_points.items():
            source = self.stage / point.lstrip('/')
            destination = self.volumes[name]['Mountpoint']
            self.assertEqual(bundle.fingerprint(source), bundle.fingerprint(destination))
        for path in (self.project,self.bind):
            self.assertEqual(bundle.fingerprint(self.stage/path.lstrip('/')),bundle.fingerprint(path))
        self.assertTrue(all('--label' in args for args in self.created))

    def test_existing_identical_project_is_retained(self):
        shutil.copytree(self.stage/self.project.lstrip('/'), self.project, symlinks=True)
        bundle.apply(str(self.stage), self.data)
        self.assertEqual(len(self.created), 3)

    def test_different_existing_project_aborts_before_creating_volumes(self):
        Path(self.project).mkdir(parents=True)
        (Path(self.project)/'important').write_text('keep')
        with self.assertRaises(ValueError): bundle.apply(str(self.stage),self.data)
        self.assertEqual(self.created, [])
        self.assertEqual((Path(self.project)/'important').read_text(),'keep')

    def test_nonempty_existing_volume_is_not_overwritten(self):
        self.docker_command('docker','volume','create','--driver','local','bitx-media_database')
        path=Path(self.volumes['bitx-media_database']['Mountpoint'])/'live.db'
        path.write_text('keep')
        with self.assertRaises(ValueError): bundle.apply(str(self.stage),self.data)
        self.assertEqual(path.read_text(),'keep')
        self.assertEqual(len(self.created),1)

    def test_empty_registered_volume_can_be_populated(self):
        self.docker_command('docker','volume','create','--driver','local','bitx-media_database')
        bundle.apply(str(self.stage),self.data)
        self.assertEqual(len(self.created),3)

    def test_running_consumer_blocks_all_changes(self):
        self.containers=[{'Id':'running','Name':'/other','State':{'Running':True},'Mounts':[
            {'Type':'volume','Name':'bitx-media_database','Source':'/different-docker-root/db/_data'}]}]
        with self.assertRaises(ValueError): bundle.apply(str(self.stage),self.data)
        self.assertEqual(self.created,[])

    def test_missing_snapshot_root_and_unsupported_driver_refused(self):
        with self.assertRaises(ValueError): bundle.validate(self.data,[self.project])
        self.data['volumes']['bitx-media_cookies']['Driver']='nfs'
        with self.assertRaises(ValueError): bundle.apply(str(self.stage),self.data)
        self.assertEqual(self.created,[])

    def test_symlink_destination_refused(self):
        actual=self.work/'other'; actual.mkdir()
        (self.work/'live').symlink_to(actual, target_is_directory=True)
        with self.assertRaises(ValueError): bundle.apply(str(self.stage),self.data)
        self.assertEqual(self.created,[])


if __name__ == '__main__':
    unittest.main()
