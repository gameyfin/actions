"""Build already passed without secrets. Sign only in the protected release job.
Existing release assets are reused, checked against the build, and never overwritten.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def gh(*args):
    return json.loads(subprocess.check_output(['gh', 'api', *args]))


def manifest(jar):
    with zipfile.ZipFile(jar) as archive:
        text = re.sub(r'\r?\n ', '', archive.read('META-INF/MANIFEST.MF').decode())
    return dict(line.split(': ', 1) for line in text.splitlines() if ': ' in line)


def content(jar):
    # Signing changes manifest digests and adds signature files; all other entries must match.
    with zipfile.ZipFile(jar) as archive:
        return {name: archive.read(name) for name in archive.namelist()
                if not name.endswith('/') and name.upper() != 'META-INF/MANIFEST.MF'
                and not re.fullmatch(r'META-INF/[^/]+\.(SF|RSA|DSA|EC)', name, re.I)}


def main():
    repository = os.environ['GITHUB_REPOSITORY']
    tag = os.environ['GITHUB_REF_NAME']
    assert repository.startswith('gameyfin/') and os.environ['GITHUB_REF_PROTECTED'] == 'true'
    jars = list(Path('unsigned').glob('*.jar'))
    assert len(jars) == 1, 'Expected exactly one plugin JAR'
    unsigned = jars[0]
    attrs = manifest(unsigned)
    version = attrs['Plugin-Version']
    assert re.fullmatch(r'\d+\.\d+\.\d+', version) and tag == f'v{version}', 'Tag/manifest version mismatch'
    assert attrs.get('Plugin-Requires') and attrs['Plugin-Requires'] != '*', 'Compatibility baseline required'
    assert attrs['Plugin-Url'] == f'https://github.com/{repository}'
    assert attrs['Plugin-Id'].startswith('org.gameyfin.')
    output = Path('signed'); output.mkdir()
    signed = output / unsigned.name
    result = subprocess.run(['gh', 'api', f'repos/{repository}/releases/tags/{tag}'], capture_output=True, text=True)
    if result.returncode and 'HTTP 404' not in result.stderr:
        raise RuntimeError(result.stderr)
    release = json.loads(result.stdout) if result.returncode == 0 else None
    # A tag is protected by the repository ruleset; publishing never modifies it.
    if release:
        existing = next((item for item in release['assets'] if item['name'] == signed.name), None)
        if existing:
            run('gh', 'release', 'download', tag, '--repo', repository, '--pattern', signed.name, '--dir', str(output))
            assert content(unsigned) == content(signed), 'Immutable release differs from this build'
            previous = manifest(signed)
            assert all(previous.get(k) == v for k, v in attrs.items()), 'Immutable manifest differs from this build'
    with tempfile.TemporaryDirectory() as temporary:
        temporary = Path(temporary)
        truststore = temporary / 'verify.p12'
        run('keytool', '-importcert', '-noprompt', '-alias', 'gameyfin-plugins', '-file',
            str(Path(__file__).resolve().parent.parent / 'certs' / 'gameyfin-plugins.pem'), '-keystore', str(truststore), '-storepass', 'changeit')
        if not signed.exists():
            shutil.copyfile(unsigned, signed)
            keystore = temporary / 'signing.jks'
            keystore.write_bytes(base64.b64decode(os.environ['GAMEYFIN_KEYSTORE_BASE64'], validate=True))
            keystore.chmod(0o600)
            run('jarsigner', '-keystore', str(keystore), '-storepass:env', 'GAMEYFIN_KEYSTORE_PASSWORD',
                str(signed), 'gameyfin-plugins')
        run('jarsigner', '-verify', '-strict', '-keystore', str(truststore), '-storepass', 'changeit', str(signed))
    # Must be computed after signing (or after downloading the immutable signed asset).
    with signed.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha512').hexdigest()
    checksum = signed.with_suffix(signed.suffix + '.sha512')
    checksum.write_text(f'{digest}  {signed.name}\n')
    if not release:
        run('gh', 'release', 'create', tag, '--repo', repository, '--verify-tag', '--title', tag, '--generate-notes')
        release = gh(f'repos/{repository}/releases/tags/{tag}')
    for asset in (signed, checksum):
        if any(item['name'] == asset.name for item in release['assets']):
            if asset == checksum:
                with tempfile.TemporaryDirectory() as directory:
                    run('gh', 'release', 'download', tag, '--repo', repository, '--pattern', asset.name, '--dir', directory)
                    assert (Path(directory) / asset.name).read_bytes() == asset.read_bytes(), 'Immutable checksum differs'
            continue
        run('gh', 'release', 'upload', tag, str(asset), '--repo', repository)
    Path('plugin-release.json').write_text(json.dumps({
        'attributes': attrs, 'release': {'version': version, 'requires': attrs['Plugin-Requires'],
        'url': f'https://github.com/{repository}/releases/download/{tag}/{signed.name}',
        'sha512sum': digest, 'date': gh(f'repos/{repository}/releases/tags/{tag}')['published_at']}
    }))


if __name__ == '__main__':
    main()
