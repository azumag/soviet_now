'use strict';

// Preload fixture for exercising obs_window_capture_source.sh's embedded Node
// client without opening a socket or touching a real OBS instance. The caller
// supplies FAKE_OBS_TRACE_PATH and optional current settings through env vars.
const fs = require('node:fs');

class FakeObsWebSocket {
  constructor(url) {
    this.url = url;
    this.listeners = new Map();
    this.requests = [];
    if (process.env.FAKE_OBS_CONSTRUCT_PATH) {
      fs.writeFileSync(process.env.FAKE_OBS_CONSTRUCT_PATH, 'constructed');
    }
    queueMicrotask(() => {
      this.emit('open', {});
      // connectAndIdentify installs its message listener after the open promise
      // resolves, so deliver Hello on the following microtask.
      queueMicrotask(() => this.emitMessage({ op: 0, d: {} }));
    });
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  emit(type, event) {
    for (const listener of this.listeners.get(type) || []) listener(event);
  }

  emitMessage(payload) {
    this.emit('message', { data: JSON.stringify(payload) });
  }

  send(raw) {
    const payload = JSON.parse(String(raw));
    if (payload.op === 1) {
      queueMicrotask(() => this.emitMessage({ op: 2, d: {} }));
      return;
    }
    if (payload.op !== 6) return;

    const request = payload.d || {};
    this.requests.push({
      requestType: request.requestType,
      requestData: request.requestData || {},
    });
    const responseData = this.responseFor(request.requestType);
    queueMicrotask(() => this.emitMessage({
      op: 7,
      d: {
        requestType: request.requestType,
        requestId: request.requestId,
        requestStatus: { result: true, code: 100 },
        responseData,
      },
    }));
  }

  responseFor(requestType) {
    const sourceName = process.env.SOREN_OBS_GAME_SOURCE_NAME || 'sorengame';
    if (requestType === 'GetInputList') {
      if (process.env.FAKE_OBS_INPUT_EXISTS === '0') return { inputs: [] };
      const inputKind = process.env.FAKE_OBS_EXISTING_INPUT_KIND
        || process.env.OBS_WINDOW_CAPTURE_INPUT_KIND
        || 'xshm_input';
      const unversionedInputKind = process.env.FAKE_OBS_EXISTING_UNVERSIONED_INPUT_KIND
        || inputKind.replace(/_v\d+$/, '');
      return {
        inputs: [{ inputName: sourceName, inputKind, unversionedInputKind }],
      };
    }
    if (requestType === 'GetInputSettings') {
      return { inputSettings: JSON.parse(process.env.FAKE_OBS_CURRENT_SETTINGS || '{}') };
    }
    if (requestType === 'GetInputPropertiesListPropertyItems') {
      return {
        propertyItems: [{
          itemName: process.env.FAKE_OBS_WINDOW_ITEM_NAME
            || 'Unity WebGL Player | soren-game - Chromium',
          itemValue: process.env.FAKE_OBS_WINDOW_ITEM_VALUE
            || '41943043\r\nUnity WebGL Player | soren-game - Chromium\r\nchromium',
        }],
      };
    }
    if (requestType === 'GetSceneItemList') {
      return {
        sceneItems: [
          { sourceName, sceneItemId: 1 },
          { sourceName: 'eventOverlay', sceneItemId: 2 },
          { sourceName: 'twica', sceneItemId: 3 },
        ],
      };
    }
    return {};
  }

  close() {
    const tracePath = process.env.FAKE_OBS_TRACE_PATH;
    if (tracePath) fs.writeFileSync(tracePath, JSON.stringify(this.requests));
    this.emit('close', {});
  }
}

globalThis.WebSocket = FakeObsWebSocket;
