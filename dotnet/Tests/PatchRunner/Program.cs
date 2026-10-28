using System.Reflection;

// usage: PatchRunner <dir containing Assembly-CSharp.dll [+ Unity2Web.Shims.dll]> <original|patched>
if (args.Length != 2) { Console.Error.WriteLine("usage: PatchRunner <dir> <original|patched>"); return 2; }

string dir = Path.GetFullPath(args[0]);
bool patched = args[1] == "patched";
// The patcher renames Assembly-CSharp (reserved by Unity) to Game-CSharp.
Assembly game = Assembly.LoadFrom(Path.Combine(dir, patched ? "Game-CSharp.dll" : "Assembly-CSharp.dll"));
Type probe = game.GetType("FakeGame.Probe", throwOnError: true)!;
int failures = 0;

object? Call(string name) => probe.GetMethod(name)!.Invoke(null, null);

void Check(string name, Func<object?> run, Func<object?, bool> expect, Type? expectedException = null)
{
    try
    {
        object? value = run();
        bool ok = expectedException is null && expect(value);
        Console.WriteLine($"{(ok ? "PASS" : "FAIL")} {name} -> {value}");
        if (!ok) failures++;
    }
    catch (TargetInvocationException e) when (expectedException is not null && expectedException.IsInstanceOfType(e.InnerException))
    {
        Console.WriteLine($"PASS {name} -> threw {e.InnerException!.GetType().Name} (expected)");
    }
    catch (Exception e)
    {
        Exception inner = e is TargetInvocationException t ? t.InnerException ?? e : e;
        Console.WriteLine($"FAIL {name} -> threw {inner.GetType().Name}: {inner.Message}");
        failures++;
    }
}

if (!patched)
{
    Check("native import fails unpatched", () => Call("Native"), _ => false, typeof(DllNotFoundException));
    Check("streamingAssetsPath unpatched", () => Call("Streaming"), v => (string?)v == "C:/Game/Game_Data/StreamingAssets");
    return failures;
}

Assembly shims = Assembly.LoadFrom(Path.Combine(dir, "Unity2Web.Shims.dll"));
Type Shim(string name) => shims.GetType($"Unity2Web.Shims.{name}", throwOnError: true)!;
void SetField(string type, string field, object? value) => Shim(type).GetField(field)!.SetValue(null, value);

string persistent = Path.Combine(Path.GetTempPath(), "u2w-runner-" + Environment.ProcessId);
string? openedUrl = null;
var log = new List<string>();
SetField("U2W", "IsWeb", true);
SetField("U2W", "LogSink", (Action<string>)log.Add);
SetField("EnvShim", "PersistentDataPath", persistent);
SetField("ProcessShim", "OpenUrl", (Action<string>)(url => openedUrl = url));
SetField("PathShim", "StreamingAssetsUrl", "http://host/game/StreamingAssets");
SetField("PathShim", "StreamingAssetsPacked", true);

Check("native imports stubbed to defaults", () => Call("Native"),
    v => (string?)v == "False|0|0|00000000-0000-0000-0000-000000000000|1");
Check("thread body runs on main thread at Join", () => Call("Threads"), v => (string?)v == "main");
Check("Thread.Sleep is a no-op", () => Call("Sleep"), v => (string?)v == "skipped");
Check("ThreadPool work is deferred", () => { Call("Pool"); return game.GetType("FakeGame.Probe")!.GetField("PoolResult")!.GetValue(null); },
    v => (string?)v == "none");
Check("deferred work runs on Pump", () =>
{
    Shim("MainThread").GetMethod("Pump")!.Invoke(null, null);
    return probe.GetField("PoolResult")!.GetValue(null);
}, v => (string?)v == "ran");
Check("Process.Start(url) opens a tab", () => { Call("OpenUrl"); return openedUrl; }, v => (string?)v == "https://example.com/");
Check("special folders go to persistentDataPath", () => Call("Folder"), v => ((string?)v ?? "").StartsWith(persistent));
Check("dataPath redirected", () => Call("DataPath"), v => (string?)v == "/u2wdata");
Check("streamingAssetsPath redirected", () => Call("Streaming"), v => (string?)v == "/u2wdata/StreamingAssets");
Check("web requests get the URL back", () => Call("Url"), v => (string?)v == "http://host/game/StreamingAssets/a.json");
Check("URL filtered when more arguments follow", () => Call("UrlWithMoreArguments"), v => (string?)v == "http://host/game/StreamingAssets/b.json|payload");
Check("Type.GetType by old assembly name still resolves", () => Call("TypeByName"), v => (bool)v! == true);
Check("Join after the thread finished", () => Call("Joined"), v => (string?)v == "done");
Check("assembly renamed", () => game.GetName().Name, v => (string?)v == "Game-CSharp");
Check("original file removed", () => File.Exists(Path.Combine(dir, "Assembly-CSharp.dll")), v => (bool)v! == false);
Check("stub calls were logged", () => string.Join(";", log), v => ((string)v!).Contains("steam_api64!SteamAPI_Init"));

try { Directory.Delete(persistent, recursive: true); } catch (IOException) { }
Console.WriteLine(failures == 0 ? "ALL PASS" : $"{failures} FAILED");
return failures;
