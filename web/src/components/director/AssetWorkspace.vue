<script setup lang="ts">
import { Check, Close, Collection, Filter, Lock, MagicStick, Picture, Refresh, Search, Warning } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { api, canvasApi } from "../../api/client";
import type {
  AssetReviewActionDto,
  CanvasAssetHistoryDto,
  CharacterDesignAssetContextDto,
  EpisodeVisualProfileDto,
  ReferenceAuthorityDto,
  SubjectReferenceDto,
  VisualPresetProfileDto,
  VisualProfileDraft,
} from "../../api/types";
import type { DirectorDirtyRegistration } from "./directorDirtyState";

type WorkspaceState = "loading" | "ready" | "stale" | "error";
type AssetCategory = "all" | "child_canon" | "cat_canon" | "episode_child" | "episode_cat" | "pair_scale" | "environment" | "style_source" | "style_board" | "other";
interface MediaItem {
  id: string;
  title: string;
  category: Exclude<AssetCategory, "all">;
  contentUrl: string;
  semanticKey: string;
  status: string;
  authority?: ReferenceAuthorityDto | null;
  metadata: Record<string, unknown>;
  createdAt?: string | null;
  source: "project" | "profile" | "preset";
  characterDesign?: CharacterDesignAssetContextDto | null;
  reviewAction?: AssetReviewActionDto | null;
}
interface AssetLoadSnapshot {
  assets?: CanvasAssetHistoryDto[];
  profile?: EpisodeVisualProfileDto;
  presets?: VisualPresetProfileDto[];
  warnings: string[];
  successfulReads: number;
}
interface ProviderEligibility {
  eligible: boolean;
  reason: string;
}

const props = withDefaults(defineProps<{
  projectId: string;
  focusedItemId?: string;
  panel?: string;
}>(), { focusedItemId: "", panel: "main" });
const emit = defineEmits<{
  "dirty-change": [registration?: DirectorDirtyRegistration];
}>();
const router = useRouter();

const state = ref<WorkspaceState>("loading");
const error = ref("");
const loadWarnings = ref<string[]>([]);
const assets = ref<CanvasAssetHistoryDto[]>([]);
const profile = ref<EpisodeVisualProfileDto>();
const presets = ref<VisualPresetProfileDto[]>([]);
const selectedId = ref("");
const batchSelection = ref<string[]>([]);
const inspectorOpen = ref(true);
const mobilePanel = ref<"categories" | "board" | "inspector">("board");
const categoriesPanel = ref<HTMLElement>();
const boardPanel = ref<HTMLElement>();
const inspectorPanel = ref<HTMLElement>();
const activeCategory = ref<AssetCategory>("all");
const query = ref("");
const saving = ref(false);
const generating = ref(false);
const saveState = ref<"saved" | "dirty" | "saving" | "conflict" | "error">("saved");
const form = reactive<VisualProfileDraft>({
  personIdentity: "",
  personHair: "",
  personBody: "",
  catIdentity: "",
  stylePositive: [],
  styleNegative: [],
  referenceBindings: [],
});
const positiveText = ref("");
const negativeText = ref("");
let requestSequence = 0;
let activeController: AbortController | undefined;
let reconciliationController: AbortController | undefined;
let pendingSnapshot: AssetLoadSnapshot | undefined;
let generationAttempt: { fingerprint: string; idempotencyKey: string } | undefined;

const characterDesignPresentation = {
  child: { category: "episode_child", semanticRole: "appearance" },
  cat: { category: "episode_cat", semanticRole: "pose" },
  pair_scale: { category: "pair_scale", semanticRole: "scale" },
} as const satisfies Record<CharacterDesignAssetContextDto["slot"], {
  category: MediaItem["category"];
  semanticRole: string;
}>;

const categories: Array<{ id: AssetCategory; title: string }> = [
  { id: "all", title: "全部资产" },
  { id: "child_canon", title: "儿童 Canon" },
  { id: "cat_canon", title: "猫咪 Canon" },
  { id: "episode_child", title: "本集儿童设计" },
  { id: "episode_cat", title: "本集猫咪设计" },
  { id: "pair_scale", title: "人猫同框比例" },
  { id: "environment", title: "环境参考" },
  { id: "style_source", title: "画风来源" },
  { id: "style_board", title: "净化画风板" },
  { id: "other", title: "其他非权威图片" },
];

function authorityFromMetadata(metadata: Record<string, unknown>): ReferenceAuthorityDto | null {
  const raw = metadata.authority;
  if (!raw || typeof raw !== "object") return null;
  const value = raw as Partial<ReferenceAuthorityDto>;
  if (!value.role || typeof value.providerEligible !== "boolean") return null;
  return {
    role: value.role,
    providerEligible: value.providerEligible,
    priority: Number(value.priority ?? 0),
    lockedTraits: Array.isArray(value.lockedTraits) ? value.lockedTraits.map(String) : [],
    mutableTraits: Array.isArray(value.mutableTraits) ? value.mutableTraits.map(String) : [],
    forbiddenTransfer: Array.isArray(value.forbiddenTransfer) ? value.forbiddenTransfer.map(String) : [],
  };
}

function inferCategory(semanticKey: string, role: string, authority?: ReferenceAuthorityDto | null): MediaItem["category"] {
  const value = `${semanticKey} ${role}`.toLowerCase();
  if (authority?.role === "style_source" || value.includes("style_source") || value.includes("leaf_material")) return "style_source";
  if (authority?.role === "style_board" || value.includes("style_board") || value.includes("healing_line_texture_v4")) return "style_board";
  if (authority?.role === "pair_scale" || value.includes("pair_scale") || value.includes("pair-scale") || value.includes("pair:")) return "pair_scale";
  if (authority?.role === "environment" || /environment|scene_reference|scene:/.test(value)) return "environment";
  if (/episode.*child|character_design_child|child_design/.test(value)) return "episode_child";
  if (/episode.*cat|character_design_cat|cat_design/.test(value)) return "episode_cat";
  if (/^(cat:|cat_identity\b)|\bcat_identity\b|猫咪/.test(value)) return "cat_canon";
  if (/^(person:|child:|person_identity\b|child_identity\b)|\b(person_identity|child_identity)\b|儿童/.test(value)) return "child_canon";
  return "other";
}

