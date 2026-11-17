"""Open/update one manifest PR. Never write the generated index or merge a PR."""
import base64
import json
from pathlib import Path
import subprocess
import urllib.parse

REPOSITORY = 'gameyfin/plugin-marketplace'


def api(path, payload=None):
    command = ['gh', 'api', path]
    if payload is not None:
        command += ['--method', 'POST', '--input', '-']
    return json.loads(subprocess.check_output(command, input=json.dumps(payload).encode() if payload is not None else None))


def optional(path):
    result = subprocess.run(['gh', 'api', path], capture_output=True, text=True)
    if result.returncode:
        if 'HTTP 404' in result.stderr:
            return None
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout)


def main():
    published = json.loads(Path('plugin-release.json').read_text())
    attrs, release = published['attributes'], published['release']
    metadata = json.loads(Path('marketplace.json').read_text())
    assert metadata['id'] == attrs['Plugin-Id']
    assert metadata['projectUrl'] == attrs['Plugin-Url']
    assert set(metadata['types']) <= {'GameMetadataProvider', 'GameStoreProvider', 'DownloadProvider', 'GameyfinTask'}
    plugin_id = metadata['id']
    path = f'plugins/{plugin_id}.json'
    repository = api(f'repos/{REPOSITORY}')
    default = repository['default_branch']
    current = optional(f'repos/{REPOSITORY}/contents/{path}?ref={default}')
    manifest = json.loads(base64.b64decode(current['content'])) if current else dict(metadata, releases=[])
    existing = next((item for item in manifest['releases'] if item['version'] == release['version']), None)
    if existing:
        assert existing['url'] == release['url'] and existing['sha512sum'] == release['sha512sum'], 'Immutable release changed'
        return
    manifest['releases'].append(release)
    branch = f'plugin-release/{plugin_id}/{release["version"]}'
    if not optional(f'repos/{REPOSITORY}/git/ref/heads/{branch}'):
        head = api(f'repos/{REPOSITORY}/git/ref/heads/{default}')['object']['sha']
        api(f'repos/{REPOSITORY}/git/refs', {'ref': f'refs/heads/{branch}', 'sha': head})
    previous = optional(f'repos/{REPOSITORY}/contents/{path}?ref={urllib.parse.quote(branch, safe="")}')
    content = (json.dumps(manifest, indent=2) + '\n').encode()
    if not previous or base64.b64decode(previous['content']) != content:
        payload = {'message': f'Add {plugin_id} {release["version"]}', 'branch': branch,
                   'content': base64.b64encode(content).decode()}
        if previous:
            payload['sha'] = previous['sha']
        subprocess.run(['gh', 'api', '--method', 'PUT', f'repos/{REPOSITORY}/contents/{path}', '--input', '-'],
                       input=json.dumps(payload).encode(), check=True)
    pulls = api(f'repos/{REPOSITORY}/pulls?head=gameyfin:{urllib.parse.quote(branch, safe="")}&state=open')
    if not pulls:
        api(f'repos/{REPOSITORY}/pulls', {'head': branch, 'base': default,
            'title': f'Add {plugin_id} {release["version"]}',
            'body': 'Publishes the signed immutable release. Marketplace validation and manual maintainer merge are required.'})


if __name__ == '__main__':
    main()
