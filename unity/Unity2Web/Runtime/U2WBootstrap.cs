using System.IO;
using System.Runtime.InteropServices;
using Unity2Web.Shims;
using UnityEngine;

namespace Unity2Web
{
    /// <summary>
    /// Wires Unity2Web.Shims to the engine before any game code runs, pumps work that patched
    /// "threads" posted to the main thread, and keeps the IndexedDB-backed save folder in sync.
    /// Injected into the recovered project by the harness; the game itself is not modified.
    /// </summary>
#if UNITY_5_5_OR_NEWER
    [DefaultExecutionOrder(-32000)]
#endif
    [AddComponentMenu("")]
    public sealed class U2WBootstrap : MonoBehaviour
    {
        private const string PackedMarker = "/u2wdata/StreamingAssets/.u2w-packed";
        private const float SyncIntervalSeconds = 3f;

        private static U2WBootstrap instance;
        private float nextSync;

#if UNITY_WEBGL && !UNITY_EDITOR
        [DllImport("__Internal")] private static extern void U2W_SyncFS();
        [DllImport("__Internal")] private static extern void U2W_InstallPageHooks();
#else
        private static void U2W_SyncFS() { }
        private static void U2W_InstallPageHooks() { }
#endif

#if UNITY_2019_2_OR_NEWER
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
#else
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
#endif
        private static void Configure()
        {
            U2W.LogSink = message => Debug.Log(message);
            U2W.WarnSink = message => Debug.LogWarning(message);
#if UNITY_WEBGL && !UNITY_EDITOR
            U2W.IsWeb = true;
#endif
            EnvShim.PersistentDataPath = Application.persistentDataPath;
            ProcessShim.OpenUrl = url => Application.OpenURL(url);
            PathShim.OriginalDataPath = Application.dataPath;
            PathShim.StreamingAssetsUrl = Application.streamingAssetsPath;
            PathShim.StreamingAssetsPacked = U2W.IsWeb && File.Exists(PackedMarker);
            if (U2W.IsWeb)
            {
                U2W.Log("bootstrap ready; StreamingAssets " + (PathShim.StreamingAssetsPacked ? "packed in memory" : "served over HTTP"));
            }
        }

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
        private static void Spawn()
        {
            if (instance != null) return;
            TextAsset skip = Resources.Load<TextAsset>("u2w-skip-threads");
            if (skip != null)
            {
                foreach (string line in skip.text.Split('\n'))
                {
                    if (line.Trim().Length > 0) ThreadShim.SkipBodies.Add(line.Trim());
                }
            }

            GameObject host = new GameObject("[Unity2Web]");
            host.hideFlags = HideFlags.HideInHierarchy;
            DontDestroyOnLoad(host);
            instance = host.AddComponent<U2WBootstrap>();
            U2W_InstallPageHooks();
        }

        private void Update()
        {
            MainThread.Pump();
            if (Time.unscaledTime >= nextSync)
            {
                nextSync = Time.unscaledTime + SyncIntervalSeconds;
                U2W_SyncFS();
            }
        }

        private void OnApplicationFocus(bool hasFocus)
        {
            if (!hasFocus) U2W_SyncFS();
        }

        private void OnApplicationPause(bool paused)
        {
            if (paused) U2W_SyncFS();
        }

        private void OnDestroy()
        {
            // Some games wipe every object on "return to title"; come back next scene load.
            if (instance == this) instance = null;
        }
    }
}
