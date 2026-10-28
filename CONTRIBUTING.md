# Contributing to REU

Reproducible compatibility findings, focused fixes, clearer documentation and original fixtures are welcome.

## Development setup

Clone the repository, install Python 3.13 and .NET 10 SDK, then run:

```powershell
python u2w.py --help
python -m unittest discover -s tests -v
```

Most tests use synthetic inputs. Installed Unity assemblies enable an optional compilation check. Full WebGL builds also need matching modules and a valid license. See [testing](docs/TESTING.md).

Preserve `u2w`, `unity2web`, assembly names and workspace contracts unless explicitly documenting a migration.

## Report a problem

Open an [issue](https://github.com/Luinseng/REU/issues) with:

- Command, source revision, Python/.NET versions and Unity version.
- Mono/IL2CPP backend, expected behavior and a short redacted error excerpt.
- A minimal original or redistributable reproduction.
- The failed stage: detection, export, scan, patch, verification, build, serving, rendering or gameplay.

Do not attach proprietary game files, account data, keys, tokens, license files, or unredacted private paths. Inspect logs before sharing.

## Pull requests

Keep changes focused. Explain the resulting behavior and include affected checks. Distinguish automated tests from manual Unity/browser results. Add a meaningful small regression check when changing behavior.

Keep tools, game exports, caches, reports and compiled output outside commits. `tools/` and `work/` are deliberately ignored. Record new dependencies and their terms in [third-party notices](THIRD_PARTY_NOTICES.md).

Submit only material you are authorized to contribute. REU-authored contributions use the repository MIT License; third-party material keeps its terms.
