using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEditorInternal;
using UnityEngine;

namespace Unity2Web.Editor
{
    /// <summary>
    /// Batch-mode WebGL build for a recovered game project. Compiles on Unity 5.6 through Unity 6:
    /// every setting that moved between versions is applied by reflection and skipped when absent.
    ///
    /// Unity -batchmode -quit -projectPath P -buildTarget WebGL -logFile L
    ///       -executeMethod Unity2Web.Editor.U2WBuilder.Build -u2wOut &lt;dir&gt;
    ///       [-u2wDevelopment] [-u2wMaxMemory 4096] [-u2wExceptions FullWithoutStacktrace] [-u2wEmscriptenArgs "..."]
    /// </summary>
    public static class U2WBuilder
    {
        [MenuItem("Unity2Web/Build WebGL")]
        public static void Build()
        {
            DateTime started = DateTime.Now;
            string output = Path.GetFullPath(Arg("-u2wOut") ?? "Builds/WebGL");
            bool development = HasArg("-u2wDevelopment");

            try
            {
                SwitchToWebGL();
                Configure(development);
                string[] scenes = Scenes();
                if (scenes.Length == 0) throw new InvalidOperationException("no scenes found to build");
                Debug.Log("[u2w] building " + scenes.Length + " scenes to " + output);

                Directory.CreateDirectory(output);
                BuildOptions options = development ? BuildOptions.Development : BuildOptions.None;
                bool ok;
                string detail;
#if UNITY_2018_1_OR_NEWER
                UnityEditor.Build.Reporting.BuildReport report = BuildPipeline.BuildPlayer(scenes, output, BuildTarget.WebGL, options);
                ok = report.summary.result == UnityEditor.Build.Reporting.BuildResult.Succeeded;
                detail = report.summary.result + ", " + report.summary.totalErrors + " errors, " + report.summary.totalSize + " bytes";
#else
                string error = BuildPipeline.BuildPlayer(scenes, output, BuildTarget.WebGL, options);
                ok = string.IsNullOrEmpty(error);
                detail = ok ? "Succeeded" : error;
#endif
                WriteSummary(output, ok, detail, scenes, started);
                Debug.Log("[u2w] build " + (ok ? "succeeded" : "FAILED") + ": " + detail);
                Finish(ok ? 0 : 1);
            }
            catch (Exception e)
            {
                Debug.LogError("[u2w] build crashed: " + e);
                WriteSummary(output, false, e.Message, new string[0], started);
                Finish(1);
            }
        }

        private static void Finish(int code)
        {
            if (InternalEditorUtility.inBatchMode) EditorApplication.Exit(code);
        }

