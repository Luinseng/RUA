using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;
using Mono.Cecil;
using Mono.Cecil.Cil;
using Mono.Cecil.Rocks;

namespace Unity2Web.Patcher;

/// <summary>
/// u2w-patcher: scans compiled game assemblies for APIs a browser cannot provide and rewrites them at IL level.
/// The original game IL is kept; only call sites and native imports are touched, so the game logic
/// that IL2CPP compiles to WebAssembly is the game's own.
///
///   u2w-patcher scan  &lt;dir|dll&gt;... [--json out.json] [--rules rules.json] [--all]
///   u2w-patcher patch &lt;dir&gt; --backup &lt;dir&gt; [--rules rules.json] [--report out.json] [--resolve &lt;dir&gt;]...
/// </summary>
internal static class Program
{
    private static readonly JsonSerializerOptions JsonOut = new()
    {
        WriteIndented = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        Converters = { new JsonStringEnumConverter() },
    };

    private static int Main(string[] args)
    {
        if (args.Length == 0 || args[0] is "-h" or "--help")
        {
            Console.WriteLine("usage: u2w-patcher scan <dir|dll>... [--json out.json] [--rules rules.json] [--all]");
            Console.WriteLine("       u2w-patcher patch <dir> --backup <dir> [--rules rules.json] [--report out.json] [--resolve <dir>]...");
            return args.Length == 0 ? 2 : 0;
        }

        var options = Options.Parse(args.Skip(1).ToArray());
        try
        {
            return args[0] switch
            {
                "scan" => Scan(options),
                "patch" => Patch(options),
                _ => Fail($"unknown command '{args[0]}'"),
            };
        }
        catch (Exception e)
        {
            Console.Error.WriteLine($"u2w-patcher: {e}");
            return 1;
        }
    }

    private static int Fail(string message)
    {
        Console.Error.WriteLine($"u2w-patcher: {message}");
        return 2;
    }

    private static int Scan(Options options)
    {
        RulesFile rules = RulesFile.Load(options.Rules);
        var reports = new List<AssemblyReport>();
        foreach (string file in AssemblyFiles.Enumerate(options.Positional))
        {
            if (!options.All && AssemblyFiles.IsExcluded(file, rules)) continue;
            using ModuleDefinition? module = AssemblyFiles.TryRead(file, options.Resolve, writable: false);
            if (module is null) continue;
            reports.Add(Scanner.ScanModule(module, file, rules));
        }

        var result = ScanResult.From(reports);
        result.PrintSummary();
        if (options.Json is not null)
        {
            File.WriteAllText(options.Json, JsonSerializer.Serialize(result, JsonOut));
            Console.WriteLine($"report: {options.Json}");
        }
        return 0;
    }

