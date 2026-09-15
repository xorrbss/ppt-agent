from __future__ import annotations
import argparse, asyncio, hashlib, json, sys, time
from pathlib import Path
from html.parser import HTMLParser

REPO=Path(__file__).resolve().parents[3]
ROOT=REPO/'artifacts/sol-common-repair-20260915'
SOURCE=REPO/'artifacts/sol-speed-quality-20260915'
sys.path.insert(0,str(REPO/'servers/fastapi'))
from utils.authored_qid_namespace import ensure_authored_qids
from utils.slide_capture import render_html_to_png_with_metrics
from services.authored_local_fit_policy import local_fit_policy,html_digest
from utils.authored_dom_quality import inspect_authored_dom
from services.authored_composition_policy import blocking_safety_issues

def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2,default=str),encoding='utf-8',newline='')
class Images(HTMLParser):
    def __init__(self):super().__init__();self.sources=[]
    def handle_starttag(self,tag,attrs):
        if tag=='img':self.sources.append(dict(attrs).get('src'))
def images(html):p=Images();p.feed(html);return p.sources

async def capture_product_fit(html, *, slide_index, deck_scope, width_px=1280, height_px=720):
    prepared=ensure_authored_qids(html);digest=html_digest(prepared);policy=local_fit_policy(author_revision=digest,original_html_digest=digest,slide_index=slide_index)
    fit_started=time.perf_counter_ns();fitted=await render_html_to_png_with_metrics(prepared,timeout=60,deck_scope=f'{deck_scope}-fit',fit=True,fit_policy=policy,fresh=True,width_px=width_px,height_px=height_px);fit_end=time.perf_counter_ns()
    final=fitted.fitted_html or prepared;capture=fitted
    if final!=prepared:
        capture=await render_html_to_png_with_metrics(final,timeout=60,deck_scope=f'{deck_scope}-verify',fit=False,fit_policy={**policy,'baseline':dict((fitted.fit_report or {}).get('baseline')or{})},fresh=True,width_px=width_px,height_px=height_px)
    capture_end=time.perf_counter_ns()
    if capture.html_digest != html_digest(final):raise RuntimeError('final HTML and PNG capture identity diverged')
    return final,capture,{'fitRenderMs':(fit_end-fit_started)/1e6,'verifyRenderMs':(capture_end-fit_end)/1e6,'fitReport':fitted.fit_report or {}}

async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--ordinals',default='1,9');parser.add_argument('--output',default='product-fit-replay');args=parser.parse_args()
    wanted={int(x) for x in args.ordinals.split(',')};allrows=json.loads((ROOT/'representative-10.json').read_text(encoding='utf-8'))['samples'];base=ROOT/args.output;base.mkdir(parents=True,exist_ok=True)
    freeze={str(p.relative_to(REPO)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (REPO/'servers/fastapi/utils').glob('slide_capture_fit*.py')}
    freeze['servers/fastapi/utils/authored_dom_text_escape.py']=hashlib.sha256((REPO/'servers/fastapi/utils/authored_dom_text_escape.py').read_bytes()).hexdigest();write(base/'source-freeze.json',freeze)
    rows=json.loads((base/'summary.json').read_text(encoding='utf-8')) if (base/'summary.json').exists() else []
    rows=[r for r in rows if r['ordinal'] not in wanted]
    for ordinal,sample in enumerate(allrows,1):
        if ordinal not in wanted:continue
        folder=SOURCE/sample['sourcePath'];out=base/f'case-{ordinal:02d}';out.mkdir(exist_ok=True)
        evidence=json.loads((folder/'render-evidence.json').read_text(encoding='utf-8'));expected=evidence['htmlDigest'].removeprefix('sha256:')
        path=next(p for p in [folder/'normalized-slide.html',folder/'slide.html',folder/'raw-slide.html'] if p.exists() and hashlib.sha256(p.read_bytes()).hexdigest()==expected)
        context_path=folder/'source.json';context_path=context_path if context_path.exists() else folder.parent/'A/source.json';source_context=json.loads(context_path.read_text(encoding='utf-8'));original=path.read_text(encoding='utf-8');started=time.perf_counter_ns();prepared=ensure_authored_qids(original);digest=html_digest(prepared);policy=local_fit_policy(author_revision=digest,original_html_digest=digest,slide_index=int(source_context['slideIndex']))
        try:
            fitted=await render_html_to_png_with_metrics(prepared,timeout=60,deck_scope=f'product-fit-{ordinal}',fit=True,fit_policy=policy,fresh=True)
            fit_end=time.perf_counter_ns();final=fitted.fitted_html or prepared;fit_report=fitted.fit_report or {};capture=fitted
            if final!=prepared:
                capture=await render_html_to_png_with_metrics(final,timeout=60,deck_scope=f'product-verify-{ordinal}',fit=False,fit_policy={**policy,'baseline':dict(fit_report.get('baseline')or{})},fresh=True)
            capture_end=time.perf_counter_ns();report=inspect_authored_dom(final,capture.browser_metrics,reference_mode='style_reference',strict_structural_off_canvas=True);end=time.perf_counter_ns();blockers=blocking_safety_issues(report)
            old={x['qid']:x for x in evidence['browser_metrics']['elements']};new={x['qid']:x for x in capture.browser_metrics['elements']};owned=[q for q in old if old[q].get('has_owned_text')]
            receipt={**sample,'ordinal':ordinal,'status':'completed','providerCalls':0,'fitReport':fit_report,'verifyFitReport':capture.fit_report,'changed':final!=prepared,'htmlIdentityMatch':capture.html_digest==html_digest(final),'htmlDigest':capture.html_digest,'preparedHtmlDigest':digest,'fitPolicy':policy,'sourceRole':source_context.get('role'),'sourceSha256':expected,'totalMs':(end-started)/1e6,'fitRenderMs':(fit_end-started)/1e6,'verifyRenderMs':(capture_end-fit_end)/1e6,'inspectionMs':(end-capture_end)/1e6,'textPreserved':all(old[q].get('owned_text')==new.get(q,{}).get('owned_text') for q in owned),'fontSizesPreserved':all(old[q].get('font_size')==new.get(q,{}).get('font_size') for q in owned),'imageSourcesPreserved':images(original)==images(final),'blockers':[x.to_dict() for x in blockers]}
            (out/'original.html').write_text(original,encoding='utf-8',newline='');(out/'prepared.html').write_text(prepared,encoding='utf-8',newline='');(out/'final.html').write_text(final,encoding='utf-8',newline='');(out/'render.png').write_bytes(capture.png);write(out/'result.json',{**receipt,'browser_metrics':capture.browser_metrics})
        except Exception as exc:
            receipt={**sample,'ordinal':ordinal,'status':'error','providerCalls':0,'errorType':type(exc).__name__,'error':str(exc),'totalMs':(time.perf_counter_ns()-started)/1e6};write(out/'result.json',receipt)
        rows.append(receipt);write(base/'summary.json',rows);print(json.dumps({'ordinal':ordinal,'status':receipt['status'],'changed':receipt.get('changed'),'fitStatus':(receipt.get('fitReport')or{}).get('status'),'blockers':len(receipt.get('blockers',[])),'totalMs':receipt['totalMs']},ensure_ascii=False),flush=True)

if __name__=='__main__':asyncio.run(main())
