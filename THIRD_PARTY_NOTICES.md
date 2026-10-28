# Third-party tools and notices

RUA-authored source and documentation use the [MIT License](LICENSE). This does not relicense external software, Unity, game content, recovered assemblies or exported assets.

Downloaded executables in `tools/` and game workspaces in `work/` are excluded from this repository. The CLI obtains tools separately and builds its own components locally. Upstream terms govern each external component.

## Manifest tools

Versions and license identifiers mirror [tools.json](tools.json). The upstream license for the downloaded version is authoritative.

| Tool | Version | Manifest license | Upstream |
| --- | --- | --- | --- |
| AssetRipper | `2.0.0` | GPL-3.0 | [AssetRipper/AssetRipper](https://github.com/AssetRipper/AssetRipper) |
| Cpp2IL | `2022.1.0-pre-release.21` | MIT | [SamboyCoding/Cpp2IL](https://github.com/SamboyCoding/Cpp2IL) |
| Il2CppDumper | `v6.7.46` | MIT | [Perfare/Il2CppDumper](https://github.com/Perfare/Il2CppDumper) |
| ilspycmd | `10.1.1.8388` | MIT | [ICSharpCode/ILSpy](https://github.com/icsharpcode/ILSpy) |
| dotnet-ilverify | `10.0.14` | MIT | [dotnet/runtime](https://github.com/dotnet/runtime) |

## .NET package references

| Package | Version | Use | Upstream |
| --- | --- | --- | --- |
| Mono.Cecil | `0.11.6` | Assembly reading and rewriting | [jbevain/cecil](https://github.com/jbevain/cecil) |
| Microsoft.NETFramework.ReferenceAssemblies | `1.0.3` | Build-time references for `net35` components | [NuGet package](https://www.nuget.org/packages/Microsoft.NETFramework.ReferenceAssemblies/1.0.3) |

NuGet restores packages separately. Their package licenses and notices govern them. Generated patcher output includes dependencies; not every generated file is RUA-authored.

## Other prerequisites

Unity Editor, Hub, modules and libraries are obtained separately under Unity's terms, with a valid license where required. The optional Python `brotli` package and GitHub Actions components retain upstream licenses.

The repository includes small original test fixtures. Commercial-game assets and binaries are not part of the source publication.

## Attribution

README organization is inspired by [morluto/rea](https://github.com/morluto/rea). The banner is an original RUA SVG. REA source, logos and screenshots are not bundled. RUA claims no affiliation with REA or Unity Technologies.
