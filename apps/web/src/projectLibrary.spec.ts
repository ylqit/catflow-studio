import { describe, expect, it } from "vitest";

import { buildLibraryQuery, groupLibraryItems, mergeLibraryItems } from "./projectLibrary";
import type { ProjectLibraryItemDto } from "./api/types";

function item(id: string, activity: string, collection?: string): ProjectLibraryItemDto {
  return {
    id,
    title: id,
    themeSummary: "安静日常",
    targetDurationSeconds: 12,
    aspectRatio: "9:16",
    collection: collection ? { id: collection, name: collection, colorKey: "sage", sortOrder: 0, archived: false, createdAt: activity, updatedAt: activity } : null,
    tags: [],
    stage: "story",
    attention: "normal",
    attentionReasons: [],
    pinned: false,
    archived: false,
    lastActivityAt: activity,
    createdAt: activity,
  };
}

describe("project library presentation", () => {
  it("groups activity into creator-friendly local calendar sections", () => {
    const now = new Date("2026-09-20T18:00:00+08:00");
    const groups = groupLibraryItems([
      item("today", "2026-09-20T09:00:00+08:00"),
      item("week", "2026-09-15T09:00:00+08:00"),
      item("month", "2026-09-05T09:00:00+08:00"),
      item("earlier", "2026-08-20T09:00:00+08:00"),
    ], "date", now);

    expect(groups.map((group) => [group.label, group.items.map((entry) => entry.id)])).toEqual([
      ["今天", ["today"]],
      ["本周", ["week"]],
      ["本月", ["month"]],
      ["更早", ["earlier"]],
    ]);
  });

  it("serializes shareable filters without leaking layout preferences into the API", () => {
    const query = buildLibraryQuery({
      q: "雨天",
      systemView: "needs_attention",
      collectionId: "collection-1",
      tags: ["室内", "毛巾"],
      stage: "editing",
      sort: "activity",
      cursor: "next-page",
      limit: 36,
    });

    expect(query.toString()).toBe("q=%E9%9B%A8%E5%A4%A9&systemView=needs_attention&collectionId=collection-1&tags=%E5%AE%A4%E5%86%85&tags=%E6%AF%9B%E5%B7%BE&stage=editing&sort=activity&cursor=next-page&limit=36");
    expect(query.has("layout")).toBe(false);
  });

  it("appends cursor pages without duplicating a project", () => {
    expect(mergeLibraryItems([item("one", "2026-09-03T00:00:00Z")], [
      item("one", "2026-09-03T00:00:00Z"),
      item("two", "2026-09-02T00:00:00Z"),
    ]).map((entry) => entry.id)).toEqual(["one", "two"]);
  });
});
