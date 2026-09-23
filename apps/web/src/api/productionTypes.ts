import type { components } from '@catflow/contracts';
import type { ShotSpecDto } from './types';

export type NarrativeDesign = components['schemas']['NarrativeDesign'];
export type ShotInformation = components['schemas']['ShotInformation'];
export type SceneBinding = components['schemas']['SceneBinding'];
export type PropBinding = components['schemas']['PropBinding'];
export type ProductionUnit = components['schemas']['ProductionUnit'];
export type ProductionPlanDraft = components['schemas']['ProductionPlanDraft'];
export type UnitSelectionCommand = components['schemas']['UnitSelectionCommand'];
export type UnitSelection = components['schemas']['UnitSelectionDto'] & { document: UnitSelectionCommand & {
  unitDesignHash: string; assetSha256: string; upstreamSelectionHash: string | null;
}};
export type ProductionDocument = {
  shots: ShotSpecDto[]; units: ProductionUnit[]; scenes: SceneBinding[]; props: PropBinding[];
  targetDurationFrames: number; narrativeDesign: NarrativeDesign | null;
};
export type ProductionPlan = Omit<components['schemas']['ProductionPlanDto'], 'document'> & { document: ProductionDocument };
export type UnitPreview = {
  inputHash: string; unitDesignHash: string; upstreamSelectionHash: string | null;
  prompt: string; compiledProviderPrompt: string; durationSeconds: number; targetDurationFrames: number;
  references: Array<{ assetId: string; sha256: string; role: string; duties: string[] }>;
  upstreamReferences: UnitPreview['references']; warnings: Array<{ code: string; message: string }>;
  keyEvents: Array<{ id: string; shotId: string; description: string; unitStartFrame: number; unitEndFrame: number }>;
};
