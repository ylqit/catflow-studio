import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import ActionBeatsEditor from './ActionBeatsEditor.vue';
import type { ActionBeatDto } from '../../api/types';

const beat: ActionBeatDto = {
  startFrame: 0, endFrame: 96, purpose: 'reaction', childAction: '移回风中', catAction: '抬头回应',
  visibleChange: '风车重新转动', catPerformance: { visibility: 'visible', gazeFrom: '风车', gazeTo: '孩子', eyelidAction: 'blink' },
};

describe('节拍与表演', () => {
  it('preserves historical absence until explicitly added and does not invent actions', async () => {
    const wrapper = mount(ActionBeatsEditor, { props: { durationFrames: 96, initialChildAction: '原动作' } });
    expect(wrapper.emitted('update:modelValue')).toBeUndefined();
    expect(wrapper.text()).toContain('历史分镜尚无节拍设计');
    await wrapper.get('button').trigger('click');
    const added = wrapper.emitted('update:modelValue')![0][0] as ActionBeatDto[];
    expect(added).toEqual([{ startFrame: 0, endFrame: 96, purpose: 'action', childAction: '原动作', catAction: '', visibleChange: '', catPerformance: null }]);
  });

  it('edits gaze and eyelids without altering timing or the source prop', async () => {
    const wrapper = mount(ActionBeatsEditor, { props: { modelValue: [structuredClone(beat)], durationFrames: 96 } });
    await wrapper.get('select[aria-label="节拍 1 眼睑动作"]').setValue('slow_blink');
    const changed = wrapper.emitted('update:modelValue')![0][0] as ActionBeatDto[];
    expect(changed[0]).toEqual({ ...beat, catPerformance: { ...beat.catPerformance!, eyelidAction: 'slow_blink' } });
    expect(wrapper.props('modelValue')![0].catPerformance!.eyelidAction).toBe('blink');
  });

  it('splits locally without extending the shot and reports gaps in content or invalid ranges', async () => {
    const wrapper = mount(ActionBeatsEditor, { props: { modelValue: [structuredClone(beat)], durationFrames: 96 } });
    await wrapper.findAll('button').find(b => b.text().includes('拆分'))!.trigger('click');
    const changed = wrapper.emitted('update:modelValue')![0][0] as ActionBeatDto[];
    expect(changed.map(b => [b.startFrame, b.endFrame])).toEqual([[0, 48], [48, 96]]);
    await wrapper.setProps({ modelValue: changed });
    expect(wrapper.get('[role="alert"]').text()).toContain('请填写动作和可见变化');
    await wrapper.setProps({ modelValue: [{ ...beat, endFrame: 100 }] });
    expect(wrapper.get('[role="alert"]').text()).toContain('越界');
    await wrapper.setProps({ disabled: true });
    expect(wrapper.get('fieldset').attributes('disabled')).toBeDefined();
  });
});
