import { spine } from './vendor/spine-webgl.mjs';
import { createSpineController } from './controller.mjs';

export async function createRenderer({ container, rendererData, resolveAssetUrl, bindingId, resourceId,
  signal, onLayout = () => {}, onError = () => {} }) {
  const canvas = document.createElement('canvas');
  canvas.className = 'spine-canvas';
  canvas.setAttribute('aria-label', 'Spine 角色预览');
  Object.assign(canvas.style, { width: '100%', height: '100%', display: 'block' });
  const abort = new AbortController();
  const textures = new Map();
  const bitmaps = [];
  let shader, batcher, controller, observer, frame = 0, disposed = false, paused = false;
  let gl, lastTime, draw;
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    abort.abort();
    signal?.removeEventListener('abort', freeze);
    observer?.disconnect();
    cancelAnimationFrame(frame);
    canvas.removeEventListener('webglcontextlost', contextLost);
    controller?.dispose();
    for (const texture of textures.values()) texture.dispose();
    for (const bitmap of bitmaps) bitmap.close();
    batcher?.dispose();
    shader?.dispose();
    gl?.getExtension('WEBGL_lose_context')?.loseContext();
    canvas.remove();
  };
  function contextLost(event) {
    event.preventDefault();
    dispose();
    onError(new Error('SPINE_WEBGL_CONTEXT_LOST'));
  }
  function checkActive() {
    if (disposed || signal?.aborted) throw new DOMException('Spine loading cancelled', 'AbortError');
  }
  function freeze() {
    abort.abort();
    paused = true;
    cancelAnimationFrame(frame);
    observer?.disconnect();
  }
  async function read(relative, method) {
    checkActive();
    const url = await resolveAssetUrl(relative);
    checkActive();
    const response = await fetch(url, { signal: abort.signal });
    if (!response.ok) throw new Error('SPINE_ASSET_LOAD_FAILED');
    return response[method]();
  }
  signal?.addEventListener('abort', freeze, { once: true });
  try {
    checkActive();
    if (!/^3\.6\.\d+$/.test(rendererData.runtimeVersion)) throw new Error('SPINE_VERSION_UNSUPPORTED');
    const { config } = rendererData;
    const [json, atlasText] = await Promise.all([read(config.skeleton, 'json'), read(config.atlas, 'text')]);
    checkActive();
    if (!/^3\.6\.\d+$/.test(json.skeleton?.spine)) throw new Error('SPINE_VERSION_UNSUPPORTED');
    gl = canvas.getContext('webgl', { alpha: true, premultipliedAlpha: true, antialias: true });
    if (!gl) throw new Error('SPINE_WEBGL_UNAVAILABLE');
    canvas.addEventListener('webglcontextlost', contextLost);
    const context = new spine.webgl.ManagedWebGLRenderingContext(gl);
    for (const [page, path] of Object.entries(rendererData.textures)) {
      const blob = await read(path, 'blob');
      const bitmap = await createImageBitmap(blob, { premultiplyAlpha: config.premultipliedAlpha ? 'none' : 'premultiply', colorSpaceConversion: 'none' });
      if (disposed || signal?.aborted) { bitmap.close(); checkActive(); }
      bitmaps.push(bitmap);
      if (Math.max(bitmap.width, bitmap.height) > gl.getParameter(gl.MAX_TEXTURE_SIZE)) throw new Error('SPINE_TEXTURE_TOO_LARGE');
      textures.set(page, new spine.webgl.GLTexture(context, bitmap));
    }
    const atlas = new spine.TextureAtlas(atlasText, page => {
      if (!textures.has(page)) throw new Error('SPINE_ATLAS_TEXTURE_MISSING');
      return textures.get(page);
    });
    const skeleton = new spine.Skeleton(new spine.SkeletonJson(new spine.AtlasAttachmentLoader(atlas)).readSkeletonData(json));
    controller = createSpineController(skeleton, config, { bindingId, resourceId });
    const offset = new spine.Vector2(), size = new spine.Vector2();
    skeleton.getBounds(offset, size, []);
    if (![offset.x, offset.y, size.x, size.y].every(Number.isFinite) || size.x <= 0 || size.y <= 0) throw new Error('SPINE_BOUNDS_INVALID');
    shader = spine.webgl.Shader.newTwoColoredTextured(context);
    batcher = new spine.webgl.PolygonBatcher(context);
    const painter = new spine.webgl.SkeletonRenderer(context);
    // Both source encodings reach the GPU as premultiplied colors. This also
    // keeps framebuffer alpha correct when translucent slots overlap.
    painter.premultipliedAlpha = true;
    const matrix = new spine.webgl.Matrix4();
    const centerX = offset.x + size.x / 2, centerY = offset.y + size.y / 2;
    const maxViewport = gl.getParameter(gl.MAX_VIEWPORT_DIMS);
    const maxBuffer = gl.getParameter(gl.MAX_RENDERBUFFER_SIZE);
    let layoutWidth, layoutHeight;
    function updateSize() {
      const width = Math.max(1, container.clientWidth), height = Math.max(1, container.clientHeight);
      // The host scales the surface and its ancestors with CSS transforms.
      // ResizeObserver sees only layout size; sample the displayed size and DPR
      // before drawing so zoom and monitor changes cannot stretch a stale buffer.
      const rect = canvas.getBoundingClientRect();
      const pixelRatio = window.devicePixelRatio || 1;
      const desiredWidth = Math.max(1, Math.ceil(rect.width * pixelRatio));
      const desiredHeight = Math.max(1, Math.ceil(rect.height * pixelRatio));
      // Bound GPU allocation for unusually large windows without imposing a
      // fixed DPR ceiling on ordinary high-density displays.
      const limit = Math.min(1, maxViewport[0] / desiredWidth, maxViewport[1] / desiredHeight,
        maxBuffer / desiredWidth, maxBuffer / desiredHeight,
        Math.sqrt(16_777_216 / (desiredWidth * desiredHeight)));
      const bufferWidth = Math.max(1, Math.floor(desiredWidth * limit));
      const bufferHeight = Math.max(1, Math.floor(desiredHeight * limit));
      if (canvas.width !== bufferWidth || canvas.height !== bufferHeight) {
        if (canvas.width !== bufferWidth) canvas.width = bufferWidth;
        if (canvas.height !== bufferHeight) canvas.height = bufferHeight;
        gl.viewport(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight);
      }
      if (layoutWidth !== width || layoutHeight !== height) {
        layoutWidth = width; layoutHeight = height;
        const scale = Math.max(size.x / width, size.y / height) * 1.16;
        const worldWidth = width * scale, worldHeight = height * scale;
        matrix.ortho2d(centerX - worldWidth / 2, centerY - worldHeight / 2, worldWidth, worldHeight);
        onLayout({ width, height, bounds: { x: (width - size.x / scale) / 2, y: (height - size.y / scale) / 2,
          width: size.x / scale, height: size.y / scale } });
      }
    }
    function resize() {
      draw?.();
    }
    draw = () => {
      if (disposed) return;
      updateSize();
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      shader.bind();
      shader.setUniformi(spine.webgl.Shader.SAMPLER, 0);
      shader.setUniform4x4f(spine.webgl.Shader.MVP_MATRIX, matrix.values);
      batcher.begin(shader);
      painter.draw(batcher, skeleton);
      batcher.end();
      shader.unbind();
    };
    function tick(time) {
      if (disposed || paused) return;
      try {
        controller.update(lastTime === undefined ? 0 : (time - lastTime) / 1000);
        lastTime = time;
        draw();
        frame = requestAnimationFrame(tick);
      } catch (error) { dispose(); onError(error); }
    }
    container.append(canvas);
    observer = new ResizeObserver(resize);
    observer.observe(container);
    resize();
    frame = requestAnimationFrame(tick);
    return {
      size: { width: Math.ceil(size.x * 1.16), height: Math.ceil(size.y * 1.16) },
      applyControl(control, delivery) { const applied = controller.applyControl(control, delivery); draw(); return applied; },
      cancel() { controller.cancel(); draw(); },
      resize,
      setPaused(value) {
        if (disposed || signal?.aborted || paused === Boolean(value)) return;
        paused = Boolean(value);
        cancelAnimationFrame(frame);
        lastTime = undefined;
        if (!paused) frame = requestAnimationFrame(tick);
      },
      snapshot: () => controller.snapshot(),
      capture() {
        if (disposed || signal?.aborted) return null;
        draw();
        const image = document.createElement('canvas');
        const ratio = Math.min(1, 256 / Math.max(canvas.width, canvas.height));
        image.width = Math.max(1, Math.round(canvas.width * ratio));
        image.height = Math.max(1, Math.round(canvas.height * ratio));
        image.getContext('2d').drawImage(canvas, 0, 0, image.width, image.height);
        return image.toDataURL('image/png');
      },
      dispose,
    };
  } catch (error) { dispose(); throw error; }
}

