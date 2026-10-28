using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Threading;

namespace Unity2Web.Shims
{
    /// <summary>
    /// Logging and configuration shared by every shim. U2WBootstrap (project side) wires the sinks
    /// to UnityEngine.Debug and fills in the platform paths before the first scene loads.
    /// </summary>
    public static class U2W
    {
        public static Action<string> LogSink;
        public static Action<string> WarnSink;

        /// <summary>True only inside the WebGL player. Shims stay transparent in the editor.</summary>
        public static bool IsWeb;

        private static readonly Dictionary<string, bool> reported = new Dictionary<string, bool>();
        private static readonly object reportedLock = new object();

        public static void Log(string message)
        {
            Action<string> sink = LogSink;
            if (sink != null) sink("[u2w] " + message);
        }

        public static void Warn(string message)
        {
            Action<string> sink = WarnSink ?? LogSink;
            if (sink != null) sink("[u2w] " + message);
        }

        /// <summary>Called at the top of every stubbed native import. Logs each entry point once.</summary>
        public static void Stub(string id)
        {
            lock (reportedLock)
            {
                if (reported.ContainsKey(id)) return;
                reported[id] = true;
            }
            Warn("native call stubbed (no browser equivalent): " + id);
        }
    }

    /// <summary>
    /// Single-threaded work queue drained once per frame by U2WBootstrap.Update().
    /// The browser main thread is the only thread a non-threaded WebGL build has.
    /// </summary>
    public static class MainThread
    {
        private static readonly Queue<Action> queue = new Queue<Action>();
        private static readonly object queueLock = new object();

        /// <summary>Upper bound per frame so a flood of work items cannot freeze the tab.</summary>
        public static int BudgetMilliseconds = 8;

        public static void Post(Action action)
        {
            if (action == null) return;
            lock (queueLock) queue.Enqueue(action);
        }

        public static int Pending
        {
            get { lock (queueLock) return queue.Count; }
        }

        public static void Pump()
        {
            Stopwatch watch = Stopwatch.StartNew();
            while (true)
            {
                Action next;
                lock (queueLock)
                {
                    if (queue.Count == 0) return;
                    next = queue.Dequeue();
                }
                try
                {
                    next();
                }
                catch (Exception e)
                {
                    U2W.Warn("deferred work item threw: " + e);
                }
                if (watch.ElapsedMilliseconds >= BudgetMilliseconds) return;
            }
        }
    }

    /// <summary>
    /// Replacement for System.Threading.Thread in builds without threads.
    /// Each "thread" body runs later on the main thread. Join() runs a still-pending body immediately,
    /// so producer/consumer code that starts a thread and then joins it keeps its ordering.
    /// Bodies that loop forever (worker loops) cannot be emulated; the scanner flags them.
    /// </summary>
    public static class ThreadShim
    {
        private sealed class Box
        {
            public Delegate Body;
            public object Argument;
            public int State; // 0 created, 1 queued, 2 running, 3 finished
        }

        private static readonly Dictionary<Thread, Box> boxes = new Dictionary<Thread, Box>();
        private static readonly Queue<Thread> finished = new Queue<Thread>();
        private static readonly object boxesLock = new object();

        /// <summary>Finished threads stay known (Join/IsAlive/Abort after the fact) up to this many.</summary>
        private const int FinishedHistory = 4096;

        /// <summary>
        /// "Type::Method" names of thread bodies never to run (worker loops that would freeze the tab).
        /// Filled by U2WBootstrap from Resources/u2w-skip-threads.txt, written by the harness from rules.json.
        /// </summary>
        public static readonly List<string> SkipBodies = new List<string>();

        private static Thread Register(Thread thread, Delegate body)
        {
            lock (boxesLock) boxes[thread] = new Box { Body = body };
            return thread;
        }

        private static void MarkFinished(Thread thread, Box box)
        {
            lock (boxesLock)
            {
                if (box.State == 3) return;
                box.State = 3;
                finished.Enqueue(thread);
                while (finished.Count > FinishedHistory) boxes.Remove(finished.Dequeue());
            }
        }

        private static bool IsSkipped(Delegate body)
        {
            if (SkipBodies.Count == 0 || body == null || body.Method == null) return false;
            string name = (body.Method.DeclaringType != null ? body.Method.DeclaringType.FullName : "") + "::" + body.Method.Name;
            return SkipBodies.Contains(name);
        }