function referenceItem(reference: SubjectReferenceDto, source: MediaItem["source"]): MediaItem | null {
  if (!reference.assetId || !(reference.contentUrl || reference.thumbnailUrl)) return null;
  return {
    id: reference.assetId,
    title: reference.title,
    category: inferCategory(reference.semanticKey, reference.semanticRole ?? "", reference.authority),
    contentUrl: reference.thumbnailUrl || reference.contentUrl || "",
    semanticKey: reference.semanticKey,
    status: reference.approvalStatus,
    authority: reference.authority,
    metadata: {
      instruction: reference.instruction,
      required: reference.required,
      visualProfileRevisionId: reference.visualProfileRevisionId,
      authorityOrigin: reference.authorityOrigin ?? (source === "preset" ? "preset" : undefined),
      currentAuthority: reference.currentAuthority,
      subjectId: reference.subjectId,
      subjectRevisionId: reference.subjectRevisionId,
      subjectRevision: reference.subjectRevision,
      subjectKind: reference.subjectKind,
      subjectRole: reference.subjectRole,
    },
    source,
    reviewAction: {
      executable: false,
      route: "readonly",
      targetId: reference.assetId,
      disabledReason: source === "preset" ? "预设资产只读，请查看版本或应用预设" : "当前视觉档案绑定只读",
    },
  };
}

const media = computed<MediaItem[]>(() => {
  const byId = new Map<string, MediaItem>();
  for (const preset of presets.value) {
    for (const slot of preset.slots) {
      const item = referenceItem(slot, "preset");
      if (item) byId.set(item.id, item);
    }
  }
  for (const reference of profile.value?.references ?? []) {
    const item = referenceItem(reference, "profile");
    if (item) byId.set(item.id, item);
  }
  for (const asset of assets.value) {
    if (asset.mediaType !== "image" || !asset.contentUrl) continue;
    const authority = authorityFromMetadata(asset.metadata);
    const existing = byId.get(asset.id);
    const effectiveAuthority = existing?.authority ?? authority;
    byId.set(asset.id, {
      id: asset.id,
      title: String(asset.metadata.title ?? asset.metadata.displayName ?? existing?.title ?? asset.semanticKey ?? asset.role),
      category: asset.characterDesign
        ? characterDesignPresentation[asset.characterDesign.slot].category
        : inferCategory(asset.semanticKey ?? "", asset.role, effectiveAuthority),
      contentUrl: asset.contentUrl,
      semanticKey: asset.semanticKey ?? existing?.semanticKey ?? "",
      status: asset.status,
      // A current Visual Profile/SubjectRevision binding owns reference authority.
      // Free-form asset metadata may supplement an unbound asset, but must never
      // relabel a bound identity as style/environment and bypass Canon checks.
      authority: effectiveAuthority,
      metadata: { ...asset.metadata, ...(existing?.metadata ?? {}) },
      createdAt: asset.createdAt,
      source: "project",
      characterDesign: asset.characterDesign,
      reviewAction: asset.reviewAction ?? existing?.reviewAction,
    });
  }
  return [...byId.values()];
});
const filteredMedia = computed(() => {
  const normalized = query.value.trim().toLocaleLowerCase();
  return media.value.filter((item) => (
    (activeCategory.value === "all" || item.category === activeCategory.value)
    && (!normalized || `${item.title} ${item.semanticKey} ${item.category}`.toLocaleLowerCase().includes(normalized))
  ));
});
const selected = computed(() => media.value.find((item) => item.id === selectedId.value));
const selectedBatchItems = computed(() => media.value.filter((item) => batchSelection.value.includes(item.id)));
const pendingBatchReviewCount = computed(() => selectedBatchItems.value.filter(
  (item) => item.status !== "approved" && item.reviewAction?.executable,
).length);
const existingBatchCount = computed(() => new Set(
  assets.value.map((item) => String(item.metadata.generationBatchId ?? item.metadata.batchId ?? "")).filter(Boolean),
).size);
const dirty = computed(() => Boolean(profile.value) && (
  form.personIdentity !== profile.value!.personIdentity
  || form.personHair !== profile.value!.personHair
  || form.personBody !== profile.value!.personBody
  || form.catIdentity !== profile.value!.catIdentity
  || positiveText.value !== profile.value!.stylePositive.join("\n")
  || negativeText.value !== profile.value!.styleNegative.join("\n")
));
const currentProfileReferences = computed(() => new Map(
  (profile.value?.references ?? []).filter((reference) => reference.assetId).map((reference) => [reference.assetId!, reference]),
));
const currentProfileBoundAssetIds = computed(() => new Set([
  ...currentProfileReferences.value.keys(),
  ...(profile.value?.referenceBindings ?? []).map((binding) => binding.assetId),
]));

function providerEligibility(item: MediaItem): ProviderEligibility {
  const currentReference = currentProfileReferences.value.get(item.id);
  const effectiveAuthority = currentReference?.authority ?? item.authority;
  if (item.category === "style_source" || effectiveAuthority?.role === "style_source") {
    return { eligible: false, reason: "只用于画风提炼，不会提交 Provider" };
  }
  if (item.characterDesign) {
    const design = item.characterDesign;
    const expected = characterDesignPresentation[design.slot];
    if (item.category !== expected.category || design.semanticRole !== expected.semanticRole) {
      return { eligible: false, reason: "角色设计槽位或语义职责不匹配" };
    }
    if (item.status !== "approved") {
      return { eligible: false, reason: `审核状态为 ${item.status}，仅已批准资产可提交` };
    }
    if (!design.isCurrentRevision) {
      return { eligible: false, reason: "不是当前角色设计 Revision" };
    }
    if (!design.selected) {
      return { eligible: false, reason: "未被当前槽位选中" };
    }
    return { eligible: true, reason: `当前角色设计 R${design.revision} 已选中，可按 ${design.slot} 职责提交 Provider` };
  }
  if (!effectiveAuthority) return { eligible: false, reason: "未声明 Reference Authority，不可提交 Provider" };
  if (item.category === "other") return { eligible: false, reason: "未声明受支持的参考职责，不可提交 Provider" };
  if (!effectiveAuthority.providerEligible) return { eligible: false, reason: "Authority 明确禁止提交 Provider" };
  if (item.status !== "approved") return { eligible: false, reason: `审核状态为 ${item.status}，仅已批准资产可提交` };
  const boundToCurrentProfile = Boolean(profile.value && currentProfileBoundAssetIds.value.has(item.id));
  const requiresCurrentProfileBinding = effectiveAuthority.role === "identity" || effectiveAuthority.role === "style_board" || item.source === "preset";
  if (requiresCurrentProfileBinding && !boundToCurrentProfile) {
    return { eligible: false, reason: "未绑定到当前视觉档案 Revision" };
  }
  if (effectiveAuthority.role === "identity") {
    if (!currentReference || currentReference.authorityOrigin !== "subject_revision") {
      return { eligible: false, reason: "不是当前 SubjectRevision 派生的身份权威" };
    }
    if (currentReference.currentAuthority !== true) {
      return { eligible: false, reason: "不是当前已批准 SubjectRevision 身份权威" };
    }
  }
  if (currentReference && currentReference.approvalStatus !== "approved") {
    return { eligible: false, reason: `当前视觉档案中的引用状态为 ${currentReference.approvalStatus}` };
  }
  const boundRevision = item.metadata.visualProfileRevisionId ?? item.metadata.profileRevisionId;
  if (boundRevision && profile.value && String(boundRevision) !== profile.value.id) {
    return { eligible: false, reason: "资产属于其他视觉档案 Revision" };
  }
  if (effectiveAuthority.role === "style_board") {
    return { eligible: true, reason: `可提交人物、猫咪、环境和视频 Provider · 已批准并绑定当前 Revision ${profile.value!.revision}` };
  }
  return boundToCurrentProfile
    ? { eligible: true, reason: `已批准并绑定当前视觉档案 Revision ${profile.value!.revision}` }
    : { eligible: true, reason: "已批准项目资产，可按声明职责提交 Provider" };
}

