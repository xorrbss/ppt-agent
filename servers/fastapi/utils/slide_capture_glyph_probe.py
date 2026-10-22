"""Fail-closed pixel confirmation of Range-based glyph clipping suspects."""

from __future__ import annotations

import base64
import asyncio
import html as html_lib
import io
import json
import re
import secrets
import urllib.parse
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from utils.authored_dom_quality import ensure_authored_qids
from models.canvas_defaults import DEFAULT_CANVAS_SIZE_PX
from utils.hashing import sha256_hex, sha256_prefixed

GLYPH_CLIPPING_SENSOR_VERSION = "glyph-paint-v1-current-reference"
MAX_GLYPH_SUSPECTS = 4
MAX_GLYPH_OWNERS = 128
_W, _H = DEFAULT_CANVAS_SIZE_PX

_SCRIPT = r"""
window.__codexGlyphClipProof = (async () => {
  const frames = Array.from(document.querySelectorAll('iframe'));
  const config = __CONFIG__;
  const targets = config.targets, binding = config.binding;
  if(frames.length!==targets.length*2 || frames.some(frame=>frame.getAttribute('srcdoc')!==config.source))
    return {...binding,results:targets.map(qid=>({qid,complete:false,reason:'source_document_mismatch'}))};
  const plain = rect => ({x:rect.x,y:rect.y,width:rect.width,height:rect.height});
  const keys = ['fontFamily','fontSize','fontWeight','fontStyle','fontStretch','fontFeatureSettings',
    'fontVariationSettings','lineHeight','letterSpacing','wordSpacing','textTransform','writingMode',
    'direction','position','display','transform'];
  const fragments = (doc,node) => {
    const out=[]; const walk=doc.createTreeWalker(node,NodeFilter.SHOW_TEXT);
    for(let text=walk.nextNode();text;text=walk.nextNode()) {
      if(!text.textContent.trim() || text.parentElement.closest('[data-qid]')!==node) continue;
      const range=doc.createRange();range.selectNodeContents(text);
      out.push(...Array.from(range.getClientRects()).map(plain));
    }
    return out;
  };
  const snapshot = doc => Array.from(doc.querySelectorAll('[data-qid]')).map(node=>{
    const style=doc.defaultView.getComputedStyle(node);
    return {qid:node.getAttribute('data-qid'),bbox:plain(node.getBoundingClientRect()),
      style:Object.fromEntries(keys.map(key=>[key,style[key]])),fragments:fragments(doc,node)};
  });
  const mask = (doc,target,unclip) => {
    const style=doc.createElement('style');
    style.textContent=`html,body{background:#000!important}
      *:not(html):not(body){background-color:transparent!important;background-image:none!important;
      border-color:transparent!important;box-shadow:none!important;outline-color:transparent!important;
      color:transparent!important;-webkit-text-fill-color:transparent!important;text-shadow:none!important}
      *::before,*::after{visibility:hidden!important}
      img,video,canvas,svg{visibility:hidden!important}`;
    doc.head.appendChild(style);
    for(const node of doc.querySelectorAll('*')) {
      for(const property of ['color','-webkit-text-fill-color','background-color','border-color','outline-color'])
        node.style.setProperty(property,'transparent','important');
      for(const property of ['background-image','box-shadow','text-shadow']) node.style.setProperty(property,'none','important');
      if(['IMG','VIDEO','CANVAS','SVG'].includes(node.tagName)) node.style.setProperty('visibility','hidden','important');
    }
    doc.documentElement.style.setProperty('background-color','#000','important');
    doc.body.style.setProperty('background-color','#000','important');
    for(const node of [target,...target.querySelectorAll('*')]) {
      if(node.closest('[data-qid]')!==target) continue;
      node.style.setProperty('color','#fff','important');
      node.style.setProperty('-webkit-text-fill-color','#fff','important');
    }
    if(unclip) for(let node=target;node;node=node.parentElement) {
      node.style.setProperty('overflow-x','visible','important');
      node.style.setProperty('overflow-y','visible','important');
    }
  };
  const wait = async frame => {
    if(frame.contentDocument?.readyState!=='complete') await new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
    const doc=frame.contentDocument;
    await doc.fonts.ready;
    await Promise.all(Array.from(doc.images).map(image=>image.decode?image.decode().catch(()=>{}):Promise.resolve()));
  };
  await Promise.all(frames.map(wait));
  await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  const results=[];
  for(let index=0;index<targets.length;index++) {
    const pair=frames.slice(index*2,index*2+2).map(frame=>frame.contentDocument);
    try {
      if(pair.length!==2) throw Error('missing_frame');
      const nodes=pair.map(doc=>Array.from(doc.querySelectorAll('[data-qid]')).filter(node=>node.getAttribute('data-qid')===targets[index]));
      if(nodes.some(matches=>matches.length!==1)) throw Error('qid_not_unique');
      for(let side=0;side<2;side++) {
        const doc=pair[side], target=nodes[side][0];
        if(doc.defaultView.innerWidth!==config.width || doc.defaultView.innerHeight!==config.height || doc.defaultView.devicePixelRatio!==1) throw Error('viewport_mismatch');
        if(doc.querySelectorAll('[data-qid]').length>128) throw Error('owner_limit');
        for(const element of doc.querySelectorAll('*')) {
          const css=doc.defaultView.getComputedStyle(element);
          if(css.animationName!=='none'||css.transitionDuration.split(',').some(value=>parseFloat(value)!==0)) throw Error('active_motion');
          if(['::before','::after'].some(pseudo=>!['none','normal'].includes(doc.defaultView.getComputedStyle(element,pseudo).content))) throw Error('pseudo_paint');
        }
        const targetStyle=doc.defaultView.getComputedStyle(target);
        if(target.children.length || targetStyle.backgroundImage!=='none' ||
           !['transparent','rgba(0, 0, 0, 0)'].includes(targetStyle.backgroundColor) ||
           targetStyle.boxShadow!=='none' || ['Top','Right','Bottom','Left'].some(side=>parseFloat(targetStyle['border'+side+'Width'])>0) ||
           ['::before','::after'].some(pseudo=>!['none','normal'].includes(doc.defaultView.getComputedStyle(target,pseudo).content))) throw Error('non_leaf_text_paint');
        if(targetStyle.textShadow!=='none'||parseFloat(targetStyle.webkitTextStrokeWidth)>0||targetStyle.textDecorationLine!=='none') throw Error('unsupported_text_ink');
        for(let node=target;node;node=node.parentElement) {
          const css=doc.defaultView.getComputedStyle(node);
          if(css.transform!=='none'||css.filter!=='none'||css.backdropFilter!=='none'||css.clipPath!=='none'||
             (css.maskImage||'none')!=='none'||css.mixBlendMode!=='normal'||Number(css.opacity)!==1||
             css.writingMode!=='horizontal-tb'||css.visibility!=='visible'||css.display==='none') throw Error('unsupported_effect');
          if(!doc.fonts.check(css.font)) throw Error('font_unavailable');
        }
        const ranges=fragments(doc,target);
        if(!ranges.length||ranges.some(r=>r.x<2||r.y<2||r.x+r.width>config.width-2||r.y+r.height>config.height-2)) throw Error('reference_viewport_incomplete');
      }
      const before=pair.map(snapshot);
      if(JSON.stringify(before[0])!==JSON.stringify(before[1])) throw Error('frame_geometry_mismatch');
      pair.forEach((doc,side)=>mask(doc,nodes[side][0],side===1));
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      const after=pair.map(snapshot);
      if(after.some((value,side)=>JSON.stringify(value)!==JSON.stringify(before[side]))) throw Error('geometry_or_font_changed');
      results.push({qid:targets[index],complete:true,owners:before[0]});
    } catch(error) { results.push({qid:targets[index],complete:false,reason:String(error.message)}); }
  }
  const marker=document.createElement('meta');marker.id='__codex_glyph_clip_proof';
  const payload={...binding,results};
  marker.setAttribute('content',encodeURIComponent(JSON.stringify(payload)));document.head.appendChild(marker);
  return payload;
})();
"""


