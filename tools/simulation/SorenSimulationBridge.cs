// Only included in the separate local evaluation project. Never attach to the live build.
using System;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Text;
using UnityEngine;

[DefaultExecutionOrder(-10000)]
public class SorenSimulationBridge : MonoBehaviour
{
    [DllImport("__Internal")] static extern int JS_SimulationSeed();
    [DllImport("__Internal")] static extern float JS_SimulationSpeed();
    [DllImport("__Internal")] static extern int JS_SimulationPaused();
    [DllImport("__Internal")] static extern void JS_PushGameState(string json);
    [DllImport("__Internal")] static extern void JS_GetCommand(byte[] buf, int size);
    MainManager manager;
    readonly byte[] buffer = new byte[256];
    int seed;
    float speed;
    bool hostPaused;
    static UnityEngine.Random.State queueState;

    public static int NextIndex(int count)
    {
        var saved = UnityEngine.Random.state;
        UnityEngine.Random.state = queueState;
        int value = UnityEngine.Random.Range(0, count);
        queueState = UnityEngine.Random.state;
        UnityEngine.Random.state = saved;
        return value;
    }

    void Awake()
    {
        CultureInfo.CurrentCulture = CultureInfo.InvariantCulture;
        seed = JS_SimulationSeed();
        speed = Mathf.Clamp(JS_SimulationSpeed(), 1, 16);
        UnityEngine.Random.InitState(seed);
        queueState = UnityEngine.Random.state;
        Application.runInBackground = true;
        QualitySettings.vSyncCount = 0;
        Application.targetFrameRate = 60;
        AudioListener.volume = 0;
        // Keep fixedDeltaTime and all physics settings from the original project.
    }

    void Start() { manager = FindObjectOfType<MainManager>(); }

    void Update()
    {
        if (manager == null) return;
        Array.Clear(buffer, 0, buffer.Length);
        JS_GetCommand(buffer, buffer.Length);
        string cmd = Encoding.UTF8.GetString(buffer).TrimEnd('\0');
        if (cmd.Length > 0) manager.ExecuteBridgeCommand(cmd);
    }

    void LateUpdate()
    {
        if (manager == null) return;
        bool pause = JS_SimulationPaused() != 0;
        var state = manager.GetGameState();
        bool playing = state == MainManager.GameState.MOVE || state == MainManager.GameState.DROP;
        // Natural game-over / founding pauses belong to MainManager.
        if (playing && (Time.timeScale > 0 || hostPaused)) Time.timeScale = pause ? 0 : speed;
        hostPaused = pause && playing;
        string json = manager.GetBridgeState();
        string metadata = ",\"simulation\":{\"schema\":1,\"seed\":" + seed +
            ",\"speed\":" + speed.ToString("R") + ",\"time\":" + Time.timeSinceLevelLoad.ToString("R") +
            ",\"fixed_dt\":" + Time.fixedDeltaTime.ToString("R") +
            ",\"frame\":" + Time.frameCount + ",\"paused\":" + (hostPaused ? "true" : "false") + "}}";
        JS_PushGameState(json.Substring(0, json.Length - 1) + metadata);
    }
}
