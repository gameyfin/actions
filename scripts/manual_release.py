"""Prepare a version bump and publish the tested commit and protected tag."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

MANIFEST = 'src/main/resources/MANIFEST.MF'
VERSION = re.compile(rb'^Plugin-Version: (0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?=\r?$)', re.M)
BUMPS = ('major', 'minor', 'patch')
CREATION = 'Gameyfin release tag creation'
IMMUTABILITY = 'Gameyfin release tag immutability'
TAG_CONDITION = {'ref_name': {'include': ['refs/tags/v*'], 'exclude': []}}


def bump(manifest, part):
    matches = list(VERSION.finditer(manifest))
    if len(matches) != 1:
        raise ValueError('Expected exactly one numeric Plugin-Version in the manifest')
    match = matches[0]
    numbers = [int(number) for number in match.groups()]
    index = BUMPS.index(part)
    numbers[index] += 1
    numbers[index + 1:] = [0] * (2 - index)
    version = '.'.join(map(str, numbers))
    return manifest[:match.start()] + f'Plugin-Version: {version}'.encode() + manifest[match.end():], version


def git(checkout, *args):
    return subprocess.check_output(['git', '-C', str(checkout), *args])


def ruleset_matches(ruleset, *rules):
    return (ruleset.get('target') == 'tag' and ruleset.get('enforcement') == 'active'
            and ruleset.get('conditions') == TAG_CONDITION
            and {rule['type'] for rule in ruleset['rules']} == set(rules))


def assert_tag_protection(repository):
    endpoint = f'repos/{repository}/rulesets'
    pages = json.loads(subprocess.check_output([
        'gh', 'api', f'{endpoint}?includes_parents=false&per_page=100', '--paginate', '--slurp'
    ]))
    rulesets = {entry['name']: entry['id'] for page in pages for entry in page}
    for name, expected in ((CREATION, ('creation',)), (IMMUTABILITY, ('update', 'deletion'))):
        if name not in rulesets:
            raise ValueError(f'Missing tag ruleset: {name}')
        rule = json.loads(subprocess.check_output(['gh', 'api', f'{endpoint}/{rulesets[name]}']))
        if not ruleset_matches(rule, *expected) or (name == IMMUTABILITY and rule.get('bypass_actors')):
            raise ValueError(f'Invalid tag ruleset: {name}')



def publish_refs(checkout, base, branch, manifest, version):
    tag = f'v{version}'
    tag_ref = f'refs/tags/{tag}'
    remote = git(checkout, 'ls-remote', 'origin', tag_ref).split()
    if remote:
        # A rerun may resume after the atomic push, even if the branch has moved on.
        git(checkout, 'fetch', '--no-tags', 'origin', tag_ref)
        commit = git(checkout, 'rev-parse', 'FETCH_HEAD^{commit}').decode().strip()
        parents = git(checkout, 'rev-list', '--parents', '-n', '1', commit).decode().split()
        paths = git(checkout, 'diff-tree', '--no-commit-id', '--name-only', '-r', commit).decode().splitlines()
        if (parents != [commit, base] or paths != [MANIFEST]
                or git(checkout, 'show', f'{commit}:{MANIFEST}') != manifest):
            raise ValueError(f'{tag} already exists for a different release')
        return

    if git(checkout, 'rev-parse', 'HEAD').decode().strip() != base:
        raise ValueError('Release checkout differs from the tested source')
    (checkout / MANIFEST).write_bytes(manifest)
    git(checkout, 'add', '--', MANIFEST)
    git(checkout, '-c', 'user.name=gameyfin-release[bot]',
        '-c', 'user.email=gameyfin-release[bot]@users.noreply.github.com',
        'commit', '-m', f'Release {tag}', '--', MANIFEST)
    # Both refs are published or neither is. A branch update during the build rejects the push.
    git(checkout, 'push', '--atomic', '--no-follow-tags', 'origin',
        f'HEAD:refs/heads/{branch}', f'HEAD:{tag_ref}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('prepare', 'publish'))
    parser.add_argument('bump', choices=BUMPS)
    parser.add_argument('checkout', type=Path)
    args = parser.parse_args()
    path = args.checkout / MANIFEST
    updated, version = bump(git(args.checkout, 'show', f'HEAD:{MANIFEST}'), args.bump)
    if args.operation == 'prepare':
        path.write_bytes(updated)
        return

    from release_plugin import checked_artifact, publish

    repository = os.environ['GITHUB_REPOSITORY']
    tag = f'v{version}'
    # Validate the built artifact and policy before modifying any refs.
    checked_artifact(repository, tag)
    assert_tag_protection(repository)
    publish_refs(args.checkout, os.environ['GITHUB_SHA'], os.environ['GITHUB_REF_NAME'], updated, version)
    publish(repository, tag)


if __name__ == '__main__':
    main()