def build_glyph_probe(
    html: str, metrics: Mapping[str, Any], *,
    width_px: int = _W, height_px: int = _H,
) -> dict[str, Any] | None:
    suspects = [item for item in metrics.get("elements", ()) if isinstance(item, Mapping) and item.get("range_clip_suspect") is True]
    if not suspects:
        return None
    enriched = ensure_authored_qids(html)
    render_digest = sha256_prefixed(enriched.encode())
    valid_source = render_digest == metrics.get("render_digest")
    targets = [item["qid"] for item in suspects[:MAX_GLYPH_SUSPECTS] if isinstance(item.get("qid"), str)] if valid_source else []
    if len(enriched.encode()) > 512 * 1024 or len(metrics.get("elements", ())) > MAX_GLYPH_OWNERS:
        targets = []
    height = max(1, len(targets) * 2) * height_px
    frames = "".join(f'<iframe srcdoc="{html_lib.escape(enriched, quote=True)}"></iframe>' for _ in range(len(targets) * 2))
    binding = {"epoch": secrets.token_hex(16), "render_digest": render_digest, "source_digest": metrics.get("source_digest"), "sensor_version": GLYPH_CLIPPING_SENSOR_VERSION}
    script = _SCRIPT.replace("__CONFIG__", json.dumps({"targets": targets, "binding": binding, "source": enriched, "width": width_px, "height": height_px}).replace("<", "\\u003c"))
    base = f'<!doctype html><html><head><style>html,body{{margin:0;width:{width_px}px;height:{height}px;overflow:hidden;background:#000}}iframe{{display:block;border:0;width:{width_px}px;height:{height_px}px}}</style></head><body>{frames}'
    return {"document": base + f'<script>{script}</script></body></html>', "cdp_document": base + '</body></html>', "script": script, "width": width_px, "height": height, "frame_height": height_px, "targets": targets, **binding}


