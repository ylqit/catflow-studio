import type {
  ProjectLibraryGroupMode,
  ProjectLibraryItemDto,
  ProjectLibraryQuery,
} from "./api/types";

export interface ProjectLibraryGroup {
  key: string;
  label: string;
  items: ProjectLibraryItemDto[];
}

export function buildLibraryQuery(query: ProjectLibraryQuery): URLSearchParams {
  const params = new URLSearchParams();
  if (query.q) params.set("q", query.q);
  if (query.systemView && query.systemView !== "all") params.set("systemView", query.systemView);
  if (query.collectionId) params.set("collectionId", query.collectionId);
  if (query.unassigned) params.set("unassigned", "true");
  for (const tag of query.tags ?? []) params.append("tags", tag);
  if (query.stage) params.set("stage", query.stage);
  if (query.dateFrom) params.set("dateFrom", query.dateFrom);
  if (query.dateTo) params.set("dateTo", query.dateTo);
  if (query.sort) params.set("sort", query.sort);
  if (query.cursor) params.set("cursor", query.cursor);
  params.set("limit", String(query.limit ?? 36));
  return params;
}

export function mergeLibraryItems(
  current: ProjectLibraryItemDto[],
  incoming: ProjectLibraryItemDto[],
): ProjectLibraryItemDto[] {
  const seen = new Set(current.map((item) => item.id));
  return [...current, ...incoming.filter((item) => !seen.has(item.id))];
}

export function groupLibraryItems(
  items: ProjectLibraryItemDto[],
  mode: ProjectLibraryGroupMode,
  now = new Date(),
): ProjectLibraryGroup[] {
  if (mode === "none") return [{ key: "all", label: "", items }];
  if (mode === "collection") {
    const groups = new Map<string, ProjectLibraryGroup>();
    for (const item of items) {
      const key = item.collection?.id ?? "ungrouped";
      const group = groups.get(key) ?? {
        key,
        label: item.collection?.name ?? "未分组",
        items: [],
      };
      group.items.push(item);
      groups.set(key, group);
    }
    return [...groups.values()];
  }

  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const weekday = startOfToday.getDay() || 7;
  const startOfWeek = new Date(startOfToday);
  startOfWeek.setDate(startOfToday.getDate() - weekday + 1);
  const startOfMonth = new Date(now.getFullYear(), now.getMonth(), 1);
  const buckets: ProjectLibraryGroup[] = [
    { key: "today", label: "今天", items: [] },
    { key: "week", label: "本周", items: [] },
    { key: "month", label: "本月", items: [] },
    { key: "earlier", label: "更早", items: [] },
  ];
  for (const item of items) {
    const activity = new Date(item.lastActivityAt);
    const bucket = activity >= startOfToday
      ? buckets[0]
      : activity >= startOfWeek
        ? buckets[1]
        : activity >= startOfMonth
          ? buckets[2]
          : buckets[3];
    bucket.items.push(item);
  }
  return buckets.filter((bucket) => bucket.items.length > 0);
}