        private static void SwitchToWebGL()
        {
            if (EditorUserBuildSettings.activeBuildTarget == BuildTarget.WebGL) return;
#if UNITY_5_6_OR_NEWER
            EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTargetGroup.WebGL, BuildTarget.WebGL);
#else
            EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTarget.WebGL);
#endif
        }

        private static void Configure(bool development)
        {
            Type webgl = typeof(PlayerSettings).GetNestedType("WebGL", BindingFlags.Public);
            if (webgl == null) throw new InvalidOperationException("PlayerSettings.WebGL not found: is WebGL Build Support installed?");

            // The harness packs StreamingAssets into the .data file and compresses afterwards.
            Set(webgl, "compressionFormat", "Disabled");
            Set(webgl, "decompressionFallback", false);
            Set(webgl, "nameFilesAsHashes", false);
            // Desktop Mono code freely relies on catching NullReferenceException; keep that behaviour.
            Set(webgl, "exceptionSupport", Arg("-u2wExceptions") ?? "FullWithoutStacktrace");
            Set(webgl, "dataCaching", true);
            Set(webgl, "threadsSupport", false);
            Set(webgl, "linkerTarget", "Wasm");
            Set(webgl, "powerPreference", "HighPerformance");

            int maxMemory = int.Parse(Arg("-u2wMaxMemory") ?? "4096");
            if (!Set(webgl, "maximumMemorySize", maxMemory))
            {
                // Older versions allocate this heap up front, so a huge value fails to load on many machines.
                int legacyMemory = int.Parse(Arg("-u2wLegacyMemory") ?? "1024");
                Set(webgl, "memorySize", Math.Min(legacyMemory, 2032));
            }
            Set(webgl, "memoryGrowthMode", "Geometric");

            string emscriptenArgs = Arg("-u2wEmscriptenArgs");
            if (!string.IsNullOrEmpty(emscriptenArgs)) Set(webgl, "emscriptenArgs", emscriptenArgs);

#if UNITY_2020_1_OR_NEWER
            Set(webgl, "template", "PROJECT:Unity2Web");
#else
            Set(webgl, "template", "PROJECT:Unity2WebLegacy");
#endif

            // Keep the game's code: only class libraries and the engine may be stripped.
            MethodInfo stripping = typeof(PlayerSettings).GetMethod("SetManagedStrippingLevel", new[] { typeof(BuildTargetGroup), FindType("UnityEditor.ManagedStrippingLevel") ?? typeof(int) });
            if (stripping != null)
            {
                // "Minimal" exists from Unity 2021.2; earlier IL2CPP targets bottom out at "Low".
                Type level = stripping.GetParameters()[1].ParameterType;
                string lowest = Enum.GetNames(level).Contains("Minimal") ? "Minimal" : "Low";
                stripping.Invoke(null, new object[] { BuildTargetGroup.WebGL, Enum.Parse(level, lowest) });
            }
            PlayerSettings.stripEngineCode = false;
            EditorUserBuildSettings.development = development;
        }

        private static Type FindType(string fullName)
        {
            return typeof(PlayerSettings).Assembly.GetType(fullName);
        }

        private static bool Set(Type owner, string property, object value)
        {
            PropertyInfo info = owner.GetProperty(property, BindingFlags.Public | BindingFlags.Static);
            if (info == null || !info.CanWrite)
            {
                Debug.Log("[u2w] PlayerSettings.WebGL." + property + " not in this Unity version, skipped");
                return false;
            }
            try
            {
                object converted = info.PropertyType.IsEnum
                    ? Enum.Parse(info.PropertyType, value.ToString(), true)
                    : Convert.ChangeType(value, info.PropertyType);
                info.SetValue(null, converted, null);
                return true;
            }
            catch (Exception e)
            {
                Debug.LogWarning("[u2w] could not set PlayerSettings.WebGL." + property + " = " + value + ": " + e.Message);
                return false;
            }
        }

        private static string[] Scenes()
        {
            string[] listed = EditorBuildSettings.scenes.Where(s => s.enabled && File.Exists(s.path)).Select(s => s.path).ToArray();
            if (listed.Length > 0) return listed;
            Debug.LogWarning("[u2w] EditorBuildSettings has no scenes; falling back to every scene under Assets (order may differ from the game)");
            return Directory.GetFiles("Assets", "*.unity", SearchOption.AllDirectories)
                .Select(p => p.Replace('\\', '/'))
                .OrderBy(p => p, StringComparer.Ordinal)
                .ToArray();
        }

        private static void WriteSummary(string output, bool ok, string detail, string[] scenes, DateTime started)
        {
            try
            {
                Directory.CreateDirectory(output);
                var json = new StringBuilder();
                json.Append("{\n");
                json.Append("  \"success\": ").Append(ok ? "true" : "false").Append(",\n");
                json.Append("  \"detail\": ").Append(Quote(detail)).Append(",\n");
                json.Append("  \"unityVersion\": ").Append(Quote(Application.unityVersion)).Append(",\n");
                json.Append("  \"seconds\": ").Append((int)(DateTime.Now - started).TotalSeconds).Append(",\n");
                json.Append("  \"scenes\": [").Append(string.Join(", ", scenes.Select(Quote).ToArray())).Append("]\n");
                json.Append("}\n");
                File.WriteAllText(Path.Combine(output, "u2w-build.json"), json.ToString());
            }
            catch (Exception e)
            {
                Debug.LogWarning("[u2w] could not write build summary: " + e.Message);
            }
        }

        private static string Quote(string s)
        {
            return "\"" + (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\n", "\\n").Replace("\r", "") + "\"";
        }

        private static string Arg(string name)
        {
            string[] args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length - 1; i++)
            {
                if (args[i] == name) return args[i + 1];
            }
            return null;
        }

        private static bool HasArg(string name)
        {
            return Environment.GetCommandLineArgs().Contains(name);
        }
    }
}
