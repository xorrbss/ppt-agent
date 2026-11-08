"""Original-DOM SVG text range helpers for chart-only diagnostic pages.

These helpers change original element foreground paint and native Selection
foreground over exact DOM ranges. Original shaped text is never split, and no
character extent is used as a paint mask.
"""

TEXT_HELPERS = r"""
(state => {
  if (!state || !state.svg || !globalThis.getSelection || !globalThis.CSSStyleSheet ||
      !('adoptedStyleSheets' in document) || !globalThis.Intl?.Segmenter)
    throw Error('chart_text_highlight_unsupported');
  const sheet = new CSSStyleSheet();
  document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
  const highlightNames = [], rootRecords = new Map(), ownerOffsets = new Map();
  const segmenter = new Intl.Segmenter(undefined, {granularity:'grapheme'});
  let serial = 0;
  const entries = [];
  const roots = Array.from(state.svg.querySelectorAll('text'));
  const rootsByQid = new Map();
  for (const root of roots) {
    const parts = [], walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let domOffset = 0;
    for (let node = walk.nextNode(); node; node = walk.nextNode()) {
      const parent=node.parentElement,value=node.data,css=getComputedStyle(parent);
      parts.push({node,parent,text:value,domStart:domOffset,domEnd:domOffset+value.length,
                  whiteSpace:css.whiteSpace,entries:[]});
      domOffset+=value.length;
    }
    const textRootQid=root.getAttribute('data-qid');
    if(rootsByQid.has(textRootQid))throw Error('chart_text_root_qid_not_unique');
    const record={root,parts,text:parts.map(part=>part.text).join(''),textRootQid};
    rootRecords.set(root,record);rootsByQid.set(textRootQid,record);
  }
  // Python verifies these unchanged full-root texts against the pre-font wrapper.
  // Projection membership never comes from the inserted fallback span's new qid.
  state.textRootSnapshots=Array.from(rootRecords.values(),record=>({
    textRootQid:record.textRootQid,text:record.text}));
  const addEntry=(record,part,qid,startUtf16,rootStart,rootEnd)=>{
    const {node,parent}=part,css=getComputedStyle(parent);
    const localStart=rootStart-part.domStart,localEnd=rootEnd-part.domStart;
    const value=node.data.slice(localStart,localEnd),range=document.createRange();
    range.setStart(node,localStart);range.setEnd(node,localEnd);
    let fontLoaded=false;
    try {fontLoaded=document.fonts.check(css.font,value);} catch (_) {}
    const entry={node,parent,qid,startUtf16,nodeStartUtf16:startUtf16-localStart,
      endUtf16:startUtf16+value.length,range,text:value,
      fill:css.fill,stroke:css.stroke,color:css.color,
      fillOpacity:css.fillOpacity,strokeOpacity:css.strokeOpacity,opacity:css.opacity,
      strokeWidth:css.strokeWidth,strokeLinecap:css.strokeLinecap,strokeLinejoin:css.strokeLinejoin,
      strokeMiterlimit:css.strokeMiterlimit,paintOrder:css.paintOrder,
      font:css.font,fontFamily:css.fontFamily,fontSize:css.fontSize,
      fontStyle:css.fontStyle,fontWeight:css.fontWeight,fontLoaded,
      fontRoot:record.textRootQid,actualParentQid:parent.getAttribute('data-qid'),
      root:record.root,domStart:rootStart,domEnd:rootEnd,
      sourceRootStartUtf16:rootStart,sourceRootEndUtf16:rootEnd};
    entries.push(entry);part.entries.push(entry);
    return entry;
  };
  const projection=state.cfg?.textProjection;
  if(projection!==undefined) {
    if(!Array.isArray(projection))throw Error('chart_text_projection_invalid');
    const owners=new Set(),rootIntervals=new Map(),rootDigests=new Map();
    for(const item of projection) {
      const record=rootsByQid.get(item.textRootQid);
      if(!record||owners.has(item.ownerQid)||!Array.isArray(item.segments))
        throw Error('chart_text_projection_owner_invalid');
      const matches=Array.from(state.svg.querySelectorAll('[data-qid]'))
        .filter(node=>node.getAttribute('data-qid')===item.ownerQid);
      if(matches.length!==1||matches[0].closest('text')!==record.root)
        throw Error('chart_text_projection_root_mismatch');
      if(typeof item.rootTextDigest!=='string'||typeof item.ownedTextDigest!=='string'||
          rootDigests.has(item.textRootQid)&&rootDigests.get(item.textRootQid)!==item.rootTextDigest)
        throw Error('chart_text_projection_digest_mismatch');
      owners.add(item.ownerQid);rootDigests.set(item.textRootQid,item.rootTextDigest);
      if(!rootIntervals.has(record))rootIntervals.set(record,[]);
      let ownedOffset=0,previousRootEnd=0;
      for(const segment of item.segments) {
        const start=segment.rootStartUtf16,end=segment.rootEndUtf16;
        if(!Number.isInteger(start)||!Number.isInteger(end)||!Number.isInteger(segment.ownedStartUtf16)||
            start<previousRootEnd||end<=start||end>record.text.length||segment.ownedStartUtf16!==ownedOffset)
          throw Error('chart_text_projection_segment_invalid');
        previousRootEnd=end;rootIntervals.get(record).push({start,end});
        let covered=0;
        for(const part of record.parts) {
          const from=Math.max(start,part.domStart),to=Math.min(end,part.domEnd);
          if(from>=to)continue;
          addEntry(record,part,item.ownerQid,ownedOffset+from-start,from,to);covered+=to-from;
        }
        if(covered!==end-start)throw Error('chart_text_projection_interval_missing');
        ownedOffset+=end-start;
      }
    }
    for(const record of rootRecords.values()) {
      const intervals=(rootIntervals.get(record)||[]).sort((a,b)=>a.start-b.start);
      let end=0;
      for(const interval of intervals) {
        if(interval.start<end)throw Error('chart_text_projection_interval_duplicate');
        if(record.text.slice(end,interval.start).trim())throw Error('chart_text_projection_interval_missing');
        end=interval.end;
      }
      if(record.text.slice(end).trim())throw Error('chart_text_projection_interval_missing');
    }
    const rootOrder=new Map(roots.map((root,index)=>[root,index]));
    entries.sort((a,b)=>rootOrder.get(a.root)-rootOrder.get(b.root)||a.domStart-b.domStart);
    state.textProjectionSnapshots=projection.map(item=>({ownerQid:item.ownerQid,
      textRootQid:item.textRootQid,ownedText:entries.filter(entry=>entry.qid===item.ownerQid)
        .sort((a,b)=>a.startUtf16-b.startUtf16).map(entry=>entry.text).join('')}));
  } else {
    // Synthetic callers without a pre-font projection retain actual direct ownership.
    for(const record of rootRecords.values())for(const part of record.parts) {
      if(!part.text.trim())continue;
      const qid=part.parent.getAttribute('data-qid');
      if(!qid)throw Error('chart_text_owner_missing');
      const start=ownerOffsets.get(qid)||0;
      ownerOffsets.set(qid,start+part.text.length);
      addEntry(record,part,qid,start,part.domStart,part.domEnd);
    }
    state.textProjectionSnapshots=[];
  }
  state.textEntries=entries;

  const clipped=(entry,start,end)=>{
    const range=document.createRange(),base=entry.nodeStartUtf16;
    range.setStart(entry.node,start-base);range.setEnd(entry.node,end-base);
    const rootStart=entry.domStart+start-entry.startUtf16;
    return {...entry,range,startUtf16:start,endUtf16:end,
      text:entry.node.data.slice(start-base,end-base),domStart:rootStart,domEnd:rootStart+end-start,
      sourceRootStartUtf16:rootStart,sourceRootEndUtf16:rootStart+end-start};
  };
  const membersFor=(record,start,end)=>record.parts.flatMap(part=>part.entries)
    .filter(entry=>entry.domStart<end&&entry.domEnd>start).map(entry=>clipped(entry,
      entry.startUtf16+Math.max(start,entry.domStart)-entry.domStart,
      entry.startUtf16+Math.min(end,entry.domEnd)-entry.domStart));
  const meta = entry => ({qid:entry.qid,startUtf16:entry.startUtf16,endUtf16:entry.endUtf16});

  // Native Selection paints ranges from Chromium's ORIGINAL shaped run.
  // Raw DOM UTF-16 addresses remain valid across collapsed/preserved whitespace;
  // no guessed raw-to-glyph index map or character-cell ink partition is used.
  const clustersFor=record=>Array.from(segmenter.segment(record.text),segment=>({
    start:segment.index,end:segment.index+segment.segment.length,
    sharedBoundary:false,membershipComplete:true,nativeRangePartition:true}));
  for(const record of rootRecords.values()) record.clusters=clustersFor(record);

  state.prepareRanges = (qid,startUtf16,endUtf16,options={}) => {
    const matches=Array.from(state.svg.querySelectorAll('[data-qid]'))
      .filter(node=>node.getAttribute('data-qid')===qid);
    if(matches.length!==1)throw Error('chart_text_qid_not_unique');
    const owner=matches[0],direct=(options.offsetScope||'owned-text-nodes')==='owned-text-nodes';
    const owned=entries.filter(entry=>direct?entry.qid===qid:owner.contains(entry.node));
    const text=owned.map(entry=>entry.text).join('');
    if(!Number.isInteger(startUtf16)||!Number.isInteger(endUtf16)||startUtf16<0||
       endUtf16<=startUtf16||endUtf16>text.length)throw Error('chart_text_range_mismatch');
    const ranges=[],selected=[];let offset=0;
    for(const entry of owned) {
      const begin=offset,finish=begin+entry.text.length;offset=finish;
      if(begin>=endUtf16||finish<=startUtf16)continue;
      const from=entry.startUtf16+Math.max(0,startUtf16-begin);
      const to=entry.startUtf16+Math.min(entry.text.length,endUtf16-begin);
      const range=clipped(entry,from,to);ranges.push(range);
      selected.push({entry,range,selectionStart:begin});
    }
    const clusters=[];
    for(const record of rootRecords.values()) for(const cluster of record.clusters) {
      const whole=membersFor(record,cluster.start,cluster.end);
      const intersection=[];
      for(const member of whole) for(const selectedRange of ranges) {
        if(member.node!==selectedRange.node||member.qid!==selectedRange.qid)continue;
        const from=Math.max(member.startUtf16,selectedRange.startUtf16);
        const to=Math.min(member.endUtf16,selectedRange.endUtf16);
        if(from<to)intersection.push(clipped(member,from,to));
      }
      if(!intersection.length)continue;
      const selectionOffsets=intersection.map(member=> {
        const match=selected.find(item=>item.entry.node===member.node&&item.entry.qid===member.qid&&
          item.entry.startUtf16<=member.startUtf16&&item.entry.endUtf16>=member.endUtf16);
        return [match.selectionStart+member.startUtf16-match.entry.startUtf16,
                match.selectionStart+member.endUtf16-match.entry.startUtf16];
      });
      const authorizationComplete=whole.every(member=>ranges.some(range=>range.node===member.node&&range.qid===member.qid &&
        range.startUtf16<=member.startUtf16 && range.endUtf16>=member.endUtf16));
      clusters.push({startUtf16:Math.min(...selectionOffsets.map(item=>item[0])),
        endUtf16:Math.max(...selectionOffsets.map(item=>item[1])),
        text:record.text.slice(cluster.start,cluster.end),members:whole.map(meta),
        ownedIntersection:intersection.map(meta),sharedBoundary:cluster.sharedBoundary,nativeRangePartition:true,
        membershipComplete:cluster.membershipComplete,authorizationComplete,ranges:whole});
    }
    return {ranges,clusters,ownedText:text,requestedRange:{qid,startUtf16,endUtf16},
      sharedBoundary:clusters.some(cluster=>cluster.sharedBoundary||!cluster.authorizationComplete),
      complete:clusters.every(cluster=>cluster.membershipComplete),
      fontRoot:owned[0]?.fontRoot||null};
  };

  const plans=[];
  const owners=[...new Set(entries.map(entry=>entry.parent))];
  const savedPaints=owners.map(parent=>{const css=getComputedStyle(parent);return [parent,css.fill,css.stroke];});
  const same=(a,b)=>a.fill===b.fill&&a.stroke===b.stroke;
  const render=()=>{
    getSelection().removeAllRanges();sheet.replaceSync('');
    const chosen=[],partial=[];
    for(const entry of entries) {
      const applicable=plans.filter(plan=>plan.entry.node===entry.node&&plan.entry.qid===entry.qid&&
        plan.entry.startUtf16<entry.endUtf16&&plan.entry.endUtf16>entry.startUtf16);
      const cuts=[entry.startUtf16,entry.endUtf16,...applicable.flatMap(plan=>[
        Math.max(entry.startUtf16,plan.entry.startUtf16),Math.min(entry.endUtf16,plan.entry.endUtf16)])];
      const points=[...new Set(cuts)].sort((a,b)=>a-b);
      const segments=[];
      for(let index=1;index<points.length;index++) {
        const from=points[index-1],to=points[index];if(from===to)continue;
        let paint={fill:entry.fill,stroke:entry.stroke},priority=-Infinity,order=-1,force=false;
        for(const plan of applicable)if(plan.entry.startUtf16<=from&&plan.entry.endUtf16>=to&&
            (plan.priority>priority||plan.priority===priority&&plan.order>order)) {
          paint=plan.paint;priority=plan.priority;order=plan.order;force=plan.force;
        }
        segments.push({entry:clipped(entry,from,to),paint,force});
      }
      // The lowest-priority complete-entry paint is the base. Higher-priority
      // substring operations use native selection without splitting the run.
      const completePlans=applicable.filter(plan=>plan.entry.startUtf16===entry.startUtf16&&plan.entry.endUtf16===entry.endUtf16)
        .sort((a,b)=>b.priority-a.priority||b.order-a.order);
      const base=completePlans[0]?.paint||{fill:entry.fill,stroke:entry.stroke};
      chosen.push({entry,base,basePriority:completePlans[0]?.priority??-Infinity,segments});
    }
    // Existing text/tspan elements own direct text and tails. Pin every
    // descendant computed foreground so ancestor overrides cannot leak.
    for(const [parent,fill,stroke] of savedPaints){parent.style.setProperty('fill',fill,'important');parent.style.setProperty('stroke',stroke,'important');}
    const parentBases=new Map();
    for(const item of chosen) {
      const previous=parentBases.get(item.entry.parent);
      if(!previous||item.basePriority<previous.priority)
        parentBases.set(item.entry.parent,{paint:item.base,priority:item.basePriority});
    }
    for(const [parent,{paint}] of parentBases){parent.style.setProperty('fill',paint.fill,'important');parent.style.setProperty('stroke',paint.stroke,'important');}
    for(const item of chosen)for(const segment of item.segments)
      if(!same(segment.paint,parentBases.get(item.entry.parent).paint)||segment.force)partial.push(segment);
    if(!partial.length)return;
    const ordered=partial.slice().sort((a,b)=>{
      if(a.entry.node===b.entry.node)return (a.entry.startUtf16-a.entry.nodeStartUtf16)-(b.entry.startUtf16-b.entry.nodeStartUtf16);
      return a.entry.node.compareDocumentPosition(b.entry.node)&Node.DOCUMENT_POSITION_FOLLOWING?-1:1;});
    const first=ordered[0].entry,last=ordered[ordered.length-1].entry,selectionRange=document.createRange();
    selectionRange.setStart(first.node,first.startUtf16-first.nodeStartUtf16);
    selectionRange.setEnd(last.node,last.endUtf16-last.nodeStartUtf16);
    const selectionPaints=new Map();
    for(const item of chosen)for(const segment of item.segments) {
      if(!selectionRange.intersectsNode(segment.entry.node))continue;
      const left=document.createRange();left.setStart(segment.entry.node,segment.entry.startUtf16-segment.entry.nodeStartUtf16);left.setEnd(segment.entry.node,segment.entry.endUtf16-segment.entry.nodeStartUtf16);
      const compare=(node,offset,other,otherOffset)=>{const a=document.createRange(),b=document.createRange();
        a.setStart(node,offset);a.collapse(true);b.setStart(other,otherOffset);b.collapse(true);
        return a.compareBoundaryPoints(Range.START_TO_START,b);};
      if(compare(left.endContainer,left.endOffset,selectionRange.startContainer,selectionRange.startOffset)<=0||
         compare(left.startContainer,left.startOffset,selectionRange.endContainer,selectionRange.endOffset)>=0)continue;
      const parent=segment.entry.parent;
      if(selectionPaints.has(parent)&&!same(selectionPaints.get(parent),segment.paint))
        throw Error('chart_text_simultaneous_disjoint_ranges_unproven');
      selectionPaints.set(parent,segment.paint);
    }
    for(const [parent,paint] of selectionPaints) {
      const qid=parent.getAttribute('data-qid');
      const index=sheet.insertRule('[data-qid="'+qid+'"]::selection {}',sheet.cssRules.length),rule=sheet.cssRules[index];
      rule.style.setProperty('background-color','transparent','important');
      const original=entries.find(entry=>entry.parent===parent);
      // Blink filters selection fill/stroke-opacity to one. Encode their exact
      // source-over factors in foreground alpha instead; this is diagnostic
      // paint only and must still reproduce the original all-on raster exactly.
      const withOpacity=(color,opacity)=>color==='none'||Number(opacity)===1?color:
        'color-mix(in srgb, '+color+' '+(Number(opacity)*100)+'%, transparent)';
      const selectionFill=withOpacity(paint.fill,original.fillOpacity);
      const selectionStroke=withOpacity(paint.stroke,original.strokeOpacity);
      rule.style.setProperty('fill',selectionFill,'important');rule.style.setProperty('stroke',selectionStroke,'important');
      rule.style.setProperty('color',paint.fill==='none'?'transparent':selectionFill,'important');
      for(const [property,value] of [['fill-opacity',original.fillOpacity],['stroke-opacity',original.strokeOpacity],
          ['stroke-width',original.strokeWidth],['stroke-linecap',original.strokeLinecap],
          ['stroke-linejoin',original.strokeLinejoin],['stroke-miterlimit',original.strokeMiterlimit],
          ['paint-order',original.paintOrder]])rule.style.setProperty(property,value,'important');
    }
    getSelection().addRange(selectionRange);
    if(getSelection().rangeCount!==1)throw Error('chart_native_selection_unavailable');
    state.selectionForegroundEvidence=[...selectionPaints].map(([parent,paint])=>{
      const original=entries.find(entry=>entry.parent===parent),actual=getComputedStyle(parent,'::selection');
      return {qid:original.qid,fill:paint.fill,stroke:paint.stroke,
        expected:[original.fillOpacity,original.strokeOpacity,original.strokeWidth],
        actual:[actual.fillOpacity,actual.strokeOpacity,actual.strokeWidth]};});
  };
  state.clearTextPaint=()=>{plans.length=0;getSelection().removeAllRanges();sheet.replaceSync('');};
  state.flushTextPaint=render;
  state.paintRange=(ranges,options={})=>{
    const priority=options.priority??0;
    for(const entry of ranges) {
      let fill=entry.fill,stroke=entry.stroke;
      if(options.hidden){if(fill!=='none')fill='transparent';if(stroke!=='none')stroke='transparent';}
      else if(!options.original){if(options.fill!==undefined&&fill!=='none')fill=options.fill;if(options.stroke!==undefined&&stroke!=='none')stroke=options.stroke;}
      plans.push({entry,priority,paint:{fill,stroke},order:plans.length,force:options.forceSelection===true});
    }
    return true;
  };
})(window.__chartPaint);
"""