    private static int Patch(Options options)
    {
        if (options.Positional.Count != 1) return Fail("patch takes exactly one directory");
        if (options.Backup is null) return Fail("patch requires --backup <dir> (originals are kept there and always patched from)");

        string root = Path.GetFullPath(options.Positional[0]);
        string backupRoot = Path.GetFullPath(options.Backup);
        RulesFile rules = RulesFile.Load(options.Rules);
        var results = new List<PatchReport>();

        Dictionary<string, string> renames = rules.RenameAssemblies;
        var renamedOutputs = new HashSet<string>(renames.Values, StringComparer.OrdinalIgnoreCase);

        // Phase 1: keep a pristine copy of every game assembly. Files we produced under a new name are skipped.
        foreach (string file in AssemblyFiles.Enumerate([root]))
        {
            if (AssemblyFiles.IsExcluded(file, rules) || renamedOutputs.Contains(Path.GetFileNameWithoutExtension(file))) continue;
            string backup = Path.Combine(backupRoot, Path.GetRelativePath(root, file));
            if (File.Exists(backup) || !AssemblyFiles.IsManaged(file)) continue;
            if (AssemblyFiles.ReferencesShims(file))
            {
                Console.Error.WriteLine($"u2w-patcher: {Path.GetRelativePath(root, file)} is already patched but has no backup; re-export the game to get a clean original");
                continue;
            }
            Directory.CreateDirectory(Path.GetDirectoryName(backup)!);
            File.Copy(file, backup);
        }

        // Phase 2: always patch from the backups, so running patch again gives the same result.
        if (Directory.Exists(backupRoot))
        {
            foreach (string backup in AssemblyFiles.Enumerate([backupRoot]))
            {
                if (AssemblyFiles.IsExcluded(backup, rules)) continue;
                string relative = Path.GetRelativePath(backupRoot, backup);
                string target = Path.Combine(root, relative);

                var resolveDirs = new List<string>(options.Resolve) { Path.GetDirectoryName(backup)!, Path.GetDirectoryName(target)!, root };
                using ModuleDefinition? module = AssemblyFiles.TryRead(backup, resolveDirs, writable: true);
                if (module is null) continue;

                PatchReport report = new IlPatcher(module, rules).Run();
                report.Assembly = relative;
                Renamer.Apply(module, renames, report);
                string output = report.RenamedTo is null
                    ? target
                    : Path.Combine(Path.GetDirectoryName(target)!, report.RenamedTo + Path.GetExtension(target));
                report.Output = Path.GetRelativePath(root, output);

                if (report.Total == 0)
                {
                    File.Copy(backup, output, overwrite: true);
                }
                else
                {
                    string temp = output + ".u2wtmp";
                    module.Write(temp);
                    File.Move(temp, output, overwrite: true);
                }
                if (!string.Equals(output, target, StringComparison.OrdinalIgnoreCase)) Renamer.MoveMeta(target, output);

                results.Add(report);
                Console.WriteLine($"{(report.Total == 0 ? "unchanged" : "patched  ")} {relative}  {report.Describe()}");
            }
        }

        if (options.Report is not null)
        {
            File.WriteAllText(options.Report, JsonSerializer.Serialize(results, JsonOut));
            Console.WriteLine($"report: {options.Report}");
        }
        return 0;
    }
}

internal sealed class Options
{
    public List<string> Positional { get; } = [];
    public List<string> Resolve { get; } = [];
    public string? Json { get; private set; }
    public string? Rules { get; private set; }
    public string? Backup { get; private set; }
    public string? Report { get; private set; }
    public bool All { get; private set; }

    public static Options Parse(string[] args)
    {
        var o = new Options();
        for (int i = 0; i < args.Length; i++)
        {
            string Next() => i + 1 < args.Length ? args[++i] : throw new ArgumentException($"{args[i]} needs a value");
            switch (args[i])
            {
                case "--json": o.Json = Next(); break;
                case "--rules": o.Rules = Next(); break;
                case "--backup": o.Backup = Next(); break;
                case "--report": o.Report = Next(); break;
                case "--resolve": o.Resolve.Add(Next()); break;
                case "--all": o.All = true; break;
                default: o.Positional.Add(args[i]); break;
            }
        }
        return o;
    }
}

