import { reactive } from 'vue';

// Bootstrap updates this shared capability boundary; forms never choose provider limits.
export const productionCapabilities = reactive({
  revision: 'catflow-production-v1', minimumWorkSeconds: 8, maximumWorkSeconds: 60,
  frameRate: 24, minimumShotFrames: 24, maximumShotFrames: 360, maximumShots: 24,
  minimumGenerationSeconds: 4, maximumGenerationSeconds: 15, maximumUnitShots: 4,
});
