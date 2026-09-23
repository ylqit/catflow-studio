import { flushPromises, mount } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ProductionWorkspace from './ProductionWorkspace.vue';
import ShotNarrativeEditor from './ShotNarrativeEditor.vue';
import ActionBeatsEditor from './ActionBeatsEditor.vue';
import { api } from '../../api/client';
import type { WorkspaceDto, ShotSpecDto } from '../../api/types';
vi.mock('vue-router',()=>({useRouter:()=>({push:vi.fn()})}));
vi.mock('../../api/client',()=>({api:{request:vi.fn(),json:vi.fn(),assets:vi.fn()}}));
const shot:ShotSpecDto={id:'s1',order:1,formatVersion:2,durationSeconds:3,durationFrames:72,framing:'特写',cameraMovement:'固定',childAction:'',catAction:'',environmentChange:'纸箱形变',transition:'hard_cut',information:{role:'reveal',newInformation:'箱里有猫',visibleSubjects:['prop'],sceneKey:'main',propKeys:['box'],narrativeLink:'揭示',keyEvents:[],isolateGeneration:false}};
beforeEach(()=>{vi.clearAllMocks();sessionStorage.clear();vi.mocked(api.request).mockResolvedValue([]);vi.mocked(api.assets).mockResolvedValue([]);});
describe('统一生产界面',()=>{
  it('restores editing choices without generating or reanalysing a story',async()=>{
    sessionStorage.setItem('catflow:production-draft:p',JSON.stringify({shotPlanVersionId:'s',scenes:[],props:[],units:[{id:'u1',shotIds:['s1'],continuity:'reset',reason:'新的时空',generationMode:'references'}],idempotencyKey:'saved-draft'}));
    const wrapper=mount(ProductionWorkspace,{props:{projectId:'p',workspace:{activeShotPlan:{id:'s',revision:1},activeStory:{revision:1}} as unknown as WorkspaceDto},global:{stubs:{ProductionAssetsEditor:true,ProductionUnitCard:true}}});
    await flushPromises();expect(wrapper.text()).toContain('s1');
    expect(wrapper.find('input').element.value).toBe('新的时空');expect(api.json).not.toHaveBeenCalled();
  });
  it('lets a prop shot retain empty character actions and frame-accurate time',async()=>{
    const item=structuredClone(shot);const wrapper=mount(ShotNarrativeEditor,{props:{shot:item}});
    await wrapper.get('input[type=number]').setValue(49);
    expect(item.durationFrames).toBe(49);expect(item.durationSeconds).toBe(49/24);
    expect(item.childAction).toBe('');expect(item.catAction).toBe('');
  });
  it('accepts visible prop action without invented offscreen actor motion',()=>{
    const wrapper=mount(ActionBeatsEditor,{props:{durationFrames:72,modelValue:[{startFrame:0,endFrame:72,purpose:'reaction',childAction:'',catAction:'',environmentAction:'纸箱轻鼓',visibleChange:'箱壁形变'}]}});
    expect(wrapper.find('[role=alert]').exists()).toBe(false);
  });
});
