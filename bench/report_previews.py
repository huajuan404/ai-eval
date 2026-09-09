"""Embedded HTML artifacts: isolated thumbnails and an interactive enlarged view."""

from __future__ import annotations

import base64
import html
from pathlib import Path

from .scrub import scrub_text


def render_html_preview(path: Path, frame_id: str, label: str) -> str:
    """Caller must resolve and validate the declared artifact's containment first."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return '<div class="preview-empty">无法读取 HTML 预览，请打开原始作品</div>'
    source = scrub_text(source)
    sandbox = "allow-scripts"
    if path.suffix.lower() == ".svg":
        # SVG is displayed as an image: no active SVG scripting or foreign DOM content.
        encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
        source = (
            '<!doctype html><html><head><meta charset="utf-8"><style>'
            'html,body{margin:0;width:100%;height:100%;background:white}'
            'img{display:block;width:100%;height:100%;object-fit:contain}'
            '</style></head><body><img alt="SVG 作品" src="data:image/svg+xml;base64,'
            + encoded + '"></body></html>'
        )
        sandbox = ""
    source = html.escape(source, quote=True)
    title = html.escape(scrub_text(label), quote=True)
    identity = html.escape(frame_id, quote=True)
    return (
        '<div class="html-preview-stage">'
        f'<iframe id="{identity}" class="html-preview" title="{title} · 桌面预览" '
        f'sandbox="{sandbox}" referrerpolicy="no-referrer" loading="lazy" '
        f'tabindex="-1" aria-hidden="true" srcdoc="{source}"></iframe>'
        f'<button type="button" class="preview-open" data-preview="{identity}" '
        f'data-title="{title}" aria-label="放大查看 {title}"><span>放大查看 ↗</span></button>'
        '</div>'
    )


CSS = """
.work-grid{grid-template-columns:repeat(3,minmax(0,1fr));gap:24px 22px}
.work h4{font-size:13px;min-height:0;margin-bottom:10px}
.html-preview-stage{position:relative;width:100%;aspect-ratio:16/10;overflow:hidden;background:white;border:1px solid var(--line);border-radius:5px}
.html-preview{position:absolute;top:0;left:0;width:1440px;height:900px;max-width:none;border:0;transform-origin:top left;transform:scale(var(--preview-scale,.25));pointer-events:none;background:white}
.preview-open{position:absolute;inset:0;width:100%;border:0;background:transparent;color:#222;cursor:zoom-in}
.preview-open span{position:absolute;bottom:10px;right:10px;padding:6px 9px;border:1px solid #ddd;border-radius:3px;background:#fffffff2;font-size:11px;box-shadow:0 1px 5px #0001}
.preview-open:hover span{background:#222;color:white;border-color:#222}
.preview-open:focus-visible{outline:3px solid #555;outline-offset:-3px}
.work-open{font-size:12px}.work-measures strong{font-size:16px}.work-measures span{font-size:12px}
.work-repeat{font-size:11px}.work .preview-empty{height:auto;aspect-ratio:16/10}
.preview-dialog{position:fixed;inset:20px;margin:auto;width:calc(100vw - 40px);max-width:1600px;height:calc(100dvh - 40px);max-height:none;padding:0;border:1px solid var(--line);border-radius:7px;background:white;color:var(--fg)}
.preview-dialog[open]{display:flex;flex-direction:column}
.preview-dialog::backdrop{background:#0008}
.preview-toolbar{display:flex;align-items:center;flex-wrap:wrap;gap:12px;padding:12px 16px;border-bottom:1px solid var(--line);background:#fafafa}
.preview-toolbar h2{border:0;padding:0;margin:0 auto 0 0;font-size:15px;font-weight:550}
.preview-toolbar label{display:flex;align-items:center;gap:6px;font-size:12px}
.preview-toolbar select{max-width:260px;padding:6px;border:1px solid var(--line);border-radius:3px;background:white;color:var(--fg);font:inherit}
.preview-toolbar button,.preview-toolbar>a{border:1px solid var(--line);padding:6px 10px;border-radius:3px;background:white;color:var(--fg);font-size:12px}
.preview-toolbar button:disabled{opacity:.4;cursor:default}
.expanded-preview-body{flex:1;min-height:0;background:white}
.expanded-preview{display:block;width:100%;height:100%;border:0;background:white}
@media(max-width:1000px){.work-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:600px){.work-grid{grid-template-columns:1fr}.preview-dialog{inset:6px;width:calc(100vw - 12px);height:calc(100dvh - 12px)}.preview-toolbar{gap:7px;padding:10px}.preview-toolbar h2{width:100%;font-size:14px}.preview-toolbar select{max-width:190px}}
"""

DIALOG = """
<dialog id="html-preview-dialog" class="preview-dialog" aria-labelledby="html-preview-title">
<header class="preview-toolbar"><h2 id="html-preview-title">作品预览</h2>
<label>Runner <select id="html-preview-picker"></select></label>
<button type="button" id="html-preview-prev">上一份</button>
<button type="button" id="html-preview-next">下一份</button>
<a id="html-preview-original" target="_blank" rel="noopener noreferrer">打开原始作品 ↗</a>
<button type="button" id="html-preview-close" autofocus>关闭 ×</button></header>
<div id="html-preview-body" class="expanded-preview-body"></div></dialog>
"""

SCRIPT = """
<script id="report-previews">
(() => {
  const stages = [...document.querySelectorAll('.html-preview-stage')];
  const resize = stage => stage.style.setProperty('--preview-scale', stage.clientWidth / 1440);
  stages.forEach(resize);
  if (window.ResizeObserver) {
    const observer = new ResizeObserver(entries => entries.forEach(entry => resize(entry.target)));
    stages.forEach(stage => observer.observe(stage));
  } else {
    window.addEventListener('resize', () => stages.forEach(resize));
  }
  const dialog = document.getElementById('html-preview-dialog');
  const body = document.getElementById('html-preview-body');
  const picker = document.getElementById('html-preview-picker');
  const previous = document.getElementById('html-preview-prev');
  const next = document.getElementById('html-preview-next');
  const original = document.getElementById('html-preview-original');
  let choices = [];
  let current = 0;
  function show(index) {
    const button = choices[index];
    if (!button) return;
    const source = document.getElementById(button.dataset.preview);
    if (!source) return;
    current = index;
    picker.value = String(index);
    const frame = document.createElement('iframe');
    frame.className = 'expanded-preview';
    frame.setAttribute('sandbox', source.getAttribute('sandbox') || '');
    frame.referrerPolicy = 'no-referrer';
    frame.title = button.dataset.title + ' · 交互预览';
    frame.srcdoc = source.getAttribute('srcdoc');
    body.replaceChildren(frame);
    const link = button.closest('.work').querySelector('.work-open');
    if (link) original.href = link.href;
    previous.disabled = index === 0;
    next.disabled = index === choices.length - 1;
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-preview]');
    if (!button || typeof dialog.showModal !== 'function') return;
    choices = [...button.closest('.work-section').querySelectorAll('[data-preview]')];
    picker.replaceChildren(...choices.map((choice, index) => {
      const option = document.createElement('option');
      option.value = String(index);
      option.textContent = choice.dataset.title;
      return option;
    }));
    show(choices.indexOf(button));
    dialog.showModal();
  });
  picker.addEventListener('change', () => show(Number(picker.value)));
  previous.addEventListener('click', () => show(current - 1));
  next.addEventListener('click', () => show(current + 1));
  document.getElementById('html-preview-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => { body.replaceChildren(); choices = []; });
})();
</script>
"""
