using System;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class SorenSimulationBuilder
{
    public static void Build()
    {
        const string scenePath = "Assets/SORENGAMEFIXED/Scene/MainScene.unity";
        EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTargetGroup.WebGL, BuildTarget.WebGL);
        var scene = EditorSceneManager.OpenScene(scenePath);
        foreach (var bridge in UnityEngine.Object.FindObjectsOfType<SorenBridge>(true))
            UnityEngine.Object.DestroyImmediate(bridge);
        var manager = UnityEngine.Object.FindObjectOfType<MainManager>();
        if (manager == null) throw new Exception("MainManager missing");
        manager.gameObject.AddComponent<SorenSimulationBridge>();
        EditorSceneManager.SaveScene(scene);
        // No development debugger, broadcast overlays or background workers.
        PlayerSettings.WebGL.compressionFormat = WebGLCompressionFormat.Gzip;
        PlayerSettings.WebGL.template = "APPLICATION:Default";
        EditorUserBuildSettings.webGLBuildSubtarget = WebGLTextureSubtarget.Generic;
        var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions {
            scenes = new[] { scenePath }, target = BuildTarget.WebGL,
            locationPathName = Environment.GetEnvironmentVariable("SOREN_SIMULATION_BUILD"),
            options = BuildOptions.None
        });
        EditorApplication.Exit(report.summary.result == BuildResult.Succeeded ? 0 : 1);
    }
}