        private static Box Find(Thread thread)
        {
            if (thread == null) return null;
            lock (boxesLock)
            {
                Box box;
                return boxes.TryGetValue(thread, out box) ? box : null;
            }
        }

        public static Thread Create(ThreadStart start)
        {
            return U2W.IsWeb ? Register(new Thread(start), start) : new Thread(start);
        }

        public static Thread Create(ThreadStart start, int maxStackSize)
        {
            return U2W.IsWeb ? Register(new Thread(start, maxStackSize), start) : new Thread(start, maxStackSize);
        }

        public static Thread Create(ParameterizedThreadStart start)
        {
            return U2W.IsWeb ? Register(new Thread(start), start) : new Thread(start);
        }

        public static Thread Create(ParameterizedThreadStart start, int maxStackSize)
        {
            return U2W.IsWeb ? Register(new Thread(start, maxStackSize), start) : new Thread(start, maxStackSize);
        }

        public static void Start(Thread thread)
        {
            StartCore(thread, null, false);
        }

        public static void Start(Thread thread, object parameter)
        {
            StartCore(thread, parameter, true);
        }

        private static void StartCore(Thread thread, object parameter, bool hasParameter)
        {
            Box box = Find(thread);
            if (box == null)
            {
                // Not created through the shim (or running in the editor): use the real thread.
                if (hasParameter) thread.Start(parameter); else thread.Start();
                return;
            }
            if (box.State != 0) throw new ThreadStateException("Thread has already been started.");
            box.Argument = parameter;
            if (IsSkipped(box.Body))
            {
                U2W.Warn("thread body skipped by rules (would loop forever on the main thread): " + box.Body.Method.Name);
                MarkFinished(thread, box);
                return;
            }
            box.State = 1;
            MainThread.Post(delegate { Run(thread, box); });
        }

        private static void Run(Thread thread, Box box)
        {
            if (box.State != 1) return;
            box.State = 2;
            try
            {
                ParameterizedThreadStart parameterized = box.Body as ParameterizedThreadStart;
                if (parameterized != null) parameterized(box.Argument);
                else ((ThreadStart)box.Body)();
            }
            catch (ThreadAbortException)
            {
            }
            catch (Exception e)
            {
                U2W.Warn("emulated thread threw: " + e);
            }
            finally
            {
                MarkFinished(thread, box);
            }
        }

        public static void Join(Thread thread)
        {
            Box box = Find(thread);
            if (box == null) { thread.Join(); return; }
            if (box.State == 1) Run(thread, box);
        }

        public static bool Join(Thread thread, int millisecondsTimeout)
        {
            Box box = Find(thread);
            if (box == null) return thread.Join(millisecondsTimeout);
            if (box.State == 1) Run(thread, box);
            return box.State == 3;
        }

        public static bool Join(Thread thread, TimeSpan timeout)
        {
            return Join(thread, (int)timeout.TotalMilliseconds);
        }

        public static bool IsAlive(Thread thread)
        {
            Box box = Find(thread);
            if (box == null) return thread.IsAlive;
            return box.State == 1 || box.State == 2;
        }

        public static void Abort(Thread thread)
        {
            Box box = Find(thread);
            if (box == null) { thread.Abort(); return; }
            MarkFinished(thread, box);
        }

        /// <summary>The browser main thread can never block; sleeping is a no-op there.</summary>
        public static void Sleep(int millisecondsTimeout)
        {
            if (!U2W.IsWeb) Thread.Sleep(millisecondsTimeout);
        }

        public static void Sleep(TimeSpan timeout)
        {
            if (!U2W.IsWeb) Thread.Sleep(timeout);
        }

        public static bool QueueUserWorkItem(WaitCallback callback)
        {
            if (!U2W.IsWeb) return ThreadPool.QueueUserWorkItem(callback);
            MainThread.Post(delegate { callback(null); });
            return true;
        }

        public static bool QueueUserWorkItem(WaitCallback callback, object state)
        {
            if (!U2W.IsWeb) return ThreadPool.QueueUserWorkItem(callback, state);
            MainThread.Post(delegate { callback(state); });
            return true;
        }
    }

    /// <summary>
    /// Process.Start in a browser: URLs open in a new tab, everything else is refused quietly
    /// (desktop games use it for "open save folder", launchers and crash reporters).
    /// </summary>
    public static class ProcessShim
    {
        public static Action<string> OpenUrl;