const authorityConflicts = computed(() => {
  const subjectRevisions = new Map<"child" | "cat", Set<string>>([["child", new Set()], ["cat", new Set()]]);
  for (const reference of profile.value?.references ?? []) {
    if (
      reference.authority?.role !== "identity"
      || reference.approvalStatus !== "approved"
      || reference.currentAuthority !== true
      || reference.authorityOrigin !== "subject_revision"
      || !reference.subjectRevisionId
    ) continue;
    const subject = reference.subjectKind === "animal" || reference.subjectRole === "co_protagonist"
      ? "cat"
      : reference.subjectKind === "person" || reference.subjectRole === "protagonist"
      ? "child"
      : null;
    if (!subject) continue;
    subjectRevisions.get(subject)!.add(reference.subjectRevisionId);
  }
  return (["child", "cat"] as const)
    .filter((subject) => subjectRevisions.get(subject)!.size > 1)
    .map((subject) => `${subject === "child" ? "儿童" : "猫咪"}存在多个身份权威来源`);
});

function syncForm(value?: EpisodeVisualProfileDto) {
  form.personIdentity = value?.personIdentity ?? "";
  form.personHair = value?.personHair ?? "";
  form.personBody = value?.personBody ?? "";
  form.catIdentity = value?.catIdentity ?? "";
  form.referenceBindings = value?.referenceBindings.map((binding) => ({ ...binding })) ?? [];
  positiveText.value = value?.stylePositive.join("\n") ?? "";
  negativeText.value = value?.styleNegative.join("\n") ?? "";
  saveState.value = "saved";
}

function selectItem(item: MediaItem, updateRoute = true, revealInspector = true) {
  selectedId.value = item.id;
  inspectorOpen.value = true;
  if (revealInspector) mobilePanel.value = "inspector";
  if (updateRoute) {
    void router.replace({
      name: "project-assets",
      params: { projectId: props.projectId },
      query: { item: item.id, ...(props.panel !== "main" ? { panel: props.panel } : {}) },
    });
  }
}

async function setMobilePanel(panel: "categories" | "board" | "inspector") {
  mobilePanel.value = panel;
  if (panel === "inspector") inspectorOpen.value = true;
  await nextTick();
  ({ categories: categoriesPanel, board: boardPanel, inspector: inspectorPanel }[panel].value)?.focus();
}

function applySnapshot(snapshot: AssetLoadSnapshot, preserveProfile = false) {
  if (snapshot.assets) assets.value = snapshot.assets.filter((asset) => asset.mediaType === "image");
  if (snapshot.presets) presets.value = snapshot.presets;
  if (snapshot.profile && !preserveProfile) {
    profile.value = snapshot.profile;
    syncForm(snapshot.profile);
  }
  loadWarnings.value = snapshot.warnings;
  error.value = snapshot.warnings.join("\n");
  if (!snapshot.successfulReads) {
    state.value = media.value.length || profile.value || presets.value.length ? "stale" : "error";
    return;
  }
  state.value = snapshot.warnings.length ? "stale" : "ready";
  const requested = media.value.find((item) => item.id === props.focusedItemId);
  const retained = media.value.find((item) => item.id === selectedId.value);
  const initial = requested ?? retained ?? media.value[0];
  if (initial) selectItem(initial, false, Boolean(requested));
  else selectedId.value = "";
}

async function load(background = false) {
  activeController?.abort("asset workspace request superseded");
  const controller = new AbortController();
  activeController = controller;
  const sequence = ++requestSequence;
  state.value = background && (media.value.length || profile.value || presets.value.length) ? "ready" : "loading";
  error.value = "";
  loadWarnings.value = [];
  const results = await Promise.allSettled([
    canvasApi.assets(props.projectId, "image", controller.signal),
    canvasApi.episodeVisualProfile(props.projectId, controller.signal),
    canvasApi.visualPresets(controller.signal),
  ]);
  if (controller.signal.aborted || sequence !== requestSequence) return;
  const [assetResult, profileResult, presetResult] = results;
  const snapshot: AssetLoadSnapshot = {
    assets: assetResult.status === "fulfilled" ? assetResult.value : undefined,
    profile: profileResult.status === "fulfilled" ? profileResult.value : undefined,
    presets: presetResult.status === "fulfilled" ? presetResult.value : undefined,
    warnings: [
      ...(assetResult.status === "rejected" ? [`项目媒体：${String(assetResult.reason)}`] : []),
      ...(profileResult.status === "rejected" ? [`视觉档案：${String(profileResult.reason)}`] : []),
      ...(presetResult.status === "rejected" ? [`Canon 预设：${String(presetResult.reason)}`] : []),
    ],
    successfulReads: results.filter((result) => result.status === "fulfilled").length,
  };
  if (dirty.value && snapshot.successfulReads) {
    pendingSnapshot = snapshot;
    loadWarnings.value = ["服务器数据已更新；当前未保存 Canon 已保留", ...snapshot.warnings];
    error.value = loadWarnings.value.join("\n");
    state.value = "stale";
    activeController = undefined;
    return;
  }
  if (snapshot.successfulReads) pendingSnapshot = undefined;
  applySnapshot(snapshot);
  activeController = undefined;
}

