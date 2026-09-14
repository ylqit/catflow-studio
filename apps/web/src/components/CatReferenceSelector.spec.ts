import { flushPromises, mount } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import CatReferenceSelector from './CatReferenceSelector.vue';
const client = vi.hoisted(() => ({ catReferenceOptions: vi.fn() }));
vi.mock('../api/client', () => ({ api: client }));
const options = ['gray-original', 'white-v4'].map((key, index) => ({ key, label: index ? 'V4 校色白猫' : '原版灰猫', canonProfileId: key, available: true, fixedAssets: { episode_cat: { id: key + '-cat' }, pair_scale: { id: key + '-pair' } }, auxiliary: [], catIdentity: key, version: 7 }));
describe('CatReferenceSelector', () => {
  beforeEach(() => { client.catReferenceOptions.mockResolvedValue(options); });
  it('defaults explicitly to gray and switches the whole option without generation', async () => {
    const wrapper = mount(CatReferenceSelector);
    await flushPromises();
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual(['gray-original']);
    await wrapper.findAll('button')[1].trigger('click');
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual(['white-v4']);
    expect(wrapper.findAll('a')[1].attributes('href')).toContain('white-v4-pair');
  });
  it('restores white and never silently falls back when it is unavailable', async () => {
    client.catReferenceOptions.mockResolvedValue([options[0], { ...options[1], available: false, unavailableReason: '文件缺失' }]);
    const wrapper = mount(CatReferenceSelector, { props: { modelValue: 'white-v4' } });
    await flushPromises();
    expect(wrapper.emitted('update:modelValue')).toBeUndefined();
    expect(wrapper.emitted('ready')?.[0]).toEqual([false]);
    expect(wrapper.text()).toContain('文件缺失');
  });
});