def read_glyph_probe_proof(dumped_dom: bytes) -> object:
    match = re.search(r'<meta\s+id="__codex_glyph_clip_proof"\s+content="([^"]*)"', dumped_dom.decode("utf-8", errors="replace"))
    try:
        return json.loads(urllib.parse.unquote(html_lib.unescape(match.group(1)))) if match else None
    except (ValueError, TypeError):
        return None


def apply_glyph_probe(metrics, plan, proof, probe_png, original_png):
    from PIL import Image, ImageChops

    result = json.loads(json.dumps(metrics))
    by_qid = {item.get("qid"): item for item in result.get("elements", ()) if isinstance(item, dict)}
    bound = isinstance(proof, dict) and all(proof.get(key) == plan.get(key) for key in ("epoch", "render_digest", "source_digest", "sensor_version"))
    records = proof.get("results") if bound else None
    proof_by_qid = {item.get("qid"): item for item in records if isinstance(item, dict)} if isinstance(records, list) else {}
    image = None
    try:
        image = Image.open(io.BytesIO(probe_png)).convert("L") if probe_png else None
    except (ValueError, OSError):
        pass
    for qid, item in by_qid.items():
        if item.get("range_clip_suspect") is not True:
            continue
        evidence = {
            "sensor_version": GLYPH_CLIPPING_SENSOR_VERSION, "status": "unknown",
            "reason": plan.get("failure", "probe_incomplete"), "render_digest": metrics.get("render_digest"),
            "source_digest": metrics.get("source_digest"),
            "original_png_digest": sha256_hex(original_png),
        }
        item["glyph_clipping_evidence"] = evidence
        measurement = item.get("measurement")
        if not isinstance(measurement, dict):
            measurement = {}
            item["measurement"] = measurement
        measurement["clipping"] = False
        if qid not in plan["targets"]:
            evidence["reason"] = "source_or_probe_limit"
            continue
        observed = proof_by_qid.get(qid, {})
        plan_width = int(plan.get("width", _W))
        if observed.get("complete") is not True or image is None or image.size != (plan_width, plan["height"]):
            if observed.get("reason") in {"missing_frame", "qid_not_unique", "viewport_mismatch", "owner_limit", "unsupported_effect", "font_unavailable", "reference_viewport_incomplete", "frame_geometry_mismatch", "geometry_or_font_changed", "non_leaf_text_paint", "active_motion", "pseudo_paint", "unsupported_text_ink", "source_document_mismatch"}:
                evidence["reason"] = observed["reason"]
            continue
        owners = observed.get("owners")
        if (
            not isinstance(owners, list) or len(owners) != len(by_qid)
            or any(not isinstance(owner, dict) for owner in owners)
            or {owner.get("qid") for owner in owners} != set(by_qid)
            or metrics.get("fonts_ready") is not True or item.get("font_loaded") is not True
        ):
            continue
        invariant = True
        for owner in owners:
            original = by_qid.get(owner.get("qid"), {})
            if owner.get("bbox") != original.get("bbox") or owner.get("fragments") != original.get("text_fragments"):
                invariant = False
                break
            for key in ("fontFamily", "fontSize", "fontWeight", "lineHeight", "transform", "position", "display"):
                if not isinstance(original.get("computed_style", {}).get(key), str) or owner.get("style", {}).get(key) != original["computed_style"][key]:
                    invariant = False
        if not invariant:
            evidence["reason"] = "original_geometry_or_font_mismatch"
            continue
        frame_height = int(plan.get("frame_height", _H))
        offset = plan["targets"].index(qid) * 2 * frame_height
        current = image.crop((0, offset, plan_width, offset + frame_height))
        reference = image.crop((0, offset + frame_height, plan_width, offset + 2 * frame_height))
        histogram = reference.histogram()
        ink = sum(histogram[1:])
        if not ink:
            evidence["reason"] = "reference_ink_missing"
            continue
        lost = ImageChops.subtract(reference, current)
        loss = sum(lost.histogram()[1:])
        equal = ImageChops.difference(reference, current).getbbox() is None
        evidence.update({
            "status": "measured", "reason": None, "geometry_invariant": True,
            "font_invariant": True, "reference_ink_pixels": ink, "paint_loss_pixels": loss,
            "current_mask_digest": sha256_hex(current.tobytes()),
            "reference_mask_digest": sha256_hex(reference.tobytes()),
        })
        if loss or equal:
            measurement["clipping"] = True
        else:
            evidence.update(status="unknown", reason="mask_difference_without_loss")
        if equal:
            item.update(clipped=False, glyph_clipped=False, line_box_clipped=False, glyph_clip_cause="none")
    return result


