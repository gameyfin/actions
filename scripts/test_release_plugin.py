"""Exercise real signing and immutable reruns against a local GitHub release double."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import release_plugin


class ReleaseTest(unittest.TestCase):
    def test_sign_checksum_and_immutable_reruns(self):
        real_run = subprocess.run
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            keystore = root / 'test.jks'
            (root / 'certs').mkdir()
            certificate = root / 'certs' / 'gameyfin-plugins.pem'
            real_run(['keytool', '-genkeypair', '-alias', 'gameyfin-plugins', '-keystore', str(keystore),
                      '-storepass', 'test-password', '-keypass', 'test-password', '-keyalg', 'RSA',
                      '-dname', 'CN=Gameyfin test', '-validity', '1'], check=True, capture_output=True)
            real_run(['keytool', '-exportcert', '-rfc', '-alias', 'gameyfin-plugins', '-keystore', str(keystore),
                      '-storepass', 'test-password', '-file', str(certificate)], check=True, capture_output=True)
            (root / 'unsigned').mkdir()
            jar = root / 'unsigned/plugin-test-1.0.0.jar'
            manifest = ('Manifest-Version: 1.0\nPlugin-Id: org.gameyfin.test\nPlugin-Version: 1.0.0\n'
                        'Plugin-Requires: >=2.7.0\nPlugin-Url: https://github.com/gameyfin/plugin-test\n\n')
            with zipfile.ZipFile(jar, 'w') as archive:
                archive.writestr('META-INF/MANIFEST.MF', manifest)
                archive.writestr('payload.txt', 'signed plugin content')
                archive.writestr('META-INF/extensions.idx', 'example.Extension')
            release = None
            assets = {}
            signed_count = 0

            def run(command, *args, **kwargs):
                nonlocal release, signed_count
                if command[0] != 'gh':
                    if command[0] == 'jarsigner' and '-verify' not in command:
                        signed_count += 1
                    return real_run(command, *args, **kwargs)
                if command[1] == 'api':
                    return subprocess.CompletedProcess(command, 0 if release else 1,
                        stdout=json.dumps(release) if release else '', stderr='' if release else 'HTTP 404')
                operation = command[2]
                if operation == 'create':
                    release = {'assets': [], 'published_at': '2026-11-17T00:00:00Z'}
                elif operation == 'upload':
                    path = Path(command[4])
                    self.assertNotIn(path.name, assets, 'Never overwrite an immutable asset')
                    assets[path.name] = path.read_bytes()
                    release['assets'].append({'name': path.name})
                elif operation == 'download':
                    name = command[command.index('--pattern') + 1]
                    directory = Path(command[command.index('--dir') + 1])
                    directory.mkdir(parents=True, exist_ok=True)
                    (directory / name).write_bytes(assets[name])
                else:
                    raise AssertionError(command)
                return subprocess.CompletedProcess(command, 0)

            environment = {'GITHUB_REPOSITORY': 'gameyfin/plugin-test', 'GITHUB_REF_NAME': 'v1.0.0',
                           'GITHUB_REF_PROTECTED': 'true', 'GAMEYFIN_KEYSTORE_PASSWORD': 'test-password',
                           'GAMEYFIN_KEYSTORE_BASE64': base64.b64encode(keystore.read_bytes()).decode()}
            previous_directory = Path.cwd()
            try:
                os.chdir(root)
                with patch.dict(os.environ, environment), patch.object(release_plugin, '__file__', str(root / 'scripts' / 'release_plugin.py')), \
                     patch.object(subprocess, 'run', run), patch.object(release_plugin, 'gh', lambda *args: copy.deepcopy(release)):
                    release_plugin.main()
                    jar_name = jar.name
                    digest = hashlib.sha512(assets[jar_name]).hexdigest()
                    self.assertEqual(assets[jar_name + '.sha512'], f'{digest}  {jar_name}\n'.encode())
                    self.assertNotEqual(assets[jar_name], jar.read_bytes())
                    published = copy.deepcopy(assets)
                    shutil.rmtree(root / 'signed')
                    release_plugin.main()
                    self.assertEqual(assets, published)
                    self.assertEqual(signed_count, 1, 'A rerun must reuse the signed artifact')
                    del assets[jar_name + '.sha512']
                    release['assets'] = [{'name': jar_name}]
                    shutil.rmtree(root / 'signed')
                    release_plugin.main()
                    self.assertEqual(assets, published, 'Repair partial publication without replacing the JAR')
                    self.assertEqual(signed_count, 1)
                    assets[jar_name + '.sha512'] = b'wrong checksum'
                    shutil.rmtree(root / 'signed')
                    with self.assertRaises(AssertionError):
                        release_plugin.main()
                    self.assertEqual(assets[jar_name], published[jar_name])
            finally:
                os.chdir(previous_directory)


if __name__ == '__main__':
    unittest.main()