internal static class AssemblyFiles
{
    public static IEnumerable<string> Enumerate(IEnumerable<string> inputs)
    {
        foreach (string input in inputs)
        {
            if (File.Exists(input))
            {
                yield return Path.GetFullPath(input);
                continue;
            }
            if (!Directory.Exists(input)) throw new FileNotFoundException($"not found: {input}");
            foreach (string file in Directory.EnumerateFiles(input, "*.dll", SearchOption.AllDirectories).Order(StringComparer.Ordinal))
            {
                // Editor-only assemblies never reach the player build.
                string[] parts = Path.GetRelativePath(input, file).Split(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                if (parts.Any(p => p.Equals("Editor", StringComparison.OrdinalIgnoreCase))) continue;
                yield return Path.GetFullPath(file);
            }
        }
    }

    public static bool IsExcluded(string file, RulesFile rules)
    {
        string name = Path.GetFileNameWithoutExtension(file);
        return rules.ExcludeAssemblies.Any(pattern => Regex.IsMatch(name, pattern, RegexOptions.IgnoreCase));
    }

    public static bool IsManaged(string file)
    {
        try
        {
            using ModuleDefinition module = ModuleDefinition.ReadModule(file, new ReaderParameters { ReadingMode = ReadingMode.Deferred });
            return true;
        }
        catch (BadImageFormatException)
        {
            return false;
        }
    }

    public static bool ReferencesShims(string file)
    {
        using ModuleDefinition module = ModuleDefinition.ReadModule(file, new ReaderParameters { ReadingMode = ReadingMode.Deferred });
        return module.AssemblyReferences.Any(r => r.Name == Catalog.ShimAssembly);
    }

    public static ModuleDefinition? TryRead(string file, IEnumerable<string> resolveDirs, bool writable)
    {
        var resolver = new DefaultAssemblyResolver();
        foreach (string dir in resolveDirs.Where(Directory.Exists).Distinct()) resolver.AddSearchDirectory(dir);
        resolver.AddSearchDirectory(Path.GetDirectoryName(file)!);
        try
        {
            return ModuleDefinition.ReadModule(file, new ReaderParameters
            {
                AssemblyResolver = resolver,
                ReadingMode = writable ? ReadingMode.Immediate : ReadingMode.Deferred,
                InMemory = true,
                ReadSymbols = false,
            });
        }
        catch (BadImageFormatException)
        {
            return null; // native DLL
        }
    }
}

// ---------------------------------------------------------------- scanning

internal sealed class Finding
{
    public required string Category { get; init; }
    public required Severity Severity { get; init; }
    public required string Api { get; init; }
    public required string Advice { get; init; }
    public List<string> Sites { get; } = [];
}

internal sealed class NativeImport
{
    public required string Library { get; init; }
    public required string EntryPoint { get; init; }
    public required string Method { get; init; }
    public required string Action { get; init; }
}

internal sealed class AssemblyReport
{
    public required string Name { get; init; }
    public required string Path { get; init; }
    public List<string> References { get; } = [];
    public List<Finding> Findings { get; } = [];
    public List<NativeImport> NativeImports { get; } = [];
    public List<string> ThreadBodiesWithLoops { get; } = [];
}

internal sealed class ScanResult
{
    public required List<AssemblyReport> Assemblies { get; init; }
    public required Dictionary<string, int> Totals { get; init; }

    public static ScanResult From(List<AssemblyReport> reports)
    {
        var totals = new Dictionary<string, int>
        {
            ["assemblies"] = reports.Count,
            ["blocker"] = reports.Sum(r => r.Findings.Where(f => f.Severity == Severity.Blocker).Sum(f => f.Sites.Count)),
            ["warn"] = reports.Sum(r => r.Findings.Where(f => f.Severity == Severity.Warn).Sum(f => f.Sites.Count)),
            ["patched"] = reports.Sum(r => r.Findings.Where(f => f.Severity == Severity.Patched).Sum(f => f.Sites.Count)),
            ["nativeImports"] = reports.Sum(r => r.NativeImports.Count),
            ["threadLoops"] = reports.Sum(r => r.ThreadBodiesWithLoops.Count),
        };
        return new ScanResult { Assemblies = reports, Totals = totals };
    }

