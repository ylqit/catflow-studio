import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import DirectorDraftEditor from './DirectorDraftEditor.vue';

describe('returned director draft editing', () => {
  it('exposes all missing spatial fields and edits them with the correct JSON types without filling other fields', async () => {
    const source = { targetDurationSeconds: 12, directorTreatment: { theme: '既有主题' }, shots: [{
      id: 'shot-1', durationSeconds: 12, durationFrames: 288, childAction: '原动作摘要',
      childBlocking: { initialState: '孩子手持唯一木球', movementPath: '放进篮中', endState: '双手收回' },
    }] };
    const wrapper = mount(DirectorDraftEditor, { props: { modelValue: JSON.stringify(source) } });
    const values = [
      ['机位与空间关系', '摄影机侧拍孩子与篮筐'],
      ['交互约束（每行一项）', '双手握持同一木球\n木球进入篮筐后双手松开'],
      ['画面排除项（每行一项）', '篮外多出第二颗木球\n手与木球融合'],
    ];
    for (const [label] of values) {
      const control = wrapper.get<HTMLTextAreaElement>(`textarea[aria-label="镜头 1 ${label}"]`);
      expect(control.element.value).toBe('');
      expect(control.element.closest('label')!.classList.contains('missing')).toBe(true);
    }
    expect(wrapper.emitted('update:modelValue')).toBeUndefined();
    for (const [label, value] of values) {
      await wrapper.get(`textarea[aria-label="镜头 1 ${label}"]`).setValue(value);
      const edited = wrapper.emitted('update:modelValue')!.at(-1)![0] as string;
      await wrapper.setProps({ modelValue: edited });
    }
    const updated = JSON.parse(wrapper.props('modelValue'));
    expect(updated.shots[0]).toEqual({
      ...source.shots[0], cameraSpatialRelation: '摄影机侧拍孩子与篮筐',
      interactionConstraints: ['双手握持同一木球', '木球进入篮筐后双手松开'],
      visualExclusions: ['篮外多出第二颗木球', '手与木球融合'],
    });
    expect(updated.directorTreatment).toEqual(source.directorTreatment);
    expect(source.shots[0]).not.toHaveProperty('cameraSpatialRelation');
    for (const [label] of values) {
      expect(wrapper.get(`textarea[aria-label="镜头 1 ${label}"]`).element.closest('label')!.classList.contains('missing')).toBe(false);
    }
    wrapper.unmount();
  });

  it('keeps explicitly empty constraint lists distinct from missing fields and removes entries as an empty array', async () => {
    const source = { shots: [{ cameraSpatialRelation: '同侧机位', interactionConstraints: [], visualExclusions: ['多余角色'] }] };
    const wrapper = mount(DirectorDraftEditor, { props: { modelValue: JSON.stringify(source) } });
    const constraints = wrapper.get('textarea[aria-label="镜头 1 交互约束（每行一项）"]');
    expect(constraints.element.closest('label')!.classList.contains('missing')).toBe(false);
    await wrapper.get('textarea[aria-label="镜头 1 画面排除项（每行一项）"]').setValue('');
    const updated = JSON.parse(wrapper.emitted('update:modelValue')!.at(-1)![0] as string);
    expect(updated.shots[0].visualExclusions).toEqual([]);
    expect(updated.shots[0].interactionConstraints).toEqual([]);
    wrapper.unmount();
  });

  it('lets the creator explicitly record no applicable constraints without inventing an entry', async () => {
    const wrapper = mount(DirectorDraftEditor, { props: { modelValue: JSON.stringify({ shots: [{ cameraSpatialRelation: '固定侧拍' }] }) } });
    await wrapper.get('button[aria-label="镜头 1 交互约束（每行一项） 标记为空列表"]').trigger('click');
    const edited = wrapper.emitted('update:modelValue')!.at(-1)![0] as string;
    expect(JSON.parse(edited).shots[0]).toEqual({ cameraSpatialRelation: '固定侧拍', interactionConstraints: [] });
    await wrapper.setProps({ modelValue: edited });
    expect(wrapper.find('button[aria-label="镜头 1 交互约束（每行一项） 标记为空列表"]').exists()).toBe(false);
    expect(wrapper.find('button[aria-label="镜头 1 画面排除项（每行一项） 标记为空列表"]').exists()).toBe(true);
    wrapper.unmount();
  });

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
