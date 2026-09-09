import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import DirectorDraftEditor from './DirectorDraftEditor.vue';

describe('returned director draft editing', () => {
  it('edits one missing role state without inventing or replacing other returned content', async () => {
    const source = { targetDurationSeconds: 15, directorTreatment: { theme: '既有主题' }, shots: [{
      durationSeconds: 3, durationFrames: 72, childBlocking: { initialState: '孩子坐下', movementPath: '孩子伸手', endState: '孩子收手' },
      catBlocking: { initialState: '猫在右侧', movementPath: '猫靠近' },
    }] };
    const wrapper = mount(DirectorDraftEditor, { props: { modelValue: JSON.stringify(source) } });
    expect(wrapper.text()).toContain('镜头 1 · 已返回内容');
    await wrapper.get('textarea[aria-label="镜头 1 猫咪动作 → 结束状态"]').setValue('猫停在包口');
    const updated = JSON.parse(wrapper.emitted('update:modelValue')![0][0] as string);
    expect(updated.shots[0].catBlocking.endState).toBe('猫停在包口');
    expect(updated.shots[0].childBlocking).toEqual(source.shots[0].childBlocking);
    expect(updated.directorTreatment).toEqual(source.directorTreatment);
    expect(updated.shots[0].durationFrames).toBe(72);
    expect(updated.shots[0].sound).toBeUndefined();
  });
  it('keeps invalid JSON available for correction', () => {
    const wrapper = mount(DirectorDraftEditor, { props: { modelValue: '{invalid' } });
    expect(wrapper.text()).toContain('正文暂时不能解析');
    expect(wrapper.get('textarea').element.value).toBe('{invalid');
    expect(wrapper.emitted('update:modelValue')).toBeUndefined();
  });
});