    public void PrintSummary()
    {
        foreach (AssemblyReport a in Assemblies)
        {
            var notable = a.Findings.Where(f => f.Severity != Severity.Info).ToList();
            if (notable.Count == 0 && a.NativeImports.Count == 0 && a.ThreadBodiesWithLoops.Count == 0) continue;
            Console.WriteLine($"== {a.Name}");
            foreach (Finding f in notable.OrderByDescending(f => f.Severity))
                Console.WriteLine($"  [{f.Severity,-7}] {f.Api}  x{f.Sites.Count}  - {f.Advice}");
            foreach (var group in a.NativeImports.GroupBy(n => (n.Library, n.Action)))
                Console.WriteLine($"  [native ] {group.Key.Library}: {group.Count()} imports -> {group.Key.Action}");
            foreach (string body in a.ThreadBodiesWithLoops)
                Console.WriteLine($"  [loop   ] thread body {body} loops: if it never returns it freezes the tab; list it in rules.json \"skipThreadBodies\"");
        }
        Console.WriteLine($"totals: {string.Join(", ", Totals.Select(t => $"{t.Key}={t.Value}"))}");
    }
}

internal static class Scanner
{
    public static AssemblyReport ScanModule(ModuleDefinition module, string path, RulesFile rules)
    {
        var report = new AssemblyReport { Name = module.Assembly?.Name.Name ?? module.Name, Path = path };
        report.References.AddRange(module.AssemblyReferences.Select(r => r.Name).Order(StringComparer.Ordinal));
        var findings = new Dictionary<string, Finding>();

        foreach (TypeDefinition type in module.GetTypes())
        {
            foreach (MethodDefinition method in type.Methods)
            {
                if (method.IsPInvokeImpl && method.PInvokeInfo is { } info)
                {
                    report.NativeImports.Add(new NativeImport
                    {
                        Library = info.Module.Name,
                        EntryPoint = info.EntryPoint ?? method.Name,
                        Method = method.FullName,
                        Action = IlPatcher.NativeAction(info.Module.Name, rules),
                    });
                }
                if (!method.HasBody) continue;

                Instruction? previous = null;
                foreach (Instruction ins in method.Body.Instructions)
                {
                    if (ins.Operand is MethodReference callee)
                    {
                        Record(findings, callee, method, rules);
                        if (ins.OpCode.Code == Code.Newobj && previous is { OpCode.Code: Code.Ldftn or Code.Ldvirtftn }
                            && callee.DeclaringType.FullName is "System.Threading.ThreadStart" or "System.Threading.ParameterizedThreadStart"
                            && previous.Operand is MethodReference body && LoopDetector.HasLoop(body))
                        {
                            report.ThreadBodiesWithLoops.Add($"{body.DeclaringType.FullName.Replace('/', '+')}::{body.Name}");
                        }
                    }
                    previous = ins;
                }
            }
        }

        report.Findings.AddRange(findings.Values.OrderByDescending(f => f.Severity).ThenBy(f => f.Api, StringComparer.Ordinal));
        return report;
    }

    private static void Record(Dictionary<string, Finding> findings, MethodReference callee, MethodDefinition site, RulesFile rules)
    {
        string typeName = Signatures.TypeName(callee.DeclaringType);
        string api = $"{typeName}::{callee.Name}";
        Finding? finding;

        Redirect? redirect = Signatures.FindRedirect(callee);
        if (redirect is not null)
        {
            bool enabled = !rules.IsDisabled(redirect.Group);
            api = $"{api}({string.Join(", ", redirect.Params)})";
            if (!findings.TryGetValue(api, out finding))
            {
                finding = new Finding
                {
                    Category = redirect.Group,
                    Severity = enabled ? Severity.Patched : Severity.Warn,
                    Api = api,
                    Advice = enabled ? $"redirected to {redirect.ShimType}.{redirect.ShimMember}" : $"redirect group '{redirect.Group}' disabled in rules",
                };
                findings[api] = finding;
            }
        }
        else
        {
            RiskRule? rule = Catalog.Risks.FirstOrDefault(r => r.Matches(typeName, callee.Name));
            if (rule is null) return;
            if (!findings.TryGetValue(api, out finding))
            {
                finding = new Finding { Category = rule.Category, Severity = rule.Severity, Api = api, Advice = rule.Advice };
                findings[api] = finding;
            }
        }
        if (finding.Sites.Count < 50) finding.Sites.Add(site.FullName);
    }
}

internal static class LoopDetector
{
    public static bool HasLoop(MethodReference reference)
    {
        MethodDefinition? method;
        try { method = reference.Resolve(); }
        catch (AssemblyResolutionException) { return false; }
        if (method is not { HasBody: true }) return false;
        foreach (Instruction ins in method.Body.Instructions)
        {
            if (ins.Operand is Instruction target && target.Offset <= ins.Offset) return true;
            if (ins.Operand is Instruction[] targets && targets.Any(t => t.Offset <= ins.Offset)) return true;
        }
        return false;
    }
}

internal static class Signatures
{
    /// <summary>Type name without generic instantiation arguments ("List`1", not "List`1&lt;Int32&gt;").</summary>
    public static string TypeName(TypeReference type)
    {
        TypeReference element = type is GenericInstanceType generic ? generic.ElementType : type;
        return element.FullName;
    }

