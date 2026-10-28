using System.Text.Json;
using System.Text.Json.Serialization;

namespace Unity2Web.Patcher;

/// <summary>What the browser does with a given API, as reported by the scanner.</summary>
internal enum Severity
{
    Info,
    Patched,
    Warn,
    Blocker,
}

/// <summary>A risky API the scanner reports. TypeName may end in '*' to match a namespace prefix.</summary>
internal sealed record RiskRule(string Category, Severity Severity, string TypeName, string Member, string Advice)
{
    public bool Matches(string typeName, string member)
    {
        bool typeMatches = TypeName.EndsWith('*')
            ? typeName.StartsWith(TypeName[..^1], StringComparison.Ordinal)
            : typeName == TypeName;
        return typeMatches && (Member == "*" || Member == member);
    }
}

/// <summary>
/// A call-site rewrite: a reference to TypeName::Member(Params) becomes a static call into the shim assembly.
/// Instance methods gain the receiver as first parameter, constructors become factories returning the type.
/// </summary>
internal sealed record Redirect(string Group, string TypeName, string Member, string[] Params, string ShimType, string ShimMember);

/// <summary>
/// Calls whose first string argument is a URL get it filtered through PathShim.ToUrl, so packed
/// StreamingAssets paths turn back into HTTP URLs. Member "*" matches every URL-taking member listed in UrlMembers.
/// </summary>
internal sealed record UrlFilter(string TypeName, string Member);

internal static class Catalog
{
    public const string ShimAssembly = "Unity2Web.Shims";
    public const string ShimNamespace = "Unity2Web.Shims";

    public static readonly Redirect[] Redirects =
    [
        new("threads", "System.Threading.Thread", ".ctor", ["System.Threading.ThreadStart"], "ThreadShim", "Create"),
        new("threads", "System.Threading.Thread", ".ctor", ["System.Threading.ThreadStart", "System.Int32"], "ThreadShim", "Create"),
        new("threads", "System.Threading.Thread", ".ctor", ["System.Threading.ParameterizedThreadStart"], "ThreadShim", "Create"),
        new("threads", "System.Threading.Thread", ".ctor", ["System.Threading.ParameterizedThreadStart", "System.Int32"], "ThreadShim", "Create"),
        new("threads", "System.Threading.Thread", "Start", [], "ThreadShim", "Start"),
        new("threads", "System.Threading.Thread", "Start", ["System.Object"], "ThreadShim", "Start"),
        new("threads", "System.Threading.Thread", "Join", [], "ThreadShim", "Join"),
        new("threads", "System.Threading.Thread", "Join", ["System.Int32"], "ThreadShim", "Join"),
        new("threads", "System.Threading.Thread", "Join", ["System.TimeSpan"], "ThreadShim", "Join"),
        new("threads", "System.Threading.Thread", "get_IsAlive", [], "ThreadShim", "IsAlive"),
        new("threads", "System.Threading.Thread", "Abort", [], "ThreadShim", "Abort"),
        new("threads", "System.Threading.Thread", "Sleep", ["System.Int32"], "ThreadShim", "Sleep"),
        new("threads", "System.Threading.Thread", "Sleep", ["System.TimeSpan"], "ThreadShim", "Sleep"),
        new("threads", "System.Threading.ThreadPool", "QueueUserWorkItem", ["System.Threading.WaitCallback"], "ThreadShim", "QueueUserWorkItem"),
        new("threads", "System.Threading.ThreadPool", "QueueUserWorkItem", ["System.Threading.WaitCallback", "System.Object"], "ThreadShim", "QueueUserWorkItem"),
        new("process", "System.Diagnostics.Process", "Start", ["System.String"], "ProcessShim", "Start"),
        new("process", "System.Diagnostics.Process", "Start", ["System.String", "System.String"], "ProcessShim", "Start"),
        new("process", "System.Diagnostics.Process", "Start", ["System.Diagnostics.ProcessStartInfo"], "ProcessShim", "Start"),
        new("process", "System.Diagnostics.Process", "Start", [], "ProcessShim", "StartInstance"),
        new("folders", "System.Environment", "GetFolderPath", ["System.Environment/SpecialFolder"], "EnvShim", "GetFolderPath"),
        new("paths", "UnityEngine.Application", "get_streamingAssetsPath", [], "PathShim", "GetStreamingAssetsPath"),
        new("paths", "UnityEngine.Application", "get_dataPath", [], "PathShim", "GetDataPath"),
    ];

    public static readonly UrlFilter[] UrlFilters =
    [
        new("UnityEngine.Networking.UnityWebRequest", "*"),
        new("UnityEngine.Networking.UnityWebRequestAssetBundle", "*"),
        new("UnityEngine.Networking.UnityWebRequestTexture", "*"),
        new("UnityEngine.Networking.UnityWebRequestMultimedia", "*"),
        new("UnityEngine.WWW", "*"),
        new("UnityEngine.Video.VideoPlayer", "set_url"),
        new("System.Uri", ".ctor"),
    ];

