// 浏览器预览只读取音量，不保存或上传音频。正式窗口仍由原生采集器供给音量。
export function createMicrophoneLevels({ window, onLevel }) {
  let revision = 0, stream = null, context = null, timer = null;
  function stop() {
    revision++;
    window.clearInterval(timer);
    timer = null;
    stream?.getTracks().forEach(track => track.stop());
    stream = null;
    void context?.close();
    context = null;
  }
  return {
    stop,
    async start(deviceId) {
      stop();
      const ticket = revision;
      const input = await window.navigator.mediaDevices.getUserMedia({
        audio: { ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
          autoGainControl: false, echoCancellation: false, noiseSuppression: false },
      });
      if (ticket !== revision) { input.getTracks().forEach(track => track.stop()); return false; }
      stream = input;
      const audio = new window.AudioContext();
      context = audio;
      try {
        await audio.resume();
        if (ticket !== revision) return false;
        const analyser = audio.createAnalyser();
        analyser.fftSize = 2048;
        audio.createMediaStreamSource(input).connect(analyser);
        const samples = new Float32Array(analyser.fftSize);
        let level = 0;
        timer = window.setInterval(() => {
          analyser.getFloatTimeDomainData(samples);
          const rms = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length);
          // 与原生 ASR 采集器使用相同的分贝归一化和音量升降响应。
          const normalized = Math.max(0, Math.min(1, (20 * Math.log10(Math.max(rms, .00001)) + 60) / 60));
          level += (normalized > level ? .85 : .65) * (normalized - level);
          onLevel(level);
        }, 50);
        return true;
      } catch (error) {
        if (ticket === revision) stop();
        throw error;
      }
    },
  };
}