    public static Redirect? FindRedirect(MethodReference callee)
    {
        string typeName = TypeName(callee.DeclaringType);
        foreach (Redirect r in Catalog.Redirects)
        {
            if (r.TypeName != typeName || r.Member != callee.Name || r.Params.Length != callee.Parameters.Count) continue;
            bool same = true;
            for (int i = 0; i < r.Params.Length && same; i++) same = callee.Parameters[i].ParameterType.FullName == r.Params[i];
            if (same) return r;
        }
        return null;
    }

    /// <summary>Index of the URL parameter (the first string), or -1 when the call is not a URL sink.</summary>
    public static int UrlParameterIndex(MethodReference callee)
    {
        string typeName = TypeName(callee.DeclaringType);
        bool listed = Catalog.UrlFilters.Any(f => f.TypeName == typeName
            && (f.Member == callee.Name || (f.Member == "*" && Catalog.UrlMembers.Contains(callee.Name))));
        if (!listed) return -1;
        for (int i = 0; i < callee.Parameters.Count; i++)
        {
            if (callee.Parameters[i].ParameterType.FullName == "System.String") return i;
        }
        return -1;
    }
}

// ---------------------------------------------------------------- patching

internal sealed class PatchReport
{
    public string Assembly { get; set; } = "";
    public string Output { get; set; } = "";
    public string? RenamedFrom { get; set; }
    public string? RenamedTo { get; set; }
    public List<string> RenamedReferences { get; } = [];
    public int RenamedStrings { get; set; }
    public Dictionary<string, int> Redirected { get; } = [];
    public int UrlFilters { get; set; }
    public List<string> StubbedNative { get; } = [];
    public List<string> RelinkedNative { get; } = [];

    public int Total => Redirected.Values.Sum() + UrlFilters + StubbedNative.Count + RelinkedNative.Count
        + RenamedReferences.Count + RenamedStrings + (RenamedTo is null ? 0 : 1);

    public string Describe()
    {
        var parts = Redirected.Select(r => $"{r.Key}={r.Value}").ToList();
        if (RenamedTo is not null) parts.Insert(0, $"renamed->{RenamedTo}");
        if (RenamedReferences.Count > 0) parts.Add($"refs-renamed={RenamedReferences.Count}");
        if (RenamedStrings > 0) parts.Add($"strings-renamed={RenamedStrings}");
        if (UrlFilters > 0) parts.Add($"urls={UrlFilters}");
        if (StubbedNative.Count > 0) parts.Add($"native-stubs={StubbedNative.Count}");
        if (RelinkedNative.Count > 0) parts.Add($"native-relinked={RelinkedNative.Count}");
        return string.Join(" ", parts);
    }
}

/// <summary>
/// Unity reserves Assembly-CSharp* for its own script compilation, so the game's precompiled copies are
/// renamed (identity, file name, references from other game assemblies). Scene references survive because a
/// MonoScript inside a DLL is addressed by the DLL's .meta GUID plus a hash of namespace and class name.
/// </summary>
internal static class Renamer
{
    public static void Apply(ModuleDefinition module, Dictionary<string, string> renames, PatchReport report)
    {
        string name = module.Assembly.Name.Name;
        if (renames.TryGetValue(name, out string? newName))
        {
            module.Assembly.Name.Name = newName;
            module.Name = newName + ".dll";
            report.RenamedFrom = name;
            report.RenamedTo = newName;
        }
        foreach (AssemblyNameReference reference in module.AssemblyReferences)
        {
            if (renames.TryGetValue(reference.Name, out string? referenced))
            {
                report.RenamedReferences.Add($"{reference.Name}->{referenced}");
                reference.Name = referenced;
            }
        }
        if (renames.Count > 0) RenameStrings(module, renames, report);
    }