def _cdp_steps(html, metrics, png, frame_id, *, width_px=_W, height_px=_H):
    plan = build_glyph_probe(
        html, metrics, width_px=width_px, height_px=height_px
    )
    if plan is None:
        return metrics
    proof, probe_png = None, None
    try:
        if plan["targets"]:
            # A new internal document does not inherit the authored document's
            # nonce/frame-src restrictions. Network denial and CSP enforcement
            # remain enabled; every srcdoc retains its original CSP unchanged.
            navigation = yield "Page.navigate", {"url": "about:blank"}
            frame_id = navigation.get("frameId", frame_id)
            yield "Emulation.setDeviceMetricsOverride", {"width": plan["width"], "height": plan["height"], "deviceScaleFactor": 1, "mobile": False}
            yield "Page.setDocumentContent", {"frameId": frame_id, "html": plan["cdp_document"]}
            response = yield "Runtime.evaluate", {"expression": plan["script"], "awaitPromise": True, "returnByValue": True}
            if response.get("exceptionDetails"):
                plan["failure"] = "probe_script_failed"
            proof = response.get("result", {}).get("value")
            if not isinstance(proof, dict):
                plan.setdefault("failure", "probe_result_missing")
            response = yield "Page.captureScreenshot", {"format": "png", "fromSurface": True, "captureBeyondViewport": False}
            probe_png = base64.b64decode(response.get("data", ""), validate=True)
    except Exception:
        plan["failure"] = "probe_transport_failed"
    return apply_glyph_probe(metrics, plan, proof, probe_png, png)


async def probe_glyph_clipping_async(
    html, metrics, png, *, command, frame_id, width_px=_W, height_px=_H
):
    steps = _cdp_steps(
        html, metrics, png, frame_id, width_px=width_px, height_px=height_px
    )
    answer = None
    while True:
        try:
            method, params = steps.send(answer)
        except StopIteration as done:
            return done.value
        try:
            answer = await command(method, params)
        except Exception as error:
            try:
                steps.throw(error)
            except StopIteration as done:
                return done.value


def probe_glyph_clipping_sync(
    html, metrics, png, *, command, frame_id, width_px=_W, height_px=_H
):
    steps = _cdp_steps(
        html, metrics, png, frame_id, width_px=width_px, height_px=height_px
    )
    answer = None
    while True:
        try:
            method, params = steps.send(answer)
        except StopIteration as done:
            return done.value
        try:
            answer = command(method, params)
        except Exception as error:
            try:
                steps.throw(error)
            except StopIteration as done:
                return done.value


async def probe_glyph_clipping_file(
    html, metrics, png, *, directory, screenshot_args,
    # The screenshot subprocess owns this deadline; see capture_slide_evidence.
    timeout,  # noqa: ASYNC109
    count_runtime, run_screenshot, wait_for_output,
    width_px=_W, height_px=_H,
):
    plan = build_glyph_probe(
        html, metrics, width_px=width_px, height_px=height_px
    )
    if plan is None:
        return metrics
    proof, probe_png = None, None
    try:
        if plan["targets"]:
            source = Path(directory) / "glyph-probe.html"
            target = Path(directory) / "glyph-probe.png"
            source.write_text(plan["document"], encoding="utf-8")
            args = [value for value in screenshot_args[:-1] if value != "--dump-dom" and not value.startswith(("--window-size=", "--screenshot=", "--user-data-dir="))]
            args += [f"--user-data-dir={Path(directory) / 'glyph-profile'}", f"--window-size={width_px},{plan['height']}", f"--screenshot={target}", "--dump-dom", source.as_uri()]
            count_runtime("chrome_process_launches")
            dumped = await asyncio.to_thread(run_screenshot, args, timeout)
            if await wait_for_output(str(target)):
                proof, probe_png = read_glyph_probe_proof(dumped), target.read_bytes()
    except Exception:
        pass
    return apply_glyph_probe(metrics, plan, proof, probe_png, png)