    /// <summary>Members whose first string parameter is the URL (all overloads).</summary>
    public static readonly string[] UrlMembers =
        [".ctor", "Get", "Post", "Put", "Head", "Delete", "GetAssetBundle", "GetTexture", "GetAudioClip",
         "GetMovieTexture", "LoadFromCacheOrDownload", "set_url"];

    public static readonly RiskRule[] Risks =
    [
        new("threads", Severity.Info, "System.Threading.Thread", "*", "Thread API other than create/start/join/sleep; usually harmless on one thread."),
        new("threads", Severity.Warn, "System.Threading.ThreadPool", "*", "Thread pool is unavailable in single-threaded WebGL."),
        new("threads", Severity.Warn, "System.Threading.Tasks.Task", "Run", "Task.Run needs a thread pool; work may never complete on WebGL. Await on the main thread instead."),
        new("threads", Severity.Warn, "System.Threading.Tasks.TaskFactory", "StartNew", "Needs a thread pool; may never complete on WebGL."),
        new("threads", Severity.Warn, "System.Threading.Tasks.Parallel", "*", "Parallel loops run without threads on WebGL (or not at all)."),
        new("threads", Severity.Warn, "System.Threading.Timer", ".ctor", "System.Threading.Timer callbacks need a timer thread."),
        new("threads", Severity.Warn, "System.Timers.Timer", ".ctor", "System.Timers.Timer callbacks need a timer thread."),
        new("blocking", Severity.Warn, "System.Threading.Monitor", "Wait", "Blocking the browser main thread deadlocks the page."),
        new("blocking", Severity.Warn, "System.Threading.WaitHandle", "WaitOne", "Blocking waits deadlock on the browser main thread."),
        new("blocking", Severity.Warn, "System.Threading.WaitHandle", "WaitAll", "Blocking waits deadlock on the browser main thread."),
        new("blocking", Severity.Warn, "System.Threading.ManualResetEventSlim", "Wait", "Blocking waits deadlock on the browser main thread."),
        new("blocking", Severity.Warn, "System.Threading.SemaphoreSlim", "Wait", "Blocking waits deadlock on the browser main thread."),
        new("blocking", Severity.Warn, "System.Threading.Tasks.Task", "Wait", "Task.Wait deadlocks on the browser main thread."),
        new("blocking", Severity.Warn, "System.Threading.Tasks.Task`1", "get_Result", "Task.Result blocks; deadlocks on the browser main thread."),
        new("network", Severity.Blocker, "System.Net.Sockets.*", "*", "Browsers have no raw TCP/UDP sockets: needs a WebSocket/WebRTC transport plus a relay server."),
        new("network", Severity.Blocker, "System.Net.Dns", "*", "No DNS API in the browser."),
        new("network", Severity.Warn, "System.Net.WebClient", "*", "System.Net HTTP is not available in WebGL; use UnityWebRequest (subject to CORS)."),
        new("network", Severity.Warn, "System.Net.HttpWebRequest", "*", "System.Net HTTP is not available in WebGL; use UnityWebRequest (subject to CORS)."),
        new("network", Severity.Warn, "System.Net.WebRequest", "*", "System.Net HTTP is not available in WebGL; use UnityWebRequest (subject to CORS)."),
        new("network", Severity.Warn, "System.Net.Http.HttpClient", "*", "HttpClient is not available in WebGL; use UnityWebRequest (subject to CORS)."),
        new("network", Severity.Info, "UnityEngine.Networking.UnityWebRequest", "*", "Works in the browser; cross-origin servers must send CORS headers."),
        new("process", Severity.Warn, "System.Diagnostics.Process", "*", "No processes in the browser."),
        new("process", Severity.Info, "System.Environment", "Exit", "Exiting is a no-op in the browser; the page stays open."),
        new("platform", Severity.Info, "UnityEngine.Application", "Quit", "Application.Quit does nothing in the browser."),
        new("platform", Severity.Blocker, "UnityEngine.Microphone", "*", "UnityEngine.Microphone is not supported on WebGL."),
        new("platform", Severity.Warn, "UnityEngine.WebCamTexture", "*", "Webcam needs a user permission prompt and HTTPS."),
        new("platform", Severity.Blocker, "UnityEngine.Windows.*", "*", "Windows-only Unity APIs."),
        new("platform", Severity.Warn, "Microsoft.Win32.Registry*", "*", "No registry in the browser; use PlayerPrefs."),
        new("platform", Severity.Blocker, "System.IO.Ports.*", "*", "No serial ports in the browser."),
        new("platform", Severity.Warn, "System.IO.FileSystemWatcher", "*", "File watching is unsupported."),
        new("assets", Severity.Warn, "UnityEngine.AssetBundle", "LoadFromFile", "Works only for files present in the in-memory file system (packed StreamingAssets or downloaded files)."),
        new("assets", Severity.Warn, "UnityEngine.AssetBundle", "LoadFromFileAsync", "Works only for files present in the in-memory file system."),
        new("rendering", Severity.Blocker, "UnityEngine.ComputeShader", "Dispatch", "Compute shaders need WebGPU (Unity 6+) - not available on WebGL 2."),
        new("rendering", Severity.Warn, "UnityEngine.Rendering.AsyncGPUReadback", "*", "Async GPU readback is unsupported on WebGL 2."),
        new("video", Severity.Warn, "UnityEngine.Video.VideoPlayer", "set_clip", "WebGL VideoPlayer plays from a URL source; VideoClip assets need converting to StreamingAssets URLs."),
        new("aot", Severity.Blocker, "System.Reflection.Emit.*", "*", "IL2CPP is ahead-of-time: no runtime code generation."),
        new("aot", Severity.Blocker, "Microsoft.CSharp.RuntimeBinder.*", "*", "'dynamic' is not supported by IL2CPP."),
        new("aot", Severity.Warn, "System.Linq.Expressions.Expression`1", "Compile", "Compiled expressions fall back to the interpreter under IL2CPP (slow, partial)."),
        new("aot", Severity.Blocker, "System.AppDomain", "CreateDomain", "No AppDomains under IL2CPP."),
        new("aot", Severity.Blocker, "System.Reflection.Assembly", "LoadFrom", "No runtime assembly loading under IL2CPP (mod loaders break)."),
        new("aot", Severity.Blocker, "System.Reflection.Assembly", "LoadFile", "No runtime assembly loading under IL2CPP (mod loaders break)."),
        new("aot", Severity.Blocker, "System.Reflection.Assembly", "Load", "No runtime assembly loading under IL2CPP (only already-built assemblies resolve)."),
        new("input", Severity.Info, "UnityEngine.Cursor", "set_lockState", "Pointer lock is granted only after a click in the page."),
        new("display", Severity.Info, "UnityEngine.Screen", "SetResolution", "The canvas size follows the page; fullscreen needs a user gesture."),
    ];
}