    /// <summary>
    /// Reflection by name ("Foo, Assembly-CSharp", Assembly.Load("Assembly-CSharp")) must follow the rename too.
    /// Only whole names are touched: an exact literal or an assembly-qualified suffix after a comma.
    /// </summary>
    private static void RenameStrings(ModuleDefinition module, Dictionary<string, string> renames, PatchReport report)
    {
        string alternation = string.Join("|", renames.Keys.OrderByDescending(k => k.Length).Select(Regex.Escape));
        var qualified = new Regex($@"(,\s*)({alternation})(?=$|[,\]\s])");
        foreach (TypeDefinition type in module.GetTypes())
        {
            foreach (MethodDefinition method in type.Methods.Where(m => m.HasBody))
            {
                foreach (Instruction ins in method.Body.Instructions)
                {
                    if (ins.OpCode.Code != Code.Ldstr || ins.Operand is not string text) continue;
                    string updated = renames.TryGetValue(text, out string? exact)
                        ? exact
                        : qualified.Replace(text, m => m.Groups[1].Value + renames[m.Groups[2].Value]);
                    if (updated == text) continue;
                    ins.Operand = updated;
                    report.RenamedStrings++;
                }
            }
        }
    }

    /// <summary>Moves the .meta (and its GUID) to the new file name and removes the old DLL.</summary>
    public static void MoveMeta(string oldPath, string newPath)
    {
        string oldMeta = oldPath + ".meta", newMeta = newPath + ".meta";
        if (File.Exists(oldMeta) && !File.Exists(newMeta)) File.Move(oldMeta, newMeta);
        else if (File.Exists(oldMeta)) File.Delete(oldMeta);
        if (File.Exists(oldPath)) File.Delete(oldPath);
    }
}

internal sealed class IlPatcher(ModuleDefinition module, RulesFile rules)
{
    private readonly PatchReport report = new();
    private AssemblyNameReference? shimAssembly;

    public static string NativeAction(string library, RulesFile rules)
    {
        if (rules.AllowNative.Contains(library, StringComparer.OrdinalIgnoreCase)) return "kept";
        if (rules.RelinkInternal.Any(l => SameLibrary(l, library))) return "relink-internal";
        return rules.IsDisabled("pinvoke") ? "kept (pinvoke stubbing disabled)" : "stub";
    }

    private static bool SameLibrary(string a, string b)
    {
        static string Norm(string s)
        {
            s = Path.GetFileName(s).ToLowerInvariant();
            foreach (string ext in new[] { ".dll", ".so", ".dylib", ".bundle" })
                if (s.EndsWith(ext)) s = s[..^ext.Length];
            return s.StartsWith("lib") ? s[3..] : s;
        }
        return Norm(a) == Norm(b);
    }

    public PatchReport Run()
    {
        foreach (TypeDefinition type in module.GetTypes())
        {
            foreach (MethodDefinition method in type.Methods)
            {
                if (method.IsPInvokeImpl) PatchNative(method);
                else if (method.HasBody) PatchCallSites(method);
            }
        }
        return report;
    }

    private TypeReference ShimType(string name)
    {
        shimAssembly ??= module.AssemblyReferences.FirstOrDefault(a => a.Name == Catalog.ShimAssembly);
        if (shimAssembly is null)
        {
            shimAssembly = new AssemblyNameReference(Catalog.ShimAssembly, new Version(1, 0, 0, 0));
            module.AssemblyReferences.Add(shimAssembly);
        }
        return new TypeReference(Catalog.ShimNamespace, name, module, shimAssembly);
    }