async function reconcileProjectAssets(): Promise<boolean> {
  reconciliationController?.abort("asset review reconciliation superseded");
  const controller = new AbortController();
  reconciliationController = controller;
  const projectId = props.projectId;
  try {
    const authoritativeAssets = await canvasApi.assets(projectId, "image", controller.signal);
    if (controller.signal.aborted || props.projectId !== projectId) return false;
    assets.value = authoritativeAssets.filter((asset) => asset.mediaType === "image");
    if (pendingSnapshot) pendingSnapshot = { ...pendingSnapshot, assets: authoritativeAssets };
    const assetIds = new Set(assets.value.map((asset) => asset.id));
    batchSelection.value = batchSelection.value.filter((assetId) => assetIds.has(assetId));
    if (selectedId.value && !media.value.some((item) => item.id === selectedId.value)) {
      selectedId.value = media.value[0]?.id ?? "";
    }
    return true;
  } catch (reason) {
    if (controller.signal.aborted) return false;
    const message = reason instanceof Error ? reason.message : String(reason);
    loadWarnings.value = [
      `资产审核已写入，但权威资产状态刷新失败：${message}`,
      ...loadWarnings.value.filter((item) => !item.startsWith("资产审核已写入")),
    ];
    error.value = loadWarnings.value.join("\n");
    state.value = "stale";
    return false;
  } finally {
    if (reconciliationController === controller) reconciliationController = undefined;
  }
}

function lines(value: string) {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}

async function saveProfile(): Promise<boolean> {
  if (!profile.value || !dirty.value || saving.value) return !dirty.value;
  saving.value = true;
  saveState.value = "saving";
  try {
    const saved = await canvasApi.updateEpisodeVisualProfile(props.projectId, profile.value.revision, {
      ...form,
      stylePositive: lines(positiveText.value),
      styleNegative: lines(negativeText.value),
      referenceBindings: form.referenceBindings.map((binding) => ({ ...binding })),
    });
    profile.value = saved;
    syncForm(saved);
    if (pendingSnapshot) {
      const incoming = pendingSnapshot;
      pendingSnapshot = undefined;
      applySnapshot(incoming, true);
    }
    ElMessage.success(`视觉档案已保存为 Revision ${saved.revision}`);
    return true;
  } catch (reason) {
    const message = reason instanceof Error ? reason.message : String(reason);
    error.value = message;
    saveState.value = /409|revision|版本|冲突/i.test(message) ? "conflict" : "error";
    return false;
  } finally {
    saving.value = false;
  }
}

function discardProfile() {
  if (pendingSnapshot) {
    const incoming = pendingSnapshot;
    pendingSnapshot = undefined;
    applySnapshot(incoming);
    return;
  }
  syncForm(profile.value);
}

async function review(decision: "approved" | "rejected") {
  const item = selected.value;
  if (!item?.reviewAction?.executable) return;
  const action = decision === "approved" ? "批准" : "拒绝";
  try {
    await ElMessageBox.confirm(
      `${action}“${item.title}”。普通卡片选择不会提交，这个明确操作会写入现有审核记录。`,
      `${action}资产`,
      { confirmButtonText: `确认${action}`, cancelButtonText: "取消", type: decision === "approved" ? "success" : "warning" },
    );
  } catch (reason) {
    if (reason !== "cancel" && reason !== "close") ElMessage.error(`无法确认资产审核：${reason instanceof Error ? reason.message : String(reason)}`);
    return;
  }
  try {
    await executeReview(item, decision);
  } catch (reason) {
    ElMessage.error(`${action}资产失败：${reason instanceof Error ? reason.message : String(reason)}`);
    return;
  }
  const reconciled = await reconcileProjectAssets();
  if (!reconciled) {
    ElMessage.warning("审核已提交，但权威状态刷新失败；请重试加载后再继续");
    return;
  }
  ElMessage.success(`资产已${action}`);
}

async function executeReview(item: MediaItem, decision: "approved" | "rejected") {
  const action = item.reviewAction;
  if (!action?.executable) throw new Error(action?.disabledReason || "当前资产没有可执行的审核目标");
  const reason = decision === "approved" ? "导演资产工作区审核通过" : "导演资产工作区审核拒绝";
  if (action.route === "recipe_character_design") {
    if (!action.recipeInstanceId || !action.targetHash) throw new Error("角色设计审核上下文不完整，请刷新后重试");
    await canvasApi.reviewRecipeTarget({
      recipeInstanceId: action.recipeInstanceId,
      targetType: "character_design",
      targetId: action.targetId,
      targetHash: action.targetHash,
      decision: decision === "approved" ? "approve" : "request_changes",
      reason,
    });
    return;
  }
  if (action.route === "legacy_asset") {
    await api.reviewAsset(item.id, decision, reason);
    return;
  }
  throw new Error(action.disabledReason || "当前资产没有可执行的审核目标");
}

function toggleBatchItem(itemId: string) {
  batchSelection.value = batchSelection.value.includes(itemId)
    ? batchSelection.value.filter((id) => id !== itemId)
    : [...batchSelection.value, itemId];
}

