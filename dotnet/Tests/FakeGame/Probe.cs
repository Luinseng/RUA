using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;

namespace FakeGame
{
    public static class Probe
    {
        [DllImport("steam_api64", EntryPoint = "SteamAPI_Init")]
        private static extern bool SteamAPI_Init();

        [DllImport("steam_api64")]
        private static extern int SteamAPI_GetHSteamUser();

        [DllImport("steam_api64")]
        private static extern void SteamAPI_GetStats(out int count, out Guid id);

        [DllImport("steam_api64")]
        private static extern int SteamAPI_RestartAppIfNecessary(uint appId);

        [DllImport("fmodstudio")]
        private static extern int FMOD_Studio_System_Create(out IntPtr system, uint headerVersion);

        [DllImport("__Internal")]
        private static extern void JsAlert(string message);

        public static string PoolResult = "none";

        public static string Native()
        {
            bool ok = SteamAPI_Init();
            int user = SteamAPI_GetHSteamUser();
            SteamAPI_GetStats(out int count, out Guid id);
            return $"{ok}|{user}|{count}|{id}|{SteamAPI_RestartAppIfNecessary(480)}";
        }

        public static int Fmod() => FMOD_Studio_System_Create(out _, 1);

        public static void Alert() => JsAlert("hi");

        public static string Threads()
        {
            int mainId = Thread.CurrentThread.ManagedThreadId;
            int ranOn = -1;
            var thread = new Thread(() => { ranOn = Thread.CurrentThread.ManagedThreadId; });
            thread.Start();
            thread.Join();
            return ranOn == mainId ? "main" : "other";
        }

        public static string Sleep()
        {
            var watch = Stopwatch.StartNew();
            Thread.Sleep(500);
            return watch.ElapsedMilliseconds < 100 ? "skipped" : "slept";
        }

        public static void Pool() => ThreadPool.QueueUserWorkItem(_ => PoolResult = "ran");

        public static void OpenUrl() => Process.Start("https://example.com/");

        public static string Folder() => Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);

        public static string DataPath() => UnityEngine.Application.dataPath;

        public static string Streaming() => UnityEngine.Application.streamingAssetsPath;

        public static string Url() =>
            UnityEngine.Networking.UnityWebRequest.Get(Path.Combine(UnityEngine.Application.streamingAssetsPath, "a.json")).Url;

        public static string UrlWithMoreArguments() =>
            UnityEngine.Networking.UnityWebRequest.Post(UnityEngine.Application.streamingAssetsPath + "/b.json", "payload").Url;

        public static bool TypeByName() => Type.GetType("FakeGame.Probe, Assembly-CSharp") != null;

        public static string Joined()
        {
            var thread = new Thread(() => { });
            thread.Start();
            thread.Join();
            thread.Join(); // joining a finished thread must not throw
            return thread.IsAlive ? "alive" : "done";
        }

        // Only scanned, never called: a worker loop the scanner must flag.
        public static void WorkerLoop() => new Thread(() => { while (true) Thread.Sleep(10); }).Start();
    }
}