    /// <summary>
    /// Builds the shim signature from the original call's own type references, so no type from the
    /// patcher's runtime ever leaks into the game module.
    /// </summary>
    private MethodReference ShimMethod(Redirect redirect, MethodReference original)
    {
        bool isCtor = original.Name == ".ctor";
        TypeReference returnType = isCtor ? original.DeclaringType : original.ReturnType;
        var shim = new MethodReference(redirect.ShimMember, returnType, ShimType(redirect.ShimType)) { HasThis = false };
        if (!isCtor && original.HasThis) shim.Parameters.Add(new ParameterDefinition(original.DeclaringType));
        foreach (ParameterDefinition p in original.Parameters) shim.Parameters.Add(new ParameterDefinition(p.ParameterType));
        return shim;
    }

    private MethodReference ToUrlMethod()
    {
        var method = new MethodReference("ToUrl", module.TypeSystem.String, ShimType("PathShim")) { HasThis = false };
        method.Parameters.Add(new ParameterDefinition(module.TypeSystem.String));
        return method;
    }

    private void PatchCallSites(MethodDefinition method)
    {
        var instructions = method.Body.Instructions;
        bool simplified = false;
        for (int i = 0; i < instructions.Count; i++)
        {
            Instruction ins = instructions[i];
            if (ins.Operand is not MethodReference callee) continue;
            if (ins.OpCode.Code is not (Code.Call or Code.Callvirt or Code.Newobj)) continue;

            Redirect? redirect = Signatures.FindRedirect(callee);
            if (redirect is not null && !rules.IsDisabled(redirect.Group))
            {
                // Rewrite in place: branch targets and exception-handler bounds keep pointing at this instruction.
                if (i > 0 && instructions[i - 1].OpCode.Code == Code.Constrained) continue;
                ins.OpCode = OpCodes.Call;
                ins.Operand = ShimMethod(redirect, callee);
                report.Redirected[redirect.Group] = report.Redirected.GetValueOrDefault(redirect.Group) + 1;
                continue;
            }

            int urlIndex = rules.IsDisabled("urls") || rules.IsDisabled("paths") ? -1 : Signatures.UrlParameterIndex(callee);
            if (urlIndex >= 0 && CanStash(callee, urlIndex) && (i == 0 || instructions[i - 1].OpCode.OpCodeType != OpCodeType.Prefix))
            {
                if (!simplified) { method.Body.SimplifyMacros(); simplified = true; }
                i = InsertUrlFilter(method, ins, callee, urlIndex);
                report.UrlFilters++;
            }
        }
        if (simplified) method.Body.OptimizeMacros();
    }

    /// <summary>Arguments after the URL are parked in locals; they must be storable.</summary>
    private static bool CanStash(MethodReference callee, int urlIndex)
    {
        for (int p = urlIndex + 1; p < callee.Parameters.Count; p++)
        {
            TypeReference type = callee.Parameters[p].ParameterType;
            if (type.IsByReference || type.ContainsGenericParameter || type.IsPointer) return false;
        }
        return true;
    }

    /// <summary>
    /// Rewrites "args...; call M" into "args...; stloc tail; call ToUrl; ldloc tail; call M". The original
    /// instruction object becomes the first new instruction, so branch targets and handler bounds stay valid.
    /// Returns the index of the re-emitted call.
    /// </summary>
    private int InsertUrlFilter(MethodDefinition method, Instruction ins, MethodReference callee, int urlIndex)
    {
        var body = method.Body;
        var tail = new List<VariableDefinition>();
        for (int p = urlIndex + 1; p < callee.Parameters.Count; p++)
        {
            var local = new VariableDefinition(callee.Parameters[p].ParameterType);
            body.Variables.Add(local);
            tail.Add(local);
        }

        var sequence = new List<Instruction>();
        for (int t = tail.Count - 1; t >= 0; t--) sequence.Add(Instruction.Create(OpCodes.Stloc, tail[t]));
        sequence.Add(Instruction.Create(OpCodes.Call, ToUrlMethod()));
        foreach (VariableDefinition local in tail) sequence.Add(Instruction.Create(OpCodes.Ldloc, local));
        sequence.Add(Instruction.Create(ins.OpCode, callee));

        ins.OpCode = sequence[0].OpCode;
        ins.Operand = sequence[0].Operand;
        Instruction previous = ins;
        ILProcessor il = body.GetILProcessor();
        for (int s = 1; s < sequence.Count; s++)
        {
            il.InsertAfter(previous, sequence[s]);
            previous = sequence[s];
        }
        return body.Instructions.IndexOf(previous);
    }

