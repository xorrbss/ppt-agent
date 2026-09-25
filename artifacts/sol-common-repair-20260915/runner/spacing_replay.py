from __future__ import annotations
import asyncio, hashlib, json, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ROOT = REPO / 'artifacts/sol-common-repair-20260915'
SOURCE = REPO / 'artifacts/sol-speed-quality-20260915'
sys.path.insert(0, str(REPO / 'servers/fastapi'))
from utils.authored_qid_namespace import ensure_authored_qids
from utils.slide_capture import render_html_to_png_with_metrics
from utils.authored_dom_quality import inspect_authored_dom
from services.authored_composition_policy import blocking_safety_issues

def sha(s): return hashlib.sha256(s.encode('utf-8')).hexdigest()
def write(path, value): path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8', newline='')

def compact(html, items):
    index = {x['qid']: x for x in items}
    def ancestors(item):
        result=[]; q=item.get('parent_qid')
        while q in index and q not in result:
            result.append(q); q=index[q].get('parent_qid')
        return result
    def px(value):
        try: return float(str(value).replace('px',''))
        except (ValueError, TypeError): return 0.0
    selected=[]; rules=[]
    for item in items:
        if item.get('has_owned_text') or not item.get('has_owned_paint') or item.get('intentional') or item.get('semantic_role') == 'decoration': continue
        box=item['bbox']
        for q in ancestors(item):
            parent=index[q]; pb=parent['bbox']
            if parent.get('has_owned_paint') and box['y']>=pb['y'] and box['y']+box['height']>pb['y']+pb['height']+2:
                if any(x.get('has_owned_text') and item['qid'] in ancestors(x) for x in items): selected.append(item['qid'])
                break
    for q in selected:
        style=index[q].get('computed_style',{})
        decl='min-height:0!important;'+''.join(f'padding-{side.lower()}:{min(4.0,px(style.get("padding"+side)))}px!important;' for side in ['Top','Bottom'])
        rules.append(f'[data-qid="{q}"]{{{decl}}}')
        for item in items:
            if q not in ancestors(item) or not item.get('has_owned_text'): continue
            font=item.get('font_size',0); s=item.get('computed_style',{}); line=px(s.get('lineHeight'))
            if font<=0: continue
            height=min(line,font*1.1) if line>0 else font*1.1
            rules.append(f'[data-qid="{item["qid"]}"]{{line-height:{height:.3f}px!important;margin-top:{min(2.0,px(s.get("marginTop")))}px!important;margin-bottom:0!important;}}')
    return (html.replace('</head>','<style>'+''.join(rules)+'</style></head>') if rules else html), selected

async def main():
    manifest=json.loads((ROOT/'representative-10.json').read_text(encoding='utf-8'))
    rows=[]
    for n,sample in enumerate(manifest['samples'],1):
        folder=SOURCE/sample['sourcePath']; out=ROOT/'spacing-replay-10'/f'case-{n:02d}'; out.mkdir(parents=True,exist_ok=True)
        evidence=json.loads((folder/'render-evidence.json').read_text(encoding='utf-8'))
        expected=evidence['htmlDigest'].removeprefix('sha256:')
        source=next(p for p in [folder/'normalized-slide.html',folder/'slide.html',folder/'raw-slide.html'] if p.exists() and hashlib.sha256(p.read_bytes()).hexdigest()==expected)
        original=source.read_text(encoding='utf-8'); html=ensure_authored_qids(original)
        start=time.perf_counter_ns(); candidate,selected=compact(html,evidence['browser_metrics']['elements']); repair_end=time.perf_counter_ns()
        capture=await render_html_to_png_with_metrics(candidate,timeout=60,fit=False,fresh=True,deck_scope=f'spacing-replay-{n}')
        render_end=time.perf_counter_ns(); report=inspect_authored_dom(candidate,capture.browser_metrics,reference_mode='style_reference',strict_structural_off_canvas=True); end=time.perf_counter_ns()
        old={x['qid']:x for x in evidence['browser_metrics']['elements']}; new={x['qid']:x for x in capture.browser_metrics['elements']}
        owned=[q for q in old if old[q].get('has_owned_text')]
        row={**sample,'ordinal':n,'selectedQids':selected,'candidateChanged':candidate!=html,'noOpBytesPreserved':bool(selected) or candidate==html,'sourceSha256':expected,'preparedSha256':sha(html),'candidateSha256':sha(candidate),'htmlIdentityMatch':capture.html_digest=='sha256:'+sha(candidate),'providerCalls':0,'totalMs':(end-start)/1e6,'repairMs':(repair_end-start)/1e6,'renderMs':(render_end-repair_end)/1e6,'inspectionMs':(end-render_end)/1e6,'textPreserved':all(old[q].get('owned_text')==new.get(q,{}).get('owned_text') for q in owned),'fontSizesPreserved':all(old[q].get('font_size')==new.get(q,{}).get('font_size') for q in owned),'blockers':[x.to_dict() for x in blocking_safety_issues(report)]}
        (out/'original.html').write_text(original,encoding='utf-8',newline='');(out/'prepared.html').write_text(html,encoding='utf-8',newline='');(out/'candidate.html').write_text(candidate,encoding='utf-8',newline='');(out/'render.png').write_bytes(capture.png)
        write(out/'result.json',{**row,'browser_metrics':capture.browser_metrics});rows.append(row);write(ROOT/'spacing-replay-10/summary.json',rows)
        print(json.dumps({'case':n,'changed':row['candidateChanged'],'blockers':len(row['blockers']),'textPreserved':row['textPreserved'],'fontSizesPreserved':row['fontSizesPreserved']},ensure_ascii=False),flush=True)

if __name__=='__main__': asyncio.run(main())
