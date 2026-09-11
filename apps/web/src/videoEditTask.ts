import type { JobDto, SegmentRepairPreviewDto, SegmentRepairPreviewCommand } from './api/types';

export function blocksVideoEditing(job: JobDto): boolean {
  if (job.kind !== 'regenerate_video_segment' || ['succeeded', 'failed', 'cancelled'].includes(job.status)) return false;
  return job.status !== 'submission_unknown' || !job.successorJobIds?.length;
}

// Frozen jobs predate the current preview schema. Normalize field names only;
// never fill individual fields from the live editor or current project choices.
export function frozenVideoEdit(job: JobDto | undefined, legacy: SegmentRepairPreviewDto | undefined): SegmentRepairPreviewDto | null {
  if (!job?.frozenInput || !Object.keys(job.frozenInput).length) return legacy ?? null;
  const input = job.frozenInput;
  if (!input.issueRange || !input.generationRange || !input.candidateCoreRange) return null;
  return {
    ...input, provider: job.provider, model: job.model, inputHash: job.inputHash,
    providerDurationSeconds: input.durationSeconds ?? input.providerDurationSeconds,
    imageReferences: input.imageReferences ?? [], warnings: input.warnings ?? [],
  } as unknown as SegmentRepairPreviewDto;
}

export function videoEditReuseInput(snapshot: SegmentRepairPreviewDto): Partial<SegmentRepairPreviewCommand> & { plannerJobId?: string } {
  const roles = ['episode_child', 'episode_cat', 'pair_scale', 'environment', 'style_board'];
  return {
    ...snapshot,
    // Job referenceRoles records SDK order, which also contains anchors.
    referenceRoles: snapshot.imageReferences.filter(item => roles.includes(item.role)).map(item => item.role) as SegmentRepairPreviewCommand['referenceRoles'],
    plannerJobId: snapshot.planSourceJobId ?? undefined,
  };
}
