# Gameyfin shared workflows

Shared GitHub Actions workflows and release tooling for Gameyfin plugins.

- [Plugin release workflow](.github/workflows/plugin-release.yml): builds and tests plugins, then signs and publishes releases.
- [Release script](scripts/release_plugin.py): signs and verifies plugin JARs and publishes SHA-512 checksums.
- [Marketplace script](scripts/propose_marketplace.py): opens a pull request to register a release in [gameyfin/plugin-marketplace](https://github.com/gameyfin/plugin-marketplace).
- [Public certificate](certs/gameyfin-plugins.pem): verifies plugin signatures. The private signing keystore is supplied through GitHub Actions secrets.
- [Release tests](scripts/test_release_plugin.py): cover signing, checksums, and immutable release reruns.
