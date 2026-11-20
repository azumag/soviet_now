mergeInto(LibraryManager.library, {
  JS_SimulationSeed: function() { return window.__sorenSimulationConfig.seed | 0; },
  JS_SimulationSpeed: function() { return window.__sorenSimulationConfig.speed; },
  JS_SimulationPaused: function() { return window.__sorenSimulationPaused ? 1 : 0; }
});
