const placeholderCache = {};

function getMangaPlaceholder(id, width = 160, height = 230) {
  if (placeholderCache[id]) return placeholderCache[id];
  const keys = Object.keys(placeholderCache);
  if (keys.length >= 30) {
    return (placeholderCache[id] = placeholderCache[keys[Math.floor(Math.random() * keys.length)]]);
  }
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  drawMangaPlaceholder(canvas);
  return (placeholderCache[id] = canvas.toDataURL('image/webp', 0.9));
}

function drawMangaPlaceholder(canvas) {
  const PANEL   = '#0c192a';
  const MARGIN  = '#070f1b';
  const OUTLINE = '#456a92';
  const PAD_X   = 7;
  const PAD_Y   = 7;
  const GAP     = 5;

  function splitRect(rect) {
    const { x, y, w, h } = rect;
    const horiz = w < h ? true : h < w ? false : Math.random() > 0.5;
    const ratio = 0.3 + Math.random() * 0.4;
    if (horiz) {
      const split = Math.floor(h * ratio);
      return [
        { x, y,            w, h: split - GAP },
        { x, y: y + split, w, h: h - split   },
      ];
    }
    const split = Math.floor(w * ratio);
    return [
      { x,           y, w: split - GAP, h },
      { x: x + split, y, w: w - split,  h },
    ];
  }

  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  const targetCount = 4 + Math.floor(Math.random() * 3);
  const panels = [{ x: PAD_X, y: PAD_Y, w: W - PAD_X * 2, h: H - PAD_Y * 2 }];

  while (panels.length < targetCount) {
    const idx = panels.reduce((best, p, i) =>
      (p.w >= 12 && p.h >= 12 && p.w * p.h > (panels[best]?.w ?? 0) * (panels[best]?.h ?? 0)) ? i : best
    , -1);
    if (idx === -1) break;
    panels.splice(idx, 1, ...splitRect(panels[idx]));
  }

  ctx.fillStyle = MARGIN;
  ctx.fillRect(0, 0, W, H);

  for (const p of panels) {
    if (p.w < 6 || p.h < 6) continue;
    ctx.fillStyle = PANEL;
    ctx.fillRect(p.x, p.y, p.w, p.h);
    ctx.strokeStyle = OUTLINE;
    ctx.lineWidth = 1;
    ctx.strokeRect(p.x + 0.5, p.y + 0.5, p.w - 1, p.h - 1);
  }
}