    private void PatchNative(MethodDefinition method)
    {
        PInvokeInfo info = method.PInvokeInfo;
        string library = info.Module.Name;
        string entry = info.EntryPoint ?? method.Name;
        string action = NativeAction(library, rules);

        if (action == "relink-internal")
        {
            ModuleReference internalRef = module.ModuleReferences.FirstOrDefault(m => m.Name == "__Internal")
                ?? AddModuleReference("__Internal");
            info.Module = internalRef;
            report.RelinkedNative.Add($"{library}!{entry}");
            return;
        }
        if (action != "stub") return;

        // Order matters: Cecil's PInvokeInfo setter turns IsPInvokeImpl back on.
        method.PInvokeInfo = null;
        method.IsPInvokeImpl = false;
        method.ImplAttributes = MethodImplAttributes.IL | MethodImplAttributes.Managed;
        method.MethodReturnType.MarshalInfo = null;
        foreach (ParameterDefinition p in method.Parameters) p.MarshalInfo = null;

        var body = method.Body;
        body.Instructions.Clear();
        body.Variables.Clear();
        body.InitLocals = true;
        var il = body.GetILProcessor();

        var stub = new MethodReference("Stub", module.TypeSystem.Void, ShimType("U2W")) { HasThis = false };
        stub.Parameters.Add(new ParameterDefinition(module.TypeSystem.String));
        il.Emit(OpCodes.Ldstr, $"{library}!{entry}");
        il.Emit(OpCodes.Call, stub);

        // out parameters must be definitely assigned.
        foreach (ParameterDefinition p in method.Parameters)
        {
            if (p.IsOut && p.ParameterType is ByReferenceType byRef)
            {
                il.Emit(OpCodes.Ldarg, p);
                il.Emit(OpCodes.Initobj, byRef.ElementType);
            }
        }

        TypeReference ret = method.ReturnType;
        if (ret.MetadataType == MetadataType.Void)
        {
            il.Emit(OpCodes.Ret);
        }
        else if (rules.StubReturns.TryGetValue(entry, out long constant) && IsIntegral(ret))
        {
            if (ret.MetadataType is MetadataType.Int64 or MetadataType.UInt64) il.Emit(OpCodes.Ldc_I8, constant);
            else il.Emit(OpCodes.Ldc_I4, (int)constant);
            il.Emit(OpCodes.Ret);
        }
        else if (ret.IsPointer || ret.IsFunctionPointer)
        {
            il.Emit(OpCodes.Ldc_I4_0);
            il.Emit(OpCodes.Conv_U);
            il.Emit(OpCodes.Ret);
        }
        else
        {
            var local = new VariableDefinition(ret);
            body.Variables.Add(local);
            il.Emit(OpCodes.Ldloca, local);
            il.Emit(OpCodes.Initobj, ret);
            il.Emit(OpCodes.Ldloc, local);
            il.Emit(OpCodes.Ret);
        }
        body.OptimizeMacros();
        report.StubbedNative.Add($"{library}!{entry}");
    }

    private ModuleReference AddModuleReference(string name)
    {
        var reference = new ModuleReference(name);
        module.ModuleReferences.Add(reference);
        return reference;
    }

    private static bool IsIntegral(TypeReference type)
    {
        if (type.MetadataType is MetadataType.Boolean or MetadataType.Byte or MetadataType.SByte or MetadataType.Int16
            or MetadataType.UInt16 or MetadataType.Int32 or MetadataType.UInt32 or MetadataType.Int64 or MetadataType.UInt64)
            return true;
        // enums: resolve to check the underlying type is integral (always true for C# enums).
        try { return type.Resolve()?.IsEnum ?? false; }
        catch (AssemblyResolutionException) { return false; }
    }
}