async function approveBatchSelection() {
  const pending = selectedBatchItems.value.filter((item) => item.status !== "approved" && item.reviewAction?.executable);
  if (!pending.length) {
    ElMessage.warning("已选资产没有待审核项");
    return;
  }
  try {
    await ElMessageBox.confirm(
      `将批准 ${pending.length} 个已选媒体资产。审核不会调用 Provider，Provider 调用 0 次。`,
      "批量审核资产",
      { confirmButtonText: `批准 ${pending.length} 项`, cancelButtonText: "取消", type: "success" },
    );
  } catch (reason) {
    if (reason !== "cancel" && reason !== "close") ElMessage.error(`无法确认批量审核：${reason instanceof Error ? reason.message : String(reason)}`);
    return;
  }
  const failed: Array<{ item: MediaItem; reason: unknown }> = [];
  const succeededItems: MediaItem[] = [];
  for (const item of pending) {
    try {
      await executeReview(item, "approved");
      succeededItems.push(item);
    } catch (reason) {
      failed.push({ item, reason });
    }
  }
  batchSelection.value = failed.map(({ item }) => item.id);
  const succeeded = pending.length - failed.length;
  const reconciled = !succeededItems.length || await reconcileProjectAssets();
  if (failed.length) {
    const details = failed.map(({ item, reason }) => `${item.title}：${reason instanceof Error ? reason.message : String(reason)}`).join("；");
    ElMessage.error(`批量审核完成：成功 ${succeeded} 项，失败 ${failed.length} 项。${details}${reconciled ? "" : "；成功项的权威状态刷新失败，请重试加载"}`);
    return;
  }
  if (!reconciled) {
    ElMessage.warning(`已批准 ${succeeded} 个资产，但权威状态刷新失败；请重试加载`);
    return;
  }
  ElMessage.success(`已批准 ${succeeded} 个资产，未产生 Provider 调用`);
}

async function generateEpisodeDesigns() {
  const recipeInstanceId = profile.value?.recipeInstanceId;
  if (!recipeInstanceId || generating.value) {
    if (!recipeInstanceId) ElMessage.warning("当前视觉档案没有关联可执行的一人一猫 Recipe");
    return;
  }
  if (dirty.value) {
    ElMessage.warning("请先保存或放弃 Canon 修改，再冻结角色设计输入");
    return;
  }
  if (authorityConflicts.value.length) {
    ElMessage.warning("请先解决儿童或猫咪的身份权威冲突");
    return;
  }
  generating.value = true;
  try {
    const fingerprint = `${recipeInstanceId}:${profile.value?.revision ?? 0}:all`;
    if (generationAttempt?.fingerprint !== fingerprint) {
      generationAttempt = { fingerprint, idempotencyKey: crypto.randomUUID() };
    }
    const preview = await canvasApi.previewRecipeCharacterDesign(
      recipeInstanceId,
      generationAttempt.idempotencyKey,
      0,
      "all",
    );
    const blockers = preview.slots.flatMap((slot) => slot.blockers.map((message) => `${slot.slot}：${message}`));
    if (blockers.length) {
      ElMessage.error(`角色设计输入不可执行：${blockers.join("；")}`);
      return;
    }
    if (preview.estimatedCostMicros == null) {
      ElMessage.warning("角色设计图片调用费用尚未计量，不能建立明确费用边界");
      return;
    }
    const slotSummary = preview.slots.map((slot) => (
      `${{ child: "本集儿童", cat: "本集猫咪", pair_scale: "人猫同框" }[slot.slot]}：${slot.provider}/${slot.model}，${slot.references.length} 张参考`
    )).join("\n");
    await ElMessageBox.confirm(
      `${slotSummary}\n\n共 ${preview.slots.length} 组图片调用，预计费用 ${(preview.estimatedCostMicros / 1_000_000).toFixed(4)}。确认后才会创建 durable task。`,
      "生成本集角色设计",
      { confirmButtonText: "确认并创建任务", cancelButtonText: "取消", type: "warning" },
    );
    const job = await canvasApi.runRecipeCharacterDesign(
      recipeInstanceId,
      preview.estimatedCostMicros,
      generationAttempt.idempotencyKey,
      preview.inputHash,
      "all",
    );
    ElMessage.success(`角色设计任务已创建（${job.jobId}）；任务完成后刷新媒体板`);
  } catch (reason) {
    if (reason !== "cancel" && reason !== "close") {
      ElMessage.error(reason instanceof Error ? reason.message : String(reason));
    }
  } finally {
    generating.value = false;
  }
}

watch(dirty, (value) => {
  saveState.value = value ? "dirty" : saveState.value === "dirty" ? "saved" : saveState.value;
  emit("dirty-change", value ? {
    scope: `assets:${props.projectId}:visual-profile`,
    label: "人物、猫咪与画风 Canon",
    save: saveProfile,
    discard: discardProfile,
  } : undefined);
}, { immediate: true });
watch(() => props.projectId, () => {
  reconciliationController?.abort("asset workspace project changed");
  assets.value = [];
  profile.value = undefined;
  presets.value = [];
  pendingSnapshot = undefined;
  selectedId.value = "";
  void load(false);
}, { immediate: true });
watch(() => props.focusedItemId, () => {
  const item = media.value.find((candidate) => candidate.id === props.focusedItemId);
  if (item) selectItem(item, false);
});
onBeforeUnmount(() => {
  requestSequence += 1;
  activeController?.abort("asset workspace unmounted");
  reconciliationController?.abort("asset workspace unmounted");
  emit("dirty-change", undefined);
});
</script>

