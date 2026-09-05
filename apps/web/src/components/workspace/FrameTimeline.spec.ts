import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import FrameTimeline from "./FrameTimeline.vue";

describe("one frame coordinate space", () => {
  it("positions both handles at the exact selected-window boundaries", () => {
    const view = mount(FrameTimeline, { props: { totalFrames: 289, modelValue: { startFrame: 144, endFrame: 289 }, currentFrame: 150 } });
    expect(view.get('[data-testid="in-handle"]').attributes("style")).toContain(`${144 / 289 * 100}%`);
    expect(view.get('[data-testid="out-handle"]').attributes("style")).toContain("100%");
    expect(view.get('[data-testid="selected-window"]').attributes("style")).toContain(`${144 / 289 * 100}%`);
  });
  it("adjusts one frame with keyboard and never changes the coordinate domain", async () => {
    const view = mount(FrameTimeline, { props: { totalFrames: 289, modelValue: { startFrame: 144, endFrame: 289 }, currentFrame: 0 } });
    await view.get('[data-testid="in-handle"]').trigger("keydown", { key: "ArrowRight" });
    expect(view.emitted("update:modelValue")?.[0]).toEqual([{ startFrame: 145, endFrame: 289 }]);
    expect(view.emitted("seek")?.[0]).toEqual([145]);
    expect(view.get('[data-testid="out-handle"]').attributes("aria-valuemax")).toBe("289");
  });
  it("locks the frozen selection after submission", async () => {
    const view = mount(FrameTimeline, { props: { totalFrames: 289, modelValue: { startFrame: 144, endFrame: 289 }, currentFrame: 0, disabled: true } });
    await view.get('[data-testid="in-handle"]').trigger("keydown", { key: "ArrowRight" });
    expect(view.emitted("update:modelValue")).toBeUndefined();
    await view.get('input[aria-label="预览定位帧"]').setValue("180");
    expect(view.emitted("seek")?.[0]).toEqual([180]);
    expect(view.emitted("update:modelValue")).toBeUndefined();
  });
  it("maps timecode into the same integer frame selection", async () => {
    const view = mount(FrameTimeline, { props: { totalFrames: 289, modelValue: { startFrame: 0, endFrame: 289 }, currentFrame: 0 } });
    await view.get('input[aria-label="入点时间码"]').setValue("00:00:06:00");
    expect(view.emitted("update:modelValue")?.[0]).toEqual([{ startFrame: 144, endFrame: 289 }]);
    expect(view.emitted("seek")?.[0]).toEqual([144]);
    await view.get('input[aria-label="出点时间码"]').setValue("00:00:12:01");
    expect(view.emitted("seek")?.[1]).toEqual([288]);
  });
});
