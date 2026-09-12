import { spine } from './vendor/spine-webgl.mjs';

// This controller is also exercised against the real runtime without a WebView.
export function createSpineController(skeleton, config, { bindingId, resourceId }) {
  const data = new spine.AnimationStateData(skeleton.data);
  data.defaultMix = 0.15;
  const animationState = new spine.AnimationState(data);
  const current = { skin: config.defaultSkin, animation: config.defaultAnimation, speed: config.speed };
  let lastSequence = -1;
  let disposed = false;
  const validState = (state) => state && typeof state === 'object' && !Array.isArray(state)
    && Object.keys(state).every(key => ['skin', 'animation', 'speed'].includes(key))
    && (state.skin === undefined || Boolean(skeleton.data.findSkin(state.skin)) && (!config.selectableSkins || config.selectableSkins.includes(state.skin)))
    && (state.animation === undefined || Boolean(skeleton.data.findAnimation(state.animation)))
    && (state.speed === undefined || (Number.isFinite(state.speed) && state.speed >= 0.1 && state.speed <= 3));
  function restore(trackTime = 0) {
    animationState.clearTracks();
    skeleton.setToSetupPose();
    skeleton.setSkinByName(current.skin);
    skeleton.setSlotsToSetupPose();
    animationState.setAnimation(0, current.animation, true).trackTime = trackTime;
    animationState.apply(skeleton);
    skeleton.updateWorldTransform();
  }
  if (!validState(current)) throw new Error('SPINE_DEFAULT_INVALID');
  restore();
  return {
    applyControl(control, { sequence } = {}) {
      if (disposed || !Number.isSafeInteger(sequence) || sequence < 0 || sequence <= lastSequence
        || control?.version !== 1 || control.bindingId !== bindingId || control.resourceId !== resourceId) return false;
      const patch = control.state ?? {};
      const actions = control.actions ?? [];
      if (!validState(patch) || !Array.isArray(actions) || actions.length > 1
        || actions.some(action => !action || Object.keys(action).length !== 1
          || typeof action.animation !== 'string' || !skeleton.data.findAnimation(action.animation))) return false;
      lastSequence = sequence;
      const changedLoop = patch.animation !== undefined && patch.animation !== current.animation;
      Object.assign(current, patch);
      if (changedLoop) restore();
      else if (patch.skin !== undefined) {
        skeleton.setSkinByName(current.skin);
        skeleton.setSlotsToSetupPose();
      }
      if (actions.length) {
        // One shot replaces the previous action and returns to the latest loop.
        const entry = animationState.setAnimation(0, actions[0].animation, false);
        animationState.addAnimation(0, current.animation, true, Math.max(entry.animation.duration, 0.001));
      }
      animationState.apply(skeleton);
      skeleton.updateWorldTransform();
      return true;
    },
    update(delta) {
      if (disposed) return;
      animationState.update(Math.max(0, Math.min(delta, 0.1)) * current.speed);
      animationState.apply(skeleton);
      skeleton.updateWorldTransform();
    },
    cancel() {
      if (disposed) return;
      const track = animationState.getCurrent(0);
      if (track?.loop && track.animation.name === current.animation) {
        // Cancelling a history operation must not restart a continuing idle loop.
        if (track.mixingFrom || track.next) restore(track.trackTime);
        return;
      }
      restore();
    },
    snapshot() {
      return { ...current, playing: animationState.getCurrent(0)?.animation.name ?? null, disposed };
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      animationState.clearTracks();
      animationState.clearListeners();
    },
  };
}