<template>
  <section class="asset-workspace" :data-mobile-panel="mobilePanel" aria-label="角色资产工作区">
    <nav class="mobile-panel-tabs" role="tablist" aria-label="角色资产工作区面板">
      <button id="asset-tab-categories" type="button" role="tab" aria-controls="asset-panel-categories" :aria-selected="mobilePanel === 'categories'" @click="setMobilePanel('categories')">分类与 Canon</button>
      <button id="asset-tab-board" type="button" role="tab" aria-controls="asset-panel-board" :aria-selected="mobilePanel === 'board'" @click="setMobilePanel('board')">媒体资产</button>
      <button id="asset-tab-inspector" type="button" role="tab" aria-controls="asset-panel-inspector" :aria-selected="mobilePanel === 'inspector'" @click="setMobilePanel('inspector')">职责检查</button>
    </nav>

    <aside id="asset-panel-categories" ref="categoriesPanel" class="asset-controls" role="tabpanel" aria-labelledby="asset-tab-categories" tabindex="-1">
      <header><el-icon><Collection /></el-icon><div><span>MEDIA BOARD</span><b>角色资产</b></div></header>
      <label class="asset-search"><el-icon><Search /></el-icon><input v-model="query" type="search" aria-label="搜索角色资产" placeholder="搜索名称或语义键" /></label>
      <nav aria-label="资产分类">
        <button v-for="category in categories" :key="category.id" type="button" :class="{ active: activeCategory === category.id }" @click="activeCategory = category.id">
          <el-icon><Filter /></el-icon><span>{{ category.title }}</span><em>{{ category.id === 'all' ? media.length : media.filter((item) => item.category === category.id).length }}</em>
        </button>
      </nav>
      <section class="batch-summary">
        <span>GENERATION BATCHES</span>
        <div><b>{{ existingBatchCount }} 个历史批次</b><small>{{ selectedBatchItems.length }} 个已选媒体</small></div>
        <button class="generate-designs" type="button" :disabled="generating || dirty || authorityConflicts.length > 0 || !profile?.recipeInstanceId" @click="generateEpisodeDesigns"><el-icon><MagicStick /></el-icon>{{ generating ? '正在准备输入…' : '生成本集儿童、猫咪与同框' }}</button>
        <button class="batch-review" type="button" :disabled="!pendingBatchReviewCount" @click="approveBatchSelection">批量审核已选 · 0 次 Provider</button>
        <small>新的付费生成批次必须先进入独立费用确认，不会由卡片选择隐式创建。</small>
      </section>
      <section class="canon-summary"><span>CANON STATUS</span><b>{{ profile ? `Revision ${profile.revision}` : '未载入' }}</b><small>{{ profile?.sourceProfileId || '尚未应用项目 Canon' }}</small><mark :class="authorityConflicts.length ? 'blocked' : 'ready'">{{ authorityConflicts.length ? '身份权威冲突' : '身份权威唯一' }}</mark></section>
    </aside>

    <main id="asset-panel-board" ref="boardPanel" class="asset-board" role="tabpanel" aria-labelledby="asset-tab-board" tabindex="-1">
      <button v-if="!inspectorOpen" class="inspector-open" type="button" aria-label="打开资产职责检查器" @click="inspectorOpen = true"><el-icon><Lock /></el-icon>资产检查器</button>
      <div v-if="state === 'loading'" class="asset-state" aria-busy="true">正在读取真实媒体和 Canon…</div>
      <div v-else-if="state === 'error'" class="asset-state error" role="alert"><b>角色资产工作区加载失败</b><p>{{ error }}</p><button type="button" @click="load(false)"><el-icon><Refresh /></el-icon>重新加载</button></div>
      <template v-else>
        <div v-if="state === 'stale'" class="asset-warning" role="status">部分数据可能过期：{{ loadWarnings.join('；') }} <button type="button" @click="load(true)">重试</button></div>
        <div v-if="authorityConflicts.length" class="authority-blocker" role="alert"><el-icon><Warning /></el-icon><div><b>Provider 提交已阻断</b><p>{{ authorityConflicts.join('；') }}。请在费用确认前保留一个权威 Revision。</p></div></div>
        <header><div><span>{{ categories.find((item) => item.id === activeCategory)?.title }}</span><b>{{ filteredMedia.length }} 个真实媒体资产</b></div><small>卡片点击只选择，不会生成、审核或提交 Provider。</small></header>
        <div v-if="filteredMedia.length" class="media-grid">
          <article v-for="item in filteredMedia" :key="item.id" class="media-card" :class="{ selected: selected?.id === item.id }">
            <label class="batch-toggle">
              <input
                type="checkbox"
                :disabled="!item.reviewAction?.executable"
                :checked="batchSelection.includes(item.id)"
                :aria-label="`选择 ${item.title} 用于批量操作`"
                @change="toggleBatchItem(item.id)"
              />
            </label>
            <button type="button" @click="selectItem(item)">
              <img :src="item.contentUrl" :alt="item.title" />
              <div><b>{{ item.title }}</b><small>{{ item.semanticKey || item.category }}</small><span :data-status="item.status">{{ item.status }}</span><em :class="{ excluded: !providerEligibility(item).eligible }" :data-provider-eligible="providerEligibility(item).eligible">{{ providerEligibility(item).reason }}</em></div>
            </button>
          </article>
        </div>
        <div v-else class="asset-state"><el-icon><Picture /></el-icon><b>当前分类没有真实媒体</b><p>这里不会用占位图伪造角色、猫咪或画风资产。</p></div>

        <details v-if="profile" class="canon-editor" :open="panel === 'references'">
          <summary><span>编辑 Canon 文字契约</span><small :data-save-state="saveState">{{ { saved: '已保存', dirty: '未保存', saving: '保存中', conflict: '版本冲突', error: '保存失败' }[saveState] }}</small></summary>
          <div class="canon-fields">
            <label><span>儿童身份固定特征</span><textarea v-model="form.personIdentity" rows="3" /></label>
            <label><span>儿童发型固定特征</span><textarea v-model="form.personHair" rows="3" /></label>
            <label><span>儿童身体比例</span><textarea v-model="form.personBody" rows="3" /></label>
            <label><span>猫咪身份固定特征</span><textarea v-model="form.catIdentity" rows="3" /></label>
            <label><span>画风正向语言（每行一项）</span><textarea v-model="positiveText" rows="4" /></label>
            <label><span>长期排除项（每行一项）</span><textarea v-model="negativeText" rows="4" /></label>
          </div>
          <footer><small>正式保存创建新 Revision，并沿用现有 stale 传播。</small><button type="button" :disabled="!dirty" @click="discardProfile">放弃修改</button><button class="save" type="button" :disabled="saving || !dirty" @click="saveProfile">保存 Canon Revision</button></footer>
        </details>
      </template>
    </main>

    <aside id="asset-panel-inspector" ref="inspectorPanel" class="asset-inspector" :class="{ 'is-collapsed': !inspectorOpen }" role="tabpanel" aria-labelledby="asset-tab-inspector" tabindex="-1">
      <template v-if="selected">
        <img :src="selected.contentUrl" :alt="selected.title" />
        <header><div><span>REFERENCE AUTHORITY</span><b>{{ selected.title }}</b><small>{{ selected.semanticKey || selected.id }}</small></div><el-icon><Lock /></el-icon><button class="inspector-close" type="button" aria-label="关闭资产职责检查器" @click="inspectorOpen = false"><el-icon><Close /></el-icon></button></header>
        <dl><div><dt>类别</dt><dd>{{ categories.find((item) => item.id === selected?.category)?.title }}</dd></div><div><dt>审核状态</dt><dd>{{ selected.status }}</dd></div><div><dt>来源</dt><dd>{{ selected.source }}</dd></div><div><dt>Provider</dt><dd>{{ providerEligibility(selected).reason }}</dd></div></dl>
        <section><span>固定特征</span><ul><li v-for="trait in selected.authority?.lockedTraits || []" :key="trait">{{ trait }}</li><li v-if="!selected.authority?.lockedTraits?.length">当前资产未声明额外固定特征</li></ul></section>
        <section><span>允许变化</span><ul><li v-for="trait in selected.authority?.mutableTraits || []" :key="trait">{{ trait }}</li><li v-if="!selected.authority?.mutableTraits?.length">当前资产未声明允许变化特征</li></ul></section>
        <section v-if="selected.authority?.forbiddenTransfer?.length"><span>禁止迁移内容</span><ul><li v-for="item in selected.authority.forbiddenTransfer" :key="item">{{ item }}</li></ul></section>
        <details><summary>审计与血缘</summary><pre>{{ JSON.stringify(selected.metadata, null, 2) }}</pre></details>
        <footer v-if="selected.reviewAction?.executable"><button type="button" :disabled="selected.status === 'approved'" @click="review('approved')"><el-icon><Check /></el-icon>批准资产</button><button class="reject" type="button" @click="review('rejected')">拒绝</button></footer>
        <p v-else class="readonly-binding"><el-icon><Lock /></el-icon>当前绑定为只读：{{ selected.reviewAction?.disabledReason || '没有可执行的审核目标' }}</p>
      </template>
      <div v-else class="asset-state">选择一个真实媒体资产查看职责、版本和血缘。</div>
    </aside>
  </section>
