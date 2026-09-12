import test from 'node:test';
import assert from 'node:assert/strict';
import { spine } from '../../../plugins/optional/sakura_spine/vendor/spine-webgl.mjs';
import { createSpineController } from '../../../plugins/optional/sakura_spine/controller.mjs';

function fixture() {
  const skeletonData = new spine.SkeletonJson({}).readSkeletonData({
    skeleton: { spine: '3.6.53' }, bones: [{ name: 'root' }],
    skins: { default: {}, smile: {} },
    animations: {
      idle: { bones: { root: {
        rotate: [{ time: 0, angle: 0 }, { time: 1, angle: 0 }],
        translate: [{ time: 0, x: 0 }, { time: 1, x: 10 }],
      } } },
      wave: { bones: { root: { rotate: [{ time: 0, angle: 0 }, { time: 0.3, angle: 45 }, { time: 0.6, angle: 0 }] } } },
    },
  });
  const skeleton = new spine.Skeleton(skeletonData);
  const controller = createSpineController(skeleton, { defaultSkin: 'default', defaultAnimation: 'idle', speed: 1 }, { bindingId: 'binding', resourceId: 'resource' });
  const envelope = (state = {}, actions = []) => ({ version: 1, bindingId: 'binding', resourceId: 'resource', state, actions });
  return { skeleton, controller, envelope };
}

test('real Spine runtime executes one-shot animation, keeps skin/speed, and restores its loop', () => {
  const { skeleton, controller, envelope } = fixture();
  assert.equal(controller.applyControl(envelope({ skin: 'smile', speed: 1.5 }, [{ animation: 'wave' }]), { sequence: 1 }), true);
  controller.update(0.1); controller.update(0.1);
  assert.ok(skeleton.bones[0].rotation > 0, 'the action actually animates the bone');
  assert.equal(skeleton.skin.name, 'smile');
  for (let i = 0; i < 10; i++) controller.update(0.1);
  assert.deepEqual(controller.snapshot(), { skin: 'smile', speed: 1.5, animation: 'idle', playing: 'idle', disposed: false });
});

test('duplicate and stale envelopes do not replay actions or mutate the current instance', () => {
  const { controller, envelope } = fixture();
  const action = envelope({}, [{ animation: 'wave' }]);
  assert.equal(controller.applyControl(action, { sequence: 2 }), true);
  for (let i = 0; i < 15; i++) controller.update(0.1);
  assert.equal(controller.applyControl(action, { sequence: 2 }), false);
  assert.equal(controller.applyControl(action, { sequence: 1 }), false);
  assert.equal(controller.applyControl({ ...action, bindingId: 'old' }, { sequence: 3 }), false);
  assert.equal(controller.applyControl({ ...action, resourceId: 'other' }, { sequence: 3 }), false);
  assert.equal(controller.snapshot().playing, 'idle');
});

test('cancellation clears action mixing and destruction rejects further delivery', () => {
  const { skeleton, controller, envelope } = fixture();
  controller.applyControl(envelope({}, [{ animation: 'wave' }]), { sequence: 1 });
  controller.update(0.1); controller.update(0.1);
  controller.cancel();
  assert.equal(controller.snapshot().playing, 'idle');
  assert.equal(skeleton.bones[0].rotation, 0);
  assert.equal(controller.applyControl(envelope({ speed: NaN }), { sequence: 2 }), false);
  assert.equal(controller.applyControl(envelope({ skin: 'missing' }), { sequence: 2 }), false);
  controller.dispose(); controller.dispose();
  assert.equal(controller.applyControl(envelope({}, [{ animation: 'wave' }]), { sequence: 3 }), false);
  controller.update(0.1);
  assert.equal(controller.snapshot().playing, null);
});

test('skin changes preserve loop progress and the speed selected in the editor', () => {
  const { skeleton, controller, envelope } = fixture();
  controller.update(0.1);
  assert.ok(Math.abs(skeleton.bones[0].x - 1) < 0.001);
  controller.applyControl(envelope({ skin: 'smile' }), { sequence: 1 });
  controller.update(0.1);
  assert.ok(Math.abs(skeleton.bones[0].x - 2) < 0.001, 'skin does not restart the idle loop');
  assert.equal(controller.snapshot().speed, 1);
  controller.applyControl(envelope({ speed: 2 }), { sequence: 2 });
  controller.applyControl(envelope({ skin: 'default' }), { sequence: 3 });
  controller.update(0.1);
  assert.ok(Math.abs(skeleton.bones[0].x - 4) < 0.001);
  assert.equal(controller.snapshot().playing, 'idle');
  assert.equal(controller.snapshot().speed, 2);
});

test('expression skins retain the base body and never expose a faceless base-only skin', () => {
  const data = new spine.SkeletonData();
  const bone = new spine.BoneData(0, 'root', null); data.bones.push(bone);
  for (const [index, name] of ['body', 'face'].entries()) {
    const slot = new spine.SlotData(index, name, bone); slot.attachmentName = name; data.slots.push(slot);
  }
  const base = new spine.Skin('default'); base.addAttachment(0, 'body', new spine.RegionAttachment('body'));
  data.defaultSkin = base;
  data.skins = [base, ...['normal', 'smile'].map(name => {
    const skin = new spine.Skin(name); skin.addAttachment(1, 'face', new spine.RegionAttachment(name)); return skin;
  })];
  data.animations.push(new spine.Animation('idle', [], 1));
  const skeleton = new spine.Skeleton(data);
  const controller = createSpineController(skeleton, {defaultSkin:'normal', defaultAnimation:'idle', speed:1, selectableSkins:['normal','smile']}, {bindingId:'b',resourceId:'r'});
  assert.deepEqual(skeleton.slots.map(slot => slot.attachment?.name), ['body','normal']);
  const deliver = skin => controller.applyControl({version:1,bindingId:'b',resourceId:'r',state:{skin}}, {sequence:1});
  assert.equal(deliver('default'), false);
  assert.equal(deliver('smile'), true);
  assert.deepEqual(skeleton.slots.map(slot => slot.attachment?.name), ['body','smile']);
});
