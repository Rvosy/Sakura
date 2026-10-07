import { errorText } from "../core/error-display.js";
import { importPluginModule } from "../core/plugin-module-loader.js";

function createTextController() {
  let epoch = 0, disposed = false;
  return {
    beginReply() { epoch += 1; },
    async beforeSegment(_segment, _index, { prepareVisual = () => {}, onStarted = () => {} } = {}) {
      if (disposed) return;
      const current = epoch;
      await prepareVisual();
      if (!disposed && current === epoch) onStarted({ state: "skipped" });
    },
    async afterSegment() { return false; }, async playHistorySegment() {},
    setInputCaptureActive() {}, stop() {},
    cancel() { epoch += 1; },
    dispose() { disposed = true; epoch += 1; },
  };
}

export function createTtsControllerHost(options, importModule = importPluginModule) {
  let controller = createTextController();
  let generation = null;
  let revision = 0;
  let disposed = false;
  let captureActive = false;

  return Object.freeze({
    async rebind(generationId) {
      if (disposed) return false;
      if (generation === generationId) return true;
      controller.dispose();
      const current = ++revision;
      controller = createTextController();
      generation = null;
      let next = null;
      try {
        const descriptor = await options.invoke("plugin_frontend_module", {
          serviceKey: "sakura.tts", moduleName: "playback",
        });
        if (!descriptor) throw new Error("TTS_SERVICE_UNAVAILABLE");
        const module = await importModule(descriptor.source);
        if (disposed || revision !== current) return false;
        next = module.createTtsController({
          ...options, errorText,
          onDiagnostic: (...args) => {
            if (!disposed && revision === current) options.onDiagnostic?.(...args);
          },
          onPlaybackState: (...args) => {
            if (!disposed && revision === current) options.onPlaybackState?.(...args);
          },
        });
        await next.start();
        if (disposed || revision !== current) {
          next.dispose();
          return false;
        }
        next.setInputCaptureActive(captureActive);
        controller.dispose();
        controller = next;
        generation = generationId;
        return true;
      } catch (error) {
        next?.dispose();
        if (!disposed && revision === current) options.onDiagnostic?.(errorText(error));
        return false;
      }
    },
    beginReply: (...args) => controller.beginReply(...args),
    beforeSegment: (...args) => controller.beforeSegment(...args),
    afterSegment: (...args) => controller.afterSegment(...args),
    playHistorySegment: (...args) => controller.playHistorySegment(...args),
    setInputCaptureActive(active) {
      captureActive = active;
      controller.setInputCaptureActive(active);
    },
    stop: () => controller.stop(),
    cancel: () => controller.cancel(),
    dispose() {
      disposed = true;
      revision += 1;
      controller.dispose();
    },
  });
}