// Public RendererHost owns operation/segment deduplication. The private runtime
// receives a local sequence for its separate state and action deliveries.
export async function mount({ container, resource, host, signal }) {
  const renderer = await createRenderer({ container, rendererData: resource.data,
    bindingId: resource.bindingId, resourceId: resource.resourceId, signal,
    resolveAssetUrl(path) {
      if (!Object.hasOwn(resource.assets, path)) throw new Error('SPINE_ASSET_LOAD_FAILED');
      return resource.assets[path];
    },
    onError: error => host.unavailable('SPINE_RENDERER_FAILED', error),
  });
  let sequence = 0;
  const deliver = (state, actions, context) => {
    if (signal.aborted || context.signal.aborted) return;
    if (!renderer.applyControl({ version: 1, bindingId: resource.bindingId,
      resourceId: resource.resourceId, state, actions }, { sequence: sequence++ })) {
      throw new Error('SPINE_CONTROL_INVALID');
    }
  };
  const ratio = Math.min(1, 8192 / Math.max(renderer.size.width, renderer.size.height));
  return {
    ready: Promise.resolve().then(async () => {
      if (signal.aborted) return;
      const accepted = await host.setSurface({ width: Math.max(1, Math.round(renderer.size.width * ratio)),
        height: Math.max(1, Math.round(renderer.size.height * ratio)) });
      if (!signal.aborted && accepted === false) throw new Error('SPINE_SURFACE_REJECTED');
    }),
    snapshotState() {
      const { skin, animation, speed } = renderer.snapshot();
      return { skin, animation, speed };
    },
    applyState: (state, context) => deliver(state, [], context),
    perform: (action, context) => deliver({}, [action], context),
    cancel() { if (!signal.aborted) renderer.cancel(); },
    destroy: () => renderer.dispose(),
  };
}
