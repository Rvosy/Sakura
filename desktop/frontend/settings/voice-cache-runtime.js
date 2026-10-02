const clone = (value) => JSON.parse(JSON.stringify(value));
const stable = (value) => JSON.stringify(value);

function read(document) {
  const maxMegabytes = Number(document.getElementById("voiceCacheMaxMegabytes").value.trim());
  return {
    directory: document.getElementById("voiceCacheDirectory").value.trim(),
    maxMegabytes,
    idleFill: document.getElementById("voiceCacheIdleFill").checked,
  };
}

function fill(document, values) {
  document.getElementById("voiceCacheDirectory").value = values.directory;
  document.getElementById("voiceCacheMaxMegabytes").value = String(values.maxMegabytes);
  document.getElementById("voiceCacheIdleFill").checked = values.idleFill;
}

export function createVoiceCacheController({ document, invoke, onDirty }) {
  let baseline = null;
  let draft = null;
  let disposed = false;

  function changed() {
    if (disposed || !baseline) return;
    draft = read(document);
    onDirty();
  }

  return Object.freeze({
    initialize(snapshot) {
      baseline = {
        directory: snapshot.directory,
        maxMegabytes: snapshot.maxMegabytes,
        idleFill: snapshot.idleFill === true,
      };
      draft = clone(baseline);
      fill(document, draft);
      document.getElementById("voiceCacheDirectory").addEventListener("input", changed);
      document.getElementById("voiceCacheMaxMegabytes").addEventListener("input", changed);
      document.getElementById("voiceCacheIdleFill").addEventListener("change", changed);
      onDirty();
    },
    isDirty: () => Boolean(baseline && stable(draft) !== stable(baseline)),
    async save() {
      if (!baseline) throw new Error("语音缓存设置尚未加载");
      draft = read(document);
      if (!Number.isSafeInteger(draft.maxMegabytes) || draft.maxMegabytes <= 0) {
        throw new Error("语音缓存容量须为正整数");
      }
      const saved = await invoke("settings_voice_cache_save", {
        request: {
          directory: draft.directory,
          maxMegabytes: draft.maxMegabytes,
          idleFill: draft.idleFill,
        },
      });
      baseline = {
        directory: saved.directory,
        maxMegabytes: saved.maxMegabytes,
        idleFill: saved.idleFill === true,
      };
      draft = clone(baseline);
      fill(document, draft);
      onDirty();
      return saved;
    },
    discard() {
      if (!baseline) return;
      draft = clone(baseline);
      fill(document, draft);
      onDirty();
    },
    dispose() {
      disposed = true;
    },
  });
}