/// <summary>User-tunable rules, loaded from rules.json.</summary>
internal sealed class RulesFile
{
    /// <summary>Native libraries kept as P/Invoke. "__Internal" means a .jslib or a statically linked web library.</summary>
    [JsonPropertyName("allowNative")]
    public List<string> AllowNative { get; set; } = ["__Internal"];

    /// <summary>Native libraries re-pointed to "__Internal" because you provide a WebAssembly build (FMOD WebGL, ...).</summary>
    [JsonPropertyName("relinkInternal")]
    public List<string> RelinkInternal { get; set; } = [];

    /// <summary>Constant return values for stubbed entry points, e.g. { "SteamAPI_RestartAppIfNecessary": 0 }.</summary>
    [JsonPropertyName("stubReturns")]
    public Dictionary<string, long> StubReturns { get; set; } = [];

    /// <summary>
    /// Game assemblies renamed because Unity reserves their names for its own script compilation.
    /// Set to {} if your AssetRipper export already renamed them.
    /// </summary>
    [JsonPropertyName("renameAssemblies")]
    public Dictionary<string, string> RenameAssemblies { get; set; } = new(StringComparer.Ordinal)
    {
        ["Assembly-CSharp"] = "Game-CSharp",
        ["Assembly-CSharp-firstpass"] = "Game-CSharp-firstpass",
        ["Assembly-UnityScript"] = "Game-UnityScript",
        ["Assembly-UnityScript-firstpass"] = "Game-UnityScript-firstpass",
        ["Assembly-Boo"] = "Game-Boo",
        ["Assembly-Boo-firstpass"] = "Game-Boo-firstpass",
    };

    /// <summary>Redirect groups to skip: threads, process, folders, paths, urls, pinvoke.</summary>
    [JsonPropertyName("disable")]
    public List<string> Disable { get; set; } = [];

    /// <summary>Assembly names (regex) never patched. The defaults cover the BCL and Unity's own assemblies.</summary>
    [JsonPropertyName("excludeAssemblies")]
    public List<string> ExcludeAssemblies { get; set; } =
        [@"^(mscorlib|netstandard|System(\..+)?|Mono\..+|Microsoft\..+|UnityEngine(\..+)?|UnityEditor(\..+)?|Unity\..+|Unity2Web\..+|nunit\..+)$"];

    public static RulesFile Load(string? path)
    {
        if (string.IsNullOrEmpty(path)) return new RulesFile();
        var options = new JsonSerializerOptions { ReadCommentHandling = JsonCommentHandling.Skip, AllowTrailingCommas = true };
        return JsonSerializer.Deserialize<RulesFile>(File.ReadAllText(path), options) ?? new RulesFile();
    }

    public bool IsDisabled(string group) => Disable.Contains(group, StringComparer.OrdinalIgnoreCase);
}