        private static bool LooksLikeUrl(string target)
        {
            if (string.IsNullOrEmpty(target)) return false;
            string lower = target.Trim().ToLowerInvariant();
            return lower.StartsWith("http://") || lower.StartsWith("https://") || lower.StartsWith("mailto:")
                || lower.StartsWith("steam://") || lower.StartsWith("discord://");
        }

        private static Process Open(string fileName, string arguments)
        {
            if (LooksLikeUrl(fileName))
            {
                Action<string> open = OpenUrl;
                if (open != null) open(fileName.Trim());
            }
            else
            {
                U2W.Warn("Process.Start refused in browser: " + fileName + " " + arguments);
            }
            return null;
        }

        public static Process Start(string fileName)
        {
            return U2W.IsWeb ? Open(fileName, null) : Process.Start(fileName);
        }

        public static Process Start(string fileName, string arguments)
        {
            return U2W.IsWeb ? Open(fileName, arguments) : Process.Start(fileName, arguments);
        }

        public static Process Start(ProcessStartInfo startInfo)
        {
            if (!U2W.IsWeb) return Process.Start(startInfo);
            return startInfo == null ? null : Open(startInfo.FileName, startInfo.Arguments);
        }

        public static bool StartInstance(Process process)
        {
            if (!U2W.IsWeb) return process.Start();
            Open(process.StartInfo.FileName, process.StartInfo.Arguments);
            return false;
        }
    }

    /// <summary>
    /// Redirects desktop folder lookups (Documents, AppData, ...) into the persistent IndexedDB-backed
    /// folder, so save files written there survive a page reload.
    /// </summary>
    public static class EnvShim
    {
        public static string PersistentDataPath = "/idbfs/u2w";

        public static string GetFolderPath(Environment.SpecialFolder folder)
        {
            if (!U2W.IsWeb) return Environment.GetFolderPath(folder);
            string path = Path.Combine(Path.Combine(PersistentDataPath, "u2w_special"), folder.ToString());
            try
            {
                if (!Directory.Exists(path)) Directory.CreateDirectory(path);
            }
            catch (Exception e)
            {
                U2W.Warn("could not create " + path + ": " + e.Message);
            }
            return path;
        }
    }

    /// <summary>
    /// Desktop games read StreamingAssets synchronously with System.IO. In the browser
    /// Application.streamingAssetsPath is a URL, so the harness packs StreamingAssets into the
    /// build's .data file (mounted in memory at startup) and the patcher redirects the getters here.
    /// </summary>
    public static class PathShim
    {
        /// <summary>Virtual folder that stands in for &lt;Game&gt;_Data.</summary>
        public static string DataPath = "/u2wdata";

        /// <summary>Real StreamingAssets URL, set by U2WBootstrap; used to translate paths back to URLs.</summary>
        public static string StreamingAssetsUrl;

        /// <summary>Set by U2WBootstrap from the packed-data marker file.</summary>
        public static bool StreamingAssetsPacked;

        /// <summary>Unpatched getter values, set by U2WBootstrap; used when nothing was packed.</summary>
        public static string OriginalDataPath;

        public static string GetDataPath()
        {
            if (U2W.IsWeb && StreamingAssetsPacked) return DataPath;
            return OriginalDataPath ?? DataPath;
        }

        public static string GetStreamingAssetsPath()
        {
            if (U2W.IsWeb && StreamingAssetsPacked) return DataPath + "/StreamingAssets";
            return StreamingAssetsUrl ?? (DataPath + "/StreamingAssets");
        }

        /// <summary>
        /// Turns a packed virtual path back into a URL for web-request APIs.
        /// Plain file paths and file:// URLs that point into the packed folder are rewritten.
        /// </summary>
        public static string ToUrl(string pathOrUrl)
        {
            if (!U2W.IsWeb || string.IsNullOrEmpty(pathOrUrl) || string.IsNullOrEmpty(StreamingAssetsUrl)) return pathOrUrl;
            string candidate = pathOrUrl;
            if (candidate.StartsWith("file://")) candidate = candidate.Substring("file://".Length);
            string prefix = DataPath + "/StreamingAssets";
            if (!candidate.StartsWith(prefix)) return pathOrUrl;
            return StreamingAssetsUrl.TrimEnd('/') + candidate.Substring(prefix.Length).Replace('\\', '/');
        }
    }
}
