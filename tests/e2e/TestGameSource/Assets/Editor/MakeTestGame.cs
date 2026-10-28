using UnityEditor;
using UnityEditor.Build;
using UnityEditor.SceneManagement;
using UnityEngine;

/// <summary>Builds the e2e fixture: one scene, Windows x64, Mono backend (like most PC Unity games).</summary>
public static class MakeTestGame
{
    public static void Build()
    {
        var scene = EditorSceneManager.NewScene(NewSceneSetup.DefaultGameObjects, NewSceneMode.Single);

        GameObject cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
        cube.name = "Spinning Cube";
        Spinner spinner = cube.AddComponent<Spinner>();
        spinner.degreesPerSecond = 45f; // non-default: proves serialized data survives the round trip

        var probe = new GameObject("Probe").AddComponent<Probe>();
        probe.spinner = spinner;

        Camera.main.transform.position = new Vector3(0f, 1f, -4f);
        Camera.main.transform.LookAt(Vector3.zero);
        Camera.main.backgroundColor = new Color(0.12f, 0.16f, 0.22f);
        Camera.main.clearFlags = CameraClearFlags.SolidColor;

        const string path = "Assets/Scenes/Main.unity";
        System.IO.Directory.CreateDirectory("Assets/Scenes");
        EditorSceneManager.SaveScene(scene, path);
        EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(path, true) };

        PlayerSettings.companyName = "Unity2Web";
        PlayerSettings.productName = "U2W Test Game";
        PlayerSettings.SetScriptingBackend(NamedBuildTarget.Standalone, ScriptingImplementation.Mono2x);

        string output = System.Environment.GetCommandLineArgs().Length > 0 ? Arg("-testGameOut") : null;
        var report = BuildPipeline.BuildPlayer(new[] { path }, (output ?? "Build") + "/U2WTestGame.exe",
            BuildTarget.StandaloneWindows64, BuildOptions.None);
        Debug.Log("[e2e] build result: " + report.summary.result);
        EditorApplication.Exit(report.summary.result == UnityEditor.Build.Reporting.BuildResult.Succeeded ? 0 : 1);
    }

    private static string Arg(string name)
    {
        string[] args = System.Environment.GetCommandLineArgs();
        for (int i = 0; i < args.Length - 1; i++) if (args[i] == name) return args[i + 1];
        return null;
    }
}
