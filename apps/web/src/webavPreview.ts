export interface WebAvPreviewController {
  play(): void;
  pause(): void;
  destroy(): void;
}

export async function mountWebAvPreview(
  container: HTMLElement,
  sourceUrl: string,
  interval: { startMs: number; endMs: number },
): Promise<WebAvPreviewController> {
  if (!("VideoDecoder" in window) || !("AudioDecoder" in window)) {
    throw new Error("当前浏览器不支持 WebCodecs，仍可使用原生视频预览和 FFmpeg 导出");
  }
  if (interval.startMs < 0 || interval.endMs <= interval.startMs) {
    throw new Error("WebAV preview interval is invalid");
  }
  const [{ AVCanvas }, { MP4Clip, VisibleSprite }] = await Promise.all([
    import("@webav/av-canvas"),
    import("@webav/av-cliper"),
  ]);
  const response = await fetch(sourceUrl);
  if (!response.ok || response.body === null) {
    throw new Error("WebAV source video could not be loaded");
  }
  const original = new MP4Clip(response.body);
  await original.ready;
  let clip = original;
  if (interval.startMs > 0) {
    const [, tail] = await clip.split(interval.startMs * 1000);
    clip = tail;
  }
  const durationMicros = (interval.endMs - interval.startMs) * 1000;
  if (clip.meta.duration > durationMicros) {
    const [head] = await clip.split(durationMicros);
    clip = head;
  }
  const sprite = new VisibleSprite(clip);
  sprite.rect.x = 0;
  sprite.rect.y = 0;
  sprite.rect.w = 720;
  sprite.rect.h = 1280;
  sprite.rect.angle = 0;
  sprite.time = { offset: 0, duration: durationMicros };
  sprite.interactable = "disabled";
  container.replaceChildren();
  const canvas = new AVCanvas(container, { bgColor: "#1f1c1a", width: 720, height: 1280 });
  await canvas.addSprite(sprite);
  await canvas.previewFrame(0);

  return {
    play: () => canvas.play({ start: 0, end: durationMicros }),
    pause: () => canvas.pause(),
    destroy: () => {
      canvas.destroy();
      sprite.destroy();
      if (clip !== original) original.destroy();
    },
  };
}
