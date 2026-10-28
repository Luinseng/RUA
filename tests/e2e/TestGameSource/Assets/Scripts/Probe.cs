using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using UnityEngine;

/// <summary>
/// A tiny "desktop game" written the way PC games are: native Steam import, a worker thread,
/// synchronous StreamingAssets reads, saves under My Documents. The harness must make all of it
/// work in the browser without changing this source (it only ever sees the compiled DLL).
/// </summary>
public class Probe : MonoBehaviour
{
    [DllImport("steam_api64")]
    private static extern bool SteamAPI_Init();

    public Spinner spinner;

    private readonly StringBuilder report = new StringBuilder();

    private void Start()
    {
        Add("unity", Application.unityVersion + " / " + Application.platform);

        try { Add("steam", "SteamAPI_Init=" + SteamAPI_Init()); }
        catch (Exception e) { Add("steam", "threw " + e.GetType().Name); }

        try
        {
            int main = Thread.CurrentThread.ManagedThreadId;
            int ranOn = -1;
            var worker = new Thread(() => { ranOn = Thread.CurrentThread.ManagedThreadId; });
            worker.Start();
            worker.Join();
            Add("thread", ranOn == main ? "body ran on main thread" : "body ran on thread " + ranOn);
        }
        catch (Exception e) { Add("thread", "threw " + e.GetType().Name); }

        try { Add("streaming", File.ReadAllText(Path.Combine(Application.streamingAssetsPath, "hello.txt")).Trim()); }
        catch (Exception e) { Add("streaming", "threw " + e.GetType().Name + ": " + e.Message); }

        try
        {
            string save = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "probe-save.txt");
            int run = File.Exists(save) ? int.Parse(File.ReadAllText(save)) + 1 : 1;
            File.WriteAllText(save, run.ToString());
            Add("save", "run #" + run + " (" + save + ")");
        }
        catch (Exception e) { Add("save", "threw " + e.GetType().Name + ": " + e.Message); }

        Add("serialized", spinner != null ? "spinner speed " + spinner.degreesPerSecond : "spinner reference LOST");
        Debug.Log("[probe] DONE");
    }

    private void Add(string key, string value)
    {
        report.Append(key).Append(": ").Append(value).Append('\n');
        Debug.Log("[probe] " + key + ": " + value);
    }

    private void OnGUI()
    {
        GUI.skin.label.fontSize = 22;
        GUI.Label(new Rect(16, 16, 1400, 600), report.ToString());
    }
}