</template>

<style scoped>
.mobile-panel-tabs{display:none}
.asset-workspace{height:100%;min-height:0;display:grid;grid-template-columns:300px minmax(0,1fr) 340px;overflow:hidden;color:#e5ecf5;background:#0d1117}.asset-controls,.asset-inspector{min-width:0;padding:16px;overflow:auto;background:#11171e}.asset-controls{border-right:1px solid #29333f}.asset-inspector{border-left:1px solid #29333f}.asset-controls>header,.asset-inspector>header{display:flex;align-items:center;gap:11px;min-height:56px}.asset-controls>header :deep(svg),.asset-inspector>header :deep(svg){width:24px;height:24px;color:#77a9d9}.asset-controls>header div,.asset-inspector>header div{min-width:0;display:grid;gap:3px}.asset-controls span,.asset-inspector span,.asset-board>header span{color:#6f87a3;font-size:10px;font-weight:800;letter-spacing:.11em}.asset-search{min-height:44px;margin:10px 0;display:flex;align-items:center;gap:8px;padding:0 10px;background:#171e26;border:1px solid #303b49;border-radius:9px}.asset-search input{min-width:0;flex:1;color:#dce5ee;background:transparent;border:0;outline:0}.asset-controls nav{display:grid;gap:4px}.asset-controls nav button{min-height:44px;padding:0 10px;display:grid;grid-template-columns:20px 1fr auto;align-items:center;gap:8px;color:#8f9baa;text-align:left;background:transparent;border:1px solid transparent;border-radius:8px;cursor:pointer}.asset-controls nav button.active{color:#e3ebf4;background:#1c2733;border-color:#34516d}.asset-controls nav em{font-size:10px;font-style:normal}.canon-summary{margin-top:14px;padding:13px;display:grid;gap:5px;background:#171e26;border:1px solid #2d3946;border-radius:11px}.canon-summary small{overflow:hidden;color:#748194;text-overflow:ellipsis;white-space:nowrap}.canon-summary mark{width:fit-content;margin-top:5px;padding:4px 7px;color:#9fd4b5;background:#1a3225;border-radius:999px;font-size:10px}.canon-summary mark.blocked{color:#e6a9a0;background:#3a2224}.asset-board{min-width:0;min-height:0;padding:18px;overflow:auto;background:#0d1117}.asset-board>header{display:flex;align-items:end;justify-content:space-between;gap:20px;margin-bottom:14px}.asset-board>header div{display:grid;gap:3px}.asset-board>header small{color:#718095}.media-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px}.media-grid>button{min-width:0;padding:0;overflow:hidden;color:#d6dfe9;text-align:left;background:#161c23;border:1px solid #2e3945;border-radius:11px;cursor:pointer}.media-grid>button.selected{border-color:#5785b1;box-shadow:inset 0 0 0 1px rgb(95 151 204 / 22%)}.media-grid img{display:block;width:100%;aspect-ratio:4/3;object-fit:cover;background:#0b0e12}.media-grid button>div{padding:10px;display:grid;grid-template-columns:1fr auto;gap:4px}.media-grid b,.media-grid small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.media-grid small{grid-column:1/span 2;color:#728093;font-size:10px}.media-grid span{width:fit-content;padding:3px 6px;color:#9fc8ae;background:#1a3023;border-radius:999px;font-size:9px}.media-grid em{grid-column:1/span 2;color:#83ad92;font-size:10px;font-style:normal}.media-grid em.excluded{color:#d5ae6f}.asset-warning,.authority-blocker{margin-bottom:12px;padding:10px 12px;border-radius:9px}.asset-warning{color:#ddb877;background:#2b2318;border:1px solid #5e4a2c}.asset-warning button{min-height:36px;color:inherit;background:transparent;border:1px solid currentColor;border-radius:7px}.authority-blocker{display:flex;gap:9px;color:#e1a199;background:#321f21;border:1px solid #714145}.authority-blocker p{margin:3px 0 0}.canon-editor{margin-top:18px;padding:14px;background:#141b23;border:1px solid #2e3946;border-radius:12px}.canon-editor summary{min-height:44px;display:flex;align-items:center;justify-content:space-between;cursor:pointer}.canon-editor summary small{color:#748194}.canon-editor summary [data-save-state='dirty'],.canon-editor summary [data-save-state='conflict'],.canon-editor summary [data-save-state='error']{color:#dca86c}.canon-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;padding-top:12px}.canon-fields label{display:grid;gap:5px;color:#8b99aa;font-size:10px}.canon-fields textarea{padding:10px;color:#e5ecf4;background:#0f141a;border:1px solid #323e4c;border-radius:8px;resize:vertical}.canon-editor footer{margin-top:12px;display:flex;align-items:center;justify-content:flex-end;gap:8px}.canon-editor footer small{margin-right:auto;color:#748194}.canon-editor button,.asset-inspector footer button,.asset-state button{min-height:44px;padding:0 13px;color:#cfdae6;background:#242e39;border:1px solid #3b4958;border-radius:9px;cursor:pointer}.canon-editor button.save,.asset-inspector footer button:first-child{color:#102219;background:#8fc6a5;border-color:#a7d8b8;font-weight:800}.canon-editor button:disabled,.asset-inspector footer button:disabled{opacity:.4;cursor:not-allowed}.asset-inspector>img{display:block;width:100%;aspect-ratio:4/3;object-fit:cover;background:#0b0e12;border-radius:10px}.asset-inspector>header{justify-content:space-between}.asset-inspector>header b,.asset-inspector>header small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.asset-inspector dl{display:grid;gap:6px;margin:10px 0}.asset-inspector dl div{display:grid;grid-template-columns:86px 1fr;gap:8px;padding:7px 0;border-bottom:1px solid #26313d}.asset-inspector dt{color:#718095}.asset-inspector dd{margin:0;color:#c3ceda}.asset-inspector section{margin-top:12px;padding:11px;background:#171e26;border:1px solid #2c3743;border-radius:9px}.asset-inspector ul{margin:8px 0 0;padding-left:18px;color:#a8b4c2;line-height:1.55}.asset-inspector details{margin-top:12px;color:#7e8b9c}.asset-inspector details summary{min-height:44px;display:flex;align-items:center;cursor:pointer}.asset-inspector pre{max-height:190px;overflow:auto;padding:9px;color:#8595a8;background:#0d1217;border-radius:7px;font-size:9px;white-space:pre-wrap}.asset-inspector footer{margin-top:12px;display:flex;gap:8px}.asset-inspector footer button{flex:1}.asset-inspector footer .reject{color:#e2aaa3;background:#352326;border-color:#654044}.asset-state{min-height:260px;display:grid;place-items:center;align-content:center;gap:8px;color:#758396}.asset-state.error{color:#dfaaa1}.asset-state p{margin:0;text-align:center}
.readonly-binding{min-height:44px;margin:12px 0 0;padding:9px 11px;display:flex;align-items:center;gap:8px;color:#8896a7;background:#171e26;border:1px solid #2c3743;border-radius:9px}
.batch-summary{margin-top:14px;padding:13px;display:grid;gap:7px;background:#171e26;border:1px solid #2d3946;border-radius:11px}.batch-summary>div{display:flex;align-items:center;justify-content:space-between;gap:8px}.batch-summary small{color:#748194;line-height:1.45}.batch-summary button{min-height:44px;padding:0 9px;color:#cbd7e4;background:#222d38;border:1px solid #3b4a59;border-radius:8px;cursor:pointer}.batch-summary button:disabled{opacity:.4;cursor:not-allowed}.media-card{position:relative;min-width:0;overflow:hidden;background:#161c23;border:1px solid #2e3945;border-radius:11px}.media-card.selected{border-color:#5785b1;box-shadow:inset 0 0 0 1px rgb(95 151 204 / 22%)}.batch-toggle{position:absolute;z-index:2;top:0;left:0;width:44px;height:44px;display:grid;place-items:center;cursor:pointer}.batch-toggle input{width:22px;height:22px;margin:0;accent-color:#65a0d5;cursor:pointer}.media-card>button{width:100%;padding:0;color:#d6dfe9;text-align:left;background:transparent;border:0;cursor:pointer}.media-card>button:focus-visible,.batch-toggle:focus-within{outline:2px solid #78aef0;outline-offset:-2px}.media-card img{display:block;width:100%;aspect-ratio:4/3;object-fit:cover;background:#0b0e12}.media-card button>div{padding:10px;display:grid;grid-template-columns:1fr auto;gap:4px}.media-card b,.media-card small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.media-card small{grid-column:1/span 2;color:#728093;font-size:10px}.media-card span{width:fit-content;padding:3px 6px;color:#9fc8ae;background:#1a3023;border-radius:999px;font-size:9px}.media-card em{grid-column:1/span 2;color:#83ad92;font-size:10px;font-style:normal}.media-card em.excluded{color:#d5ae6f}.inspector-open{position:sticky;z-index:5;top:0;float:right;min-height:44px;padding:0 12px;display:none;align-items:center;gap:6px;color:#d4e0eb;background:#1f2b37;border:1px solid #3b4c5d;border-radius:9px;cursor:pointer}.inspector-close{display:none;min-width:44px;min-height:44px;place-items:center;color:#aab7c5;background:transparent;border:1px solid #344250;border-radius:8px;cursor:pointer}
@media(max-width:1439px){.asset-workspace{grid-template-columns:280px minmax(0,1fr)}.asset-inspector{position:absolute;z-index:8;inset:0 0 0 auto;width:340px;box-shadow:-12px 0 32px rgb(0 0 0 / 36%)}.asset-inspector.is-collapsed{display:none}.inspector-close{display:grid}.inspector-open{display:inline-flex}}
@media(max-width:1023px){.asset-workspace{grid-template-columns:1fr;grid-template-rows:auto minmax(0,1fr);overflow:hidden}.mobile-panel-tabs{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px;padding:6px;background:#11171e;border-bottom:1px solid #29333f}.mobile-panel-tabs button{min-height:44px;color:#8998aa;background:#171e26;border:1px solid transparent;border-radius:8px;cursor:pointer}.mobile-panel-tabs button[aria-selected='true']{color:#e4edf6;background:#203044;border-color:#44698d}.asset-controls,.asset-board,.asset-inspector{grid-row:2;grid-column:1}.asset-controls{max-height:none;border-right:0}.asset-controls nav{grid-template-columns:repeat(2,minmax(0,1fr))}.asset-board{min-height:0}.asset-inspector{position:relative;inset:auto;width:auto;max-height:none;border-left:0;box-shadow:none}.asset-workspace[data-mobile-panel='categories'] .asset-board,.asset-workspace[data-mobile-panel='categories'] .asset-inspector,.asset-workspace[data-mobile-panel='board'] .asset-controls,.asset-workspace[data-mobile-panel='board'] .asset-inspector,.asset-workspace[data-mobile-panel='inspector'] .asset-controls,.asset-workspace[data-mobile-panel='inspector'] .asset-board{display:none}.asset-workspace[data-mobile-panel='inspector'] .asset-inspector.is-collapsed{display:block}.canon-fields{grid-template-columns:1fr}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
</style>
