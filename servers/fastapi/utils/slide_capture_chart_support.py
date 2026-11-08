"""Measured chart boundary support, evaluated without recursive paint borrowing.

All coordinates use the server manifest. Masks are diagnostic raster evidence;
geometry only selects the declared physical-pixel boundary neighborhood.
"""
from __future__ import annotations

import base64
import math
import sys
import zlib
from collections import Counter
from itertools import chain
from collections.abc import Mapping

from PIL import Image, ImageChops

from utils.hashing import canonical_json_bytes, sha256_prefixed


def mask_record(mask, alpha):
    """The single canonical codec for every chart diagnostic mask receipt."""
    return {"maskDigest": sha256_prefixed(mask.tobytes()), "pixelCount": mask.histogram()[255],
            "alphaSum": sum(a for a, covered in zip(alpha.tobytes(), mask.tobytes()) if covered)}


def encode_native_raster(image):
    """Encode actual one-byte native pixels; dimensions come from the capture."""
    if not isinstance(image, Image.Image) or image.mode != "L" or min(image.size) <= 0:
        raise ValueError("native_composition_raster_invalid")
    raw = image.tobytes()
    return {"encoding": "zlib-base64-u8/v1", "width": image.width, "height": image.height,
            "sha256": sha256_prefixed(raw), "dataBase64": base64.b64encode(zlib.compress(raw, 9)).decode("ascii")}


def encoded_mask_record(mask, alpha):
    """Keep legacy statistics and bind the exact binary pixels they describe."""
    if (not isinstance(mask, Image.Image) or mask.mode != "L" or mask.size != alpha.size
            or any(value not in (0, 255) for value in mask.tobytes())):
        raise ValueError("native_composition_mask_invalid")
    return {**mask_record(mask, alpha), "encodedRaster": encode_native_raster(mask)}


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("chart_support_number_invalid")
    return float(value)


def _matrix(value):
    if not isinstance(value, (list, tuple)) or len(value) != 6:
        raise ValueError("chart_support_canvas_matrix_missing")
    return tuple(_finite(item) for item in value)


def _roots(coefficients):
    """Real roots of a degree-at-most-four polynomial, with no epsilon cutoff."""
    coefficients=list(coefficients)
    while coefficients and coefficients[-1] == 0:
        coefficients.pop()
    if len(coefficients) <= 1:
        return []
    scale=max(abs(value) for value in coefficients)
    coefficients=[value/scale for value in coefficients]
    if len(coefficients) == 2:
        return [-coefficients[0]/coefficients[1]]
    critical=_roots([index*value for index,value in enumerate(coefficients)][1:])
    try:
        bound=1+max(abs(value/coefficients[-1]) for value in coefficients[:-1])
    except OverflowError:
        bound=sys.float_info.max
    if not math.isfinite(bound):
        bound=sys.float_info.max
    points=sorted(set([-bound,*[value for value in critical if -bound<value<bound],bound]))
    def evaluate(value):
        result=0.0
        for coefficient in reversed(coefficients):
            result=result*value+coefficient
        return result
    result=[]
    for point in points:
        if evaluate(point) == 0:
            result.append(point)
    for left,right in zip(points,points[1:]):
        low,high=evaluate(left),evaluate(right)
        if not (low<0<high or high<0<low):
            continue
        while True:
            middle=left/2+right/2
            if middle == left or middle == right:
                result.append(left if abs(low)<=abs(high) else right)
                break
            value=evaluate(middle)
            if value == 0:
                result.append(middle)
                break
            if (low<0<value or value<0<low):
                right,high=middle,value
            else:
                left,low=middle,value
    return sorted(set(result))


def _arc_contains(angle,start,sweep):
    if abs(sweep) >= math.tau:
        return True
    return ((angle-start)%math.tau if sweep>=0 else (start-angle)%math.tau) <= abs(sweep)


def _ellipse_near(px,py,u,v,start,sweep,radius):
    """Distance to the actual affine ellipse/arc, not a polygon approximation."""
    radius2=radius*radius
    def close(angle):
        cosine,sine=math.cos(angle),math.sin(angle)
        return (u[0]*cosine+v[0]*sine-px)**2+(u[1]*cosine+v[1]*sine-py)**2 <= radius2
    if close(start) or close(start+sweep):
        return True
    uu=u[0]*u[0]+u[1]*u[1]
    vv=v[0]*v[0]+v[1]*v[1]
    uv=u[0]*v[0]+u[1]*v[1]
    determinant=u[0]*v[1]-u[1]*v[0]
    if determinant:
        x=(v[1]*px-v[0]*py)/determinant
        y=(-u[1]*px+u[0]*py)/determinant
        radial=math.hypot(x,y)
        largest=math.sqrt((uu+vv+math.hypot(uu-vv,2*uv))/2)
        smallest=abs(determinant)/largest if largest else 0
        if abs(radial-1)*smallest > radius:
            return False
        angle=math.atan2(y,x)
        if _arc_contains(angle,start,sweep) and close(angle):
            return True
    pu=px*u[0]+py*u[1]
    pv=px*v[0]+py*v[1]
    # d(distance squared)/dt, with z=tan(t/2), has this quartic numerator.
    polynomial=[uv-pv,2*(vv-uu+pu),-6*uv,2*(uu-vv+pu),uv+pv]
    angles=[2*math.atan(root) for root in _roots(polynomial)]
    angles.append(math.pi)  # z=infinity, including a stationary point at pi.
    return any(_arc_contains(angle,start,sweep) and close(angle) for angle in angles)


def boundary_mask(obligation,size,canvas_matrix,dpr):
    """Select pixel centers within the manifest's physical boundary radius."""
    if (not isinstance(size,(tuple,list)) or len(size)!=2 or
            any(type(value) is not int or value<=0 for value in size)):
        raise ValueError("chart_support_raster_size_invalid")
    a,b,c,d,e,f=_matrix(canvas_matrix)
    dpr=_finite(dpr)
    if dpr<=0:
        raise ValueError("chart_support_device_scale_invalid")
    radius=_finite(obligation.get("radiusPhysicalPixels"))
    if radius<0 or obligation.get("regionRule") != "native-alpha-boundary-neighborhood/v1":
        raise ValueError("chart_support_boundary_rule_invalid")
    width,height=size
    values=bytearray(width*height)
    def transform(point):
        if not isinstance(point,(tuple,list)) or len(point)!=2:
            raise ValueError("chart_support_boundary_point_invalid")
        x,y=map(_finite,point)
        return ((a*x+c*y+e)*dpr,(b*x+d*y+f)*dpr)
    def bounds(x0,y0,x1,y1):
        return (max(0,math.ceil(x0-radius-.5)),max(0,math.ceil(y0-radius-.5)),
                min(width-1,math.floor(x1+radius-.5)),min(height-1,math.floor(y1+radius-.5)))
    for segment in obligation.get("segments",[]):
        if not isinstance(segment,(list,tuple)) or len(segment)!=2:
            raise ValueError("chart_support_boundary_segment_invalid")
        (x0,y0),(x1,y1)=map(transform,segment)
        left,top,right,bottom=bounds(min(x0,x1),min(y0,y1),max(x0,x1),max(y0,y1))
        dx,dy=x1-x0,y1-y0
        length2=dx*dx+dy*dy
        for y in range(top,bottom+1):
            for x in range(left,right+1):
                px,py=x+.5,y+.5
                amount=max(0,min(1,((px-x0)*dx+(py-y0)*dy)/length2)) if length2 else 0
                if (px-x0-amount*dx)**2+(py-y0-amount*dy)**2 <= radius*radius:
                    values[y*width+x]=255
    ellipse=obligation.get("ellipse")
    if ellipse is not None:
        center=transform(ellipse["center"])
        rx,ry=map(_finite,ellipse["radii"])
        if rx<=0 or ry<=0:
            raise ValueError("chart_support_ellipse_radii_invalid")
        start,sweep=_finite(ellipse["startAngle"]),_finite(ellipse["sweepAngle"])
        u,v=(a*rx*dpr,b*rx*dpr),(c*ry*dpr,d*ry*dpr)
        extent_x,extent_y=math.hypot(u[0],v[0]),math.hypot(u[1],v[1])
        left,top,right,bottom=bounds(center[0]-extent_x,center[1]-extent_y,
                                     center[0]+extent_x,center[1]+extent_y)
        for y in range(top,bottom+1):
            for x in range(left,right+1):
                if values[y*width+x]:
                    continue
                if _ellipse_near(x+.5-center[0],y+.5-center[1],u,v,start,sweep,radius):
                    values[y*width+x]=255
    return Image.frombytes("L",tuple(size),bytes(values))


def _binary(mask,size=None):
    if not isinstance(mask,Image.Image) or mask.mode!="L" or size is not None and mask.size!=size:
        raise ValueError("chart_support_measured_mask_missing")
    return mask.point(lambda value:255 if value else 0)


def _intersection(*masks):
    result=masks[0]
    for mask in masks[1:]:
        result=ImageChops.multiply(result,mask)
    return result


def _count(mask):
    return mask.histogram()[255]


def _union(masks,size):
    result=Image.new("L",size,0)
    for mask in masks:
        result=ImageChops.lighter(result,mask)
    return result


def _strip(mask,support,matrix,dpr):
    strip=support.get("categoryStrip")
    if strip is None:
        return mask
    a,b,c,d,e,f=_matrix(matrix)
    determinant=a*d-b*c
    if not determinant:
        raise ValueError("chart_support_canvas_transform_singular")
    minimum,maximum=strip.get("minimumX"),strip.get("maximumX")
    data=bytearray(mask.tobytes())
    for y in range(mask.height):
        for x in range(mask.width):
            index=y*mask.width+x
            if not data[index]:
                continue
            canvas_x=(d*((x+.5)/dpr-e)-c*((y+.5)/dpr-f))/determinant
            if minimum is not None and canvas_x<minimum or maximum is not None and canvas_x>maximum:
                data[index]=0
    return Image.frombytes("L",mask.size,bytes(data))


def _geometry_bounds(bounds):
    if not isinstance(bounds,Mapping):
        raise ValueError("stack_native_geometry_bounds_missing")
    left,top,right,bottom=(_finite(bounds.get(key)) for key in ("left","top","right","bottom"))
    if right<left or bottom<top:
        raise ValueError("stack_native_geometry_bounds_invalid")
    return left,top,right,bottom


def _stack_normal_profiles(obligation,canvas_matrix,dpr,target_bounds,neighbor_bounds,footprint):
    """Every declared normal candidate, using geometric rectangle interiors."""
    a,b,c,d,e,f=_matrix(canvas_matrix)
    dpr=_finite(dpr)
    radius=_finite(obligation.get("radiusPhysicalPixels"))
    if dpr<=0 or radius<0 or obligation.get("regionRule")!="native-alpha-boundary-neighborhood/v1":
        raise ValueError("stack_boundary_sampling_rule_invalid")
    first,second=_geometry_bounds(target_bounds),_geometry_bounds(neighbor_bounds)
    def transform(point):
        if not isinstance(point,(list,tuple)) or len(point)!=2:
            raise ValueError("stack_boundary_segment_invalid")
        x,y=map(_finite,point)
        return ((a*x+c*y+e)*dpr,(b*x+d*y+f)*dpr)
    def nearest(low,high,center,length):
        # Strict geometric interior and captured pixel-center domain.
        left=max(0,math.floor(low-.5)+1)
        right=min(length-1,math.ceil(high-.5)-1)
        if left>right:
            return None
        choices={max(left,min(right,math.floor(center-.5))),max(left,min(right,math.ceil(center-.5)))}
        return min(choices,key=lambda index:(abs(index+.5-center),index)),left,right
    for segment in obligation.get("segments",[]):
        if not isinstance(segment,(list,tuple)) or len(segment)!=2:
            raise ValueError("stack_boundary_segment_invalid")
        (x0,y0),(x1,y1)=map(transform,segment)
        if y0==y1 and x0!=x1:
            horizontal=True;coordinate=y0;start,end=sorted((x0,x1))
            tangent_axis,normal_axis=0,1
            tangent_length,normal_length=footprint.width,footprint.height
        elif x0==x1 and y0!=y1:
            horizontal=False;coordinate=x0;start,end=sorted((y0,y1))
            tangent_axis,normal_axis=1,0
            tangent_length,normal_length=footprint.height,footprint.width
        else:
            raise ValueError("stack_boundary_not_axis_aligned")
        first_center=(first[normal_axis]+first[normal_axis+2])/2
        second_center=(second[normal_axis]+second[normal_axis+2])/2
        if first_center<coordinate<second_center:
            negative,positive=first,second
        elif second_center<coordinate<first_center:
            negative,positive=second,first
        else:
            continue
        negative_center=(negative[normal_axis]+negative[normal_axis+2])/2
        positive_center=(positive[normal_axis]+positive[normal_axis+2])/2
        neg=nearest(negative[normal_axis],min(negative[normal_axis+2],coordinate),negative_center,normal_length)
        pos=nearest(max(positive[normal_axis],coordinate),positive[normal_axis+2],positive_center,normal_length)
        if neg is None or pos is None or not neg[0]+.5<coordinate<pos[0]+.5:
            continue
        low=max(start+radius,negative[tangent_axis],positive[tangent_axis])
        high=min(end-radius,negative[tangent_axis+2],positive[tangent_axis+2])
        first_tangent=max(0,math.floor(low-.5)+1)
        last_tangent=min(tangent_length-1,math.ceil(high-.5)-1)
        first_normal=max(0,math.ceil(coordinate-radius-.5))
        last_normal=min(normal_length-1,math.floor(coordinate+radius-.5))
        for tangent in range(first_tangent,last_tangent+1):
            for normal in range(first_normal,last_normal+1):
                boundary=(tangent,normal) if horizontal else (normal,tangent)
                if not footprint.getpixel(boundary):
                    continue
                # Each profile is (preferred midpoint depth, first depth, last
                # depth). Every depth is strict in-rectangle and on its side.
                yield boundary,neg,pos,horizontal


def stack_boundary_contrast(obligation,canvas_matrix,dpr,target_bounds,neighbor_bounds,
                            original_rgb,pair_absent_rgb,pair_native_rgba,target_native_boundary):
    """Measure native pair paint and actual signed contrast across each normal."""
    footprint=_binary(target_native_boundary)
    if (not isinstance(original_rgb,Image.Image) or original_rgb.mode!="RGB" or original_rgb.size!=footprint.size or
        not isinstance(pair_absent_rgb,Image.Image) or pair_absent_rgb.mode!="RGB" or pair_absent_rgb.size!=footprint.size or
        not isinstance(pair_native_rgba,Image.Image) or pair_native_rgba.mode!="RGBA" or pair_native_rgba.size!=footprint.size):
        raise ValueError("stack_boundary_raster_identity_mismatch")
    candidates=Image.new("L",footprint.size,0)
    native_contrast=Image.new("L",footprint.size,0)
    contributed=Image.new("L",footprint.size,0)
    samples={}
    signed=lambda first,second:tuple(a-b for a,b in zip(first,second))
    nonzero=lambda value:any(component!=0 for component in value)
    def observe(pixel):
        original=original_rgb.getpixel(pixel)
        absent=pair_absent_rgb.getpixel(pixel)
        native=pair_native_rgba.getpixel(pixel)
        return {"original":original,"absent":absent,"native":native,
                "effect":signed(original,absent),"pixel":pixel}
    for boundary,negative_profile,positive_profile,horizontal in _stack_normal_profiles(
            obligation,canvas_matrix,dpr,target_bounds,neighbor_bounds,footprint):
        candidates.putpixel(boundary,255)
        if boundary in samples:
            continue
        def pixel(depth):
            return (boundary[0],depth) if horizontal else (depth,boundary[1])
        negative=observe(pixel(negative_profile[0]))
        positive=observe(pixel(positive_profile[0]))
        native_boundary=pair_native_rgba.getpixel(boundary)
        actual_boundary=original_rgb.getpixel(boundary)
        absent_boundary=pair_absent_rgb.getpixel(boundary)
        boundary_changed=actual_boundary!=absent_boundary
        def native_pair(negative,positive):
            return (positive["native"]!=negative["native"] or native_boundary[3]>0 and
                    native_boundary!=negative["native"] and native_boundary!=positive["native"])
        def divider_side(state):
            difference=signed(actual_boundary,state["original"])
            return (native_boundary!=state["native"] and nonzero(difference) and
                    difference!=signed(absent_boundary,state["absent"]))
        def kind_for(negative,positive):
            if not boundary_changed:
                return None
            difference=signed(positive["original"],negative["original"])
            absent_difference=signed(positive["absent"],negative["absent"])
            if positive["native"]!=negative["native"] and nonzero(difference) and difference!=absent_difference:
                return "adjacent-fill"
            if native_boundary[3]>0 and divider_side(negative) and divider_side(positive):
                return "native-divider"
            return None
        kind=kind_for(negative,positive)
        if native_pair(negative,positive):
            native_contrast.putpixel(boundary,255)
        if kind is None:
            def states(profile):
                preferred,first,last=profile
                seen=set()
                for depth in chain((preferred,),range(first,preferred),range(preferred+1,last+1)):
                    state=observe(pixel(depth))
                    identity=(state["original"],state["absent"],state["native"])
                    if identity in seen:
                        continue
                    seen.add(identity)
                    yield state
            negatives=list(states(negative_profile))
            positives=list(states(positive_profile))
            # The native prerequisite is existential over the complete profiles.
            negative_native={state["native"] for state in negatives}
            positive_native={state["native"] for state in positives}
            native_fill=any(value!=positives[0]["native"] for value in negative_native) or len(positive_native)>1
            native_divider=(native_boundary[3]>0 and any(value!=native_boundary for value in negative_native)
                            and any(value!=native_boundary for value in positive_native))
            if native_fill or native_divider:
                native_contrast.putpixel(boundary,255)
            if boundary_changed:
                # For adjacent fill, reject the union of three equality sets:
                # original RGB, native RGBA, or signed (original - absent).
                # Inclusion/exclusion answers existence in linear profile work.
                dimensions=(("original",),("native",),("effect",),
                            ("original","native"),("original","effect"),("native","effect"),
                            ("original","native","effect"))
                counts=[Counter(tuple(state[key] for key in keys) for state in negatives) for keys in dimensions]
                for candidate in positives:
                    equal=[count[tuple(candidate[key] for key in keys)] for count,keys in zip(counts,dimensions)]
                    eligible=len(negatives)-sum(equal[:3])+sum(equal[3:6])-equal[6]
                    if eligible:
                        negative=next(state for state in negatives if state["original"]!=candidate["original"] and
                                      state["native"]!=candidate["native"] and state["effect"]!=candidate["effect"])
                        positive=candidate;kind="adjacent-fill"
                        break
                if kind is None and native_boundary[3]>0:
                    # Divider-to-each-interior attribution is separable; there
                    # is no need to enumerate a Cartesian product of depths.
                    negative=next((state for state in negatives if divider_side(state)),None)
                    positive=next((state for state in positives if divider_side(state)),None)
                    if negative is not None and positive is not None:
                        kind="native-divider"
        if kind is not None:
            contributed.putpixel(boundary,255)
            samples[boundary]={"kind":kind,"boundaryPixel":list(boundary),
                "negativeInteriorPixel":list(negative["pixel"]),"positiveInteriorPixel":list(positive["pixel"])}
    return {"footprint":footprint,"normalInteriorPairs":candidates,"nativeContrast":native_contrast,
            "originalContribution":contributed,"samplePairs":list(samples.values())}


def _native_pair_request(kind, target, partner, binding):
    fields = {"kind": kind, "targetId": target["id"], "partnerTargetId": partner["id"],
              "qids": [target["qid"], partner["qid"]]}
    identity = {**fields, "bindingDigest": sha256_prefixed(canonical_json_bytes(binding))}
    return {"id": "native-pair/" + sha256_prefixed(canonical_json_bytes(identity))[7:], **fields}


def native_composition_requests(chart, records, masks):
    """Request only finite source-proved pairs, retaining original DOM paint order."""
    by_id = {row["id"]: row for row in records}
    datums = {row["id"]: row for row in chart["datums"]}
    requests = {}
    for datum in chart["datums"]:
        support = datum["geometrySupport"]
        for qid in datum.get("primitiveQids", []):
            target = by_id.get(f"{chart['id']}/primitive/{qid}/{datum['id']}")
            if target is None or target.get("qid") != qid or target.get("kind") != "primitive":
                continue
            raw = masks.get(target["id"])
            if raw is None:
                continue
            candidates = []
            if chart["chartType"] in {"line", "area"}:
                for obligation in support.get("visibilityObligations", []):
                    if obligation.get("kind") != "value-anchor" or obligation.get("primitiveQid") != qid:
                        continue
                    for alternative in obligation.get("alternatives", []):
                        if (alternative.get("kind") != "marker-on-datum"
                                or alternative.get("geometrySupportDigest") != support["geometryDigest"]
                                or alternative.get("requiresEntireBoundaryCoverage") is not True
                                or alternative.get("requiresOriginalContribution") is not True):
                            continue
                        partner = by_id.get(alternative.get("markerTargetId"))
                        declared = [relation for relation in chart["compositionGroup"].get("relationships", [])
                                    if relation.get("kind") == "marker-on-datum" and relation.get("datumId") == datum["id"]
                                    and relation.get("qid") == alternative.get("markerQid")]
                        if (len(declared) == 1 and partner is not None and partner.get("qid") == alternative.get("markerQid")
                                and partner.get("kind") == "primitive"):
                            candidates.append(("marker-on-datum", partner, obligation, None))
            if chart["chartType"] in {"pie", "donut"}:
                for relation in target.get("ownedRelationships", []):
                    neighbor = datums.get(relation.get("neighborDatumId"))
                    topology = relation.get("polarTopology", {})
                    if (relation.get("kind") != "proved-polar-adjacency" or neighbor is None
                            or relation.get("datumId") != datum["id"] or relation.get("targetQid") != qid
                            or relation.get("geometrySupportDigest") != support["geometryDigest"]
                            or relation.get("neighborGeometrySupportDigest") != neighbor["geometrySupport"]["geometryDigest"]
                            or relation.get("axisKey") != support.get("axisKey")
                            or relation.get("unitKey") != support.get("unitKey")
                            or relation.get("occluderQid") not in neighbor["primitiveQids"]
                            or topology.get("fillInteriorsDisjoint") is not True
                            or topology.get("contourTopologyChecked") is not True
                            or not topology.get("sharedRadialSegments")):
                        continue
                    partner = by_id.get(f"{chart['id']}/primitive/{relation['occluderQid']}/{neighbor['id']}")
                    if partner is not None:
                        candidates.append(("proved-polar-adjacency", partner, None, topology))
            for kind, partner, obligation, topology in candidates:
                try:
                    other = masks[partner["id"]]
                    size = raw["footprint"].size
                    if (int(partner["qid"][4:]) <= int(qid[4:]) or other["footprint"].size != size
                            or _matrix(raw["canvasMatrix"]) != _matrix(other["canvasMatrix"])
                            or raw["deviceScaleFactor"] != other["deviceScaleFactor"]):
                        continue
                    if kind == "marker-on-datum":
                        demanded = _intersection(boundary_mask(obligation, size, raw["canvasMatrix"], raw["deviceScaleFactor"]),
                                                  raw["footprint"])
                        if not demanded.getbbox() or not ImageChops.subtract(demanded, _binary(raw["originalContribution"])).getbbox():
                            continue
                    else:
                        selected = boundary_mask({"segments": topology["sharedRadialSegments"],
                            "radiusPhysicalPixels": 1, "regionRule": "native-alpha-boundary-neighborhood/v1"},
                            size, raw["canvasMatrix"], raw["deviceScaleFactor"])
                        patch = _intersection(raw["altered"], raw["alpha"].point(lambda value: 255 if value else 0),
                                              other["alpha"].point(lambda value: 255 if value else 0), selected)
                        if not patch.getbbox():
                            continue
                    binding, _, _, _, _ = _composition_binding(chart, target, partner, kind)
                    request = _native_pair_request(kind, target, partner, binding)
                    requests[request["id"]] = request
                except (KeyError, TypeError, ValueError):
                    continue
    return sorted(requests.values(), key=lambda row: (int(row["qids"][0][4:]), int(row["qids"][1][4:]), row["id"]))


def _composition_binding(chart, target, partner, kind):
    datums = {row["id"]: row for row in chart["datums"]}
    if len(target.get("datumIds", [])) != 1:
        raise ValueError("native_composition_datum_identity_invalid")
    datum = datums[target["datumIds"][0]]
    support = datum["geometrySupport"]
    if target["id"] != f"{chart['id']}/primitive/{target['qid']}/{datum['id']}":
        raise ValueError("native_composition_target_identity_invalid")
    if kind == "marker-on-datum":
        relations = [row for row in chart["compositionGroup"].get("relationships", [])
                     if row.get("kind") == kind and row.get("datumId") == datum["id"] and row.get("qid") == partner["qid"]]
        alternatives = [(obligation, alternative) for obligation in support.get("visibilityObligations", [])
                        if obligation.get("kind") == "value-anchor" and obligation.get("primitiveQid") == target["qid"]
                        for alternative in obligation.get("alternatives", [])
                        if alternative.get("kind") == kind and alternative.get("markerTargetId") == partner["id"]
                        and alternative.get("markerQid") == partner["qid"]
                        and alternative.get("geometrySupportDigest") == support["geometryDigest"]
                        and alternative.get("requiresEntireBoundaryCoverage") is True
                        and alternative.get("requiresOriginalContribution") is True]
        if len(relations) != 1 or len(alternatives) != 1:
            raise ValueError("native_composition_marker_binding_invalid")
        obligation, alternative = alternatives[0]
        binding = {"relation": relations[0], "obligation": obligation, "alternative": alternative, "geometrySupport": support}
        return binding, relations[0], obligation, datum, datum
    relations = [row for row in target.get("ownedRelationships", [])
                 if row.get("kind") == kind and row.get("targetQid") == target["qid"]
                 and row.get("occluderQid") == partner["qid"] and row.get("datumId") == datum["id"]]
    if len(relations) != 1:
        raise ValueError("native_composition_polar_binding_invalid")
    relation = relations[0]
    declared = [row for group in chart["compositionGroup"].get("relationships", []) if group.get("kind") == kind
                for row in group.get("localOverlaps", []) if isinstance(row, Mapping)
                and canonical_json_bytes({"kind": kind, **row}) == canonical_json_bytes(relation)]
    if len(declared) != 1:
        raise ValueError("native_composition_polar_membership_invalid")
    neighbor = datums[relation["neighborDatumId"]]
    if partner["id"] != f"{chart['id']}/primitive/{partner['qid']}/{neighbor['id']}":
        raise ValueError("native_composition_partner_identity_invalid")
    binding = {"relation": relation, "geometrySupport": support, "partnerGeometrySupport": neighbor["geometrySupport"]}
    return binding, relation, None, datum, neighbor


def _same_native_mask(first, second):
    return first.size == second.size and first.mode == second.mode == "L" and first.tobytes() == second.tobytes()


def _native_bundle(raw, record):
    alpha = raw["alpha"]
    if not isinstance(alpha, Image.Image) or alpha.mode != "L":
        raise ValueError("native_composition_alpha_invalid")
    size = alpha.size
    fields = ("footprint", "originalContribution", "retained", "ownedOverlap", "altered", "unproven")
    selected = {key: _binary(raw[key], size) for key in fields}
    selected["quantizedAtCapture"] = _binary(raw.get("quantizedAtCapture", ImageChops.subtract(
        selected["footprint"], _union([selected[key] for key in ("retained", "ownedOverlap", "altered", "unproven")], size))), size)
    parts = [selected[key] for key in ("retained", "ownedOverlap", "altered", "quantizedAtCapture", "unproven")]
    assigned = Image.new("L", size)
    for part in parts:
        if _intersection(assigned, part).getbbox():
            raise ValueError("native_composition_partition_overlap")
        assigned = ImageChops.lighter(assigned, part)
    if not _same_native_mask(assigned, selected["footprint"]):
        raise ValueError("native_composition_partition_incomplete")
    if ImageChops.subtract(selected["originalContribution"], selected["footprint"]).getbbox():
        raise ValueError("native_composition_contribution_outside_footprint")
    for key, mask in selected.items():
        if record.get(key) != mask_record(mask, alpha):
            raise ValueError("native_composition_record_mask_mismatch")
    return {"nativeAlpha": encode_native_raster(alpha),
            **{key: encoded_mask_record(selected[key], alpha) for key in
               ("footprint", "originalContribution", "retained", "ownedOverlap", "altered", "quantizedAtCapture", "unproven")}}


def _rgb_nonzero(image):
    channels = image.convert("RGB").split()
    return ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2]).point(lambda value: 255 if value else 0)


def _resolve_native_pairs(chart, records, masks, joint_evidence):
    by_id = {row["id"]: row for row in records}
    evidence = joint_evidence.get("nativeCompositionPairs", {})
    resolved = []
    markers = {}
    assigned = {}
    covered_masks = {}
    for request in native_composition_requests(chart, records, masks):
        pair = evidence.get(request["id"])
        if not isinstance(pair, Mapping):
            continue
        try:
            if pair.get("complete") is not True or any(pair.get(key) != value for key, value in request.items()):
                raise ValueError("native_composition_pair_identity_invalid")
            target, partner = by_id[request["targetId"]], by_id[request["partnerTargetId"]]
            raw, other = masks[target["id"]], masks[partner["id"]]
            binding, relation, obligation, datum, neighbor = _composition_binding(chart, target, partner, request["kind"])
            if request != _native_pair_request(request["kind"], target, partner, binding):
                raise ValueError("native_composition_pair_binding_invalid")
            matrix, dpr, size = _matrix(raw["canvasMatrix"]), raw["deviceScaleFactor"], raw["footprint"].size
            if (matrix != _matrix(other["canvasMatrix"]) or matrix != _matrix(pair["canvasMatrix"])
                    or dpr != other["deviceScaleFactor"] or dpr != pair["deviceScaleFactor"]
                    or any(row.get("clippedQids") != [] or row.get("unresolvedRanges") != [] for row in (target, partner))):
                raise ValueError("native_composition_capture_identity_invalid")
            target_bundle, partner_bundle = _native_bundle(raw, target), _native_bundle(other, partner)
            alpha = pair["nativeAlpha"]
            if alpha.mode != "L" or alpha.size != size:
                raise ValueError("native_composition_pair_alpha_invalid")
            footprint = _strip(alpha.point(lambda value: 255 if value else 0), datum["geometrySupport"], matrix, dpr)
            if not footprint.getbbox() or not _same_native_mask(footprint, _binary(pair["footprint"], size)):
                raise ValueError("native_composition_pair_footprint_invalid")
            planes = {name: pair[name].convert("RGB") for name in
                      ("expectedResponse", "currentResponse", "referenceOriginal", "currentOriginal", "currentAbsent")}
            if any(image.size != size for image in planes.values()):
                raise ValueError("native_composition_pair_plane_size_invalid")
            original = _intersection(_rgb_nonzero(ImageChops.difference(planes["currentOriginal"], planes["currentAbsent"])), footprint)
            altered = _intersection(_union([
                _rgb_nonzero(ImageChops.difference(planes["expectedResponse"], planes["currentResponse"])),
                _rgb_nonzero(ImageChops.difference(planes["currentOriginal"], planes["referenceOriginal"]))], size), footprint)
            if (not _same_native_mask(original, _binary(pair["originalContribution"], size))
                    or not _same_native_mask(altered, _binary(pair["altered"], size))
                    or _binary(raw["unproven"], size).getbbox() or _binary(other["unproven"], size).getbbox()):
                raise ValueError("native_composition_pair_original_paint_unproven")
            incident_ids = []
            off_trace = Image.new("L", size)
            if request["kind"] == "marker-on-datum":
                demand = _intersection(boundary_mask(obligation, size, matrix, dpr), raw["footprint"])
                off_trace = ImageChops.subtract(_intersection(other["originalContribution"], other["footprint"]),
                                                raw["alpha"].point(lambda value: 255 if value else 0))
                if (_binary(raw["altered"], size).getbbox() or _binary(other["altered"], size).getbbox()
                        or not off_trace.getbbox() or partner.get("complete") is not True or partner.get("paintRetained") is not True
                        or ImageChops.subtract(demand, _binary(other["footprint"], size)).getbbox()):
                    raise ValueError("native_composition_marker_original_paint_unproven")
                incident_ids = [item["id"] for item in datum["geometrySupport"]["visibilityObligations"]
                                if str(item.get("kind", "")).startswith("incident-edge-")]
                if not incident_ids:
                    raise ValueError("native_composition_marker_incident_support_missing")
            else:
                topology = relation["polarTopology"]
                if topology.get("fillInteriorsDisjoint") is not True or topology.get("contourTopologyChecked") is not True:
                    raise ValueError("native_composition_polar_topology_invalid")
                radial = boundary_mask({"segments": topology["sharedRadialSegments"], "radiusPhysicalPixels": 1,
                    "regionRule": "native-alpha-boundary-neighborhood/v1"}, size, matrix, dpr)
                permitted = _intersection(radial, raw["alpha"].point(lambda value: 255 if value else 0),
                                          other["alpha"].point(lambda value: 255 if value else 0))
                previous = assigned.get(target["id"], Image.new("L", size))
                demand = _intersection(ImageChops.subtract(_binary(raw["altered"], size), previous), permitted)
            covered = _intersection(demand, original, footprint)
            # Pair planes may expose unrelated reference-only AA mismatches.
            # They confer authority only on this independently derived demand;
            # all source-target pixels outside it retain their old strict gates.
            if _intersection(altered, demand).getbbox():
                raise ValueError("native_composition_joint_demand_altered")
            if not demand.getbbox() or not _same_native_mask(demand, covered):
                raise ValueError("native_composition_joint_demand_unproven")
            receipt = {"schema": "native-composition-joint-pair/v1", "request": request,
                "bindingDigest": sha256_prefixed(canonical_json_bytes(binding)), "datumId": datum["id"],
                "partnerDatumId": neighbor["id"], "geometrySupportDigest": datum["geometrySupport"]["geometryDigest"],
                "partnerGeometrySupportDigest": neighbor["geometrySupport"]["geometryDigest"],
                "relationDigest": sha256_prefixed(canonical_json_bytes(relation)), "canvasMatrix": list(matrix),
                "deviceScaleFactor": dpr, "pairMasks": {"nativeAlpha": encode_native_raster(alpha),
                    "footprint": encoded_mask_record(footprint, alpha), "originalContribution": encoded_mask_record(original, alpha),
                    "altered": encoded_mask_record(altered, alpha)}, "targetMasks": target_bundle, "partnerMasks": partner_bundle,
                "regionDemand": encoded_mask_record(demand, raw["alpha"]),
                "coveredRegion": encoded_mask_record(covered, raw["alpha"]),
                "offTraceOriginalContribution": encoded_mask_record(off_trace, other["alpha"]),
                "incidentSupportIds": incident_ids,
                "capturePlanes": {name: [encode_native_raster(channel) for channel in image.split()]
                                  for name, image in planes.items()}}
            resolved.append(receipt)
            covered_masks[request["id"]] = covered
            if request["kind"] == "marker-on-datum":
                markers[(target["id"], obligation["id"])] = receipt
            else:
                assigned[target["id"]] = ImageChops.lighter(assigned.get(target["id"], Image.new("L", size)), covered)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    # A target is corrected only when its independently measured denominator
    # is exhausted by the disjoint typed-neighbor patches. Partial unions fail.
    completed = {target_id for target_id, coverage in assigned.items()
                 if _same_native_mask(coverage, _binary(masks[target_id]["altered"]))}
    resolved = [row for row in resolved if row["request"]["kind"] == "marker-on-datum"
                or row["request"]["targetId"] in completed]
    for target_id in completed:
        raw, target, coverage = masks[target_id], by_id[target_id], assigned[target_id]
        raw["altered"] = ImageChops.subtract(_binary(raw["altered"]), coverage)
        raw["ownedOverlap"] = ImageChops.lighter(_binary(raw["ownedOverlap"]), coverage)
        target["altered"] = mask_record(raw["altered"], raw["alpha"])
        target["ownedOverlap"] = mask_record(raw["ownedOverlap"], raw["alpha"])
    return resolved, markers, {row["request"]["id"]: covered_masks[row["request"]["id"]] for row in resolved}


def _resolve_area_layers(chart, records, masks, target_results):
    if chart.get("chartType") != "area":
        return []
    layers = [row for row in chart["compositionGroup"].get("relationships", []) if row.get("kind") == "native-series-layer-order"]
    if len(layers) != 1:
        return []
    by_id = {row["id"]: row for row in records}
    by_result = {row["targetId"]: row for row in target_results}
    candidates = []
    for datum in chart["datums"]:
        for qid in datum.get("primitiveQids", []):
            target_id = f"{chart['id']}/primitive/{qid}/{datum['id']}"
            if target_id not in by_id or target_id not in masks:
                return []
            candidates.append((by_id[target_id], datum, masks[target_id]))
    qids = sorted({row[0]["qid"] for row in candidates}, key=lambda qid: int(qid[4:]))
    if layers[0].get("primitiveQids") != qids:
        return []
    resolved = {}
    receipts = {}
    try:
        for record, datum, raw in sorted(candidates, key=lambda row: (-int(row[0]["qid"][4:]), row[0]["id"])):
            support, target_id = datum["geometrySupport"], record["id"]
            matrix, dpr, alpha = _matrix(raw["canvasMatrix"]), raw["deviceScaleFactor"], raw["alpha"]
            size = alpha.size
            bundle = _native_bundle(raw, record)
            footprint = _strip(alpha.point(lambda value: 255 if value else 0), support, matrix, dpr)
            if (not footprint.getbbox() or not _same_native_mask(footprint, _binary(raw["footprint"], size))
                    or _binary(raw["altered"], size).getbbox() or _binary(raw["unproven"], size).getbbox()
                    or record.get("clippedQids") != [] or record.get("unresolvedRanges") != []):
                raise ValueError("native_area_capture_unproven")
            visible = _intersection(footprint, raw["originalContribution"])
            remaining = ImageChops.subtract(footprint, visible)
            owners = []
            for relation in record.get("ownedRelationships", []):
                if relation.get("kind") != "native-series-layer-order":
                    continue
                # Record-local permission is not authority by itself: require
                # the exact unique row from the server composition manifest.
                declared = [row for row in layers[0].get("localOverlaps", []) if isinstance(row, Mapping)
                            and canonical_json_bytes({"kind": layers[0]["kind"], **row}) == canonical_json_bytes(relation)]
                if len(declared) != 1:
                    raise ValueError("native_area_layer_membership_invalid")
                expected_local = {key: support[key] for key in ("anchor", "categoryStrip", "baseline") if key in support}
                if (relation.get("datumId") != datum["id"] or relation.get("targetQid") != record["qid"]
                        or relation.get("geometrySupportDigest") != support["geometryDigest"]
                        or relation.get("geometrySupport") != expected_local
                        or relation.get("regionRule") != "native-alpha-intersection-in-datum-support/v1"
                        or int(relation["occluderQid"][4:]) <= int(record["qid"][4:])):
                    raise ValueError("native_area_layer_identity_invalid")
                for other_id, other in resolved.items():
                    if other["record"]["qid"] == relation["occluderQid"]:
                        owners.append((int(other["record"]["qid"][4:]), other_id, relation, other))
            rows = []
            for _, other_id, relation, other in sorted(owners, key=lambda row: (row[0], row[1])):
                if (other["size"] != size or other["matrix"] != matrix or other["dpr"] != dpr
                        or other["datum"]["geometrySupport"].get("axisKey") != support.get("axisKey")
                        or other["datum"]["geometrySupport"].get("unitKey") != support.get("unitKey")):
                    raise ValueError("native_area_capture_identity_mismatch")
                coverage = _intersection(remaining, other["preserved"], other["alpha"].point(lambda value: 255 if value == 255 else 0),
                                          other["footprint"], raw["ownedOverlap"])
                if not coverage.getbbox():
                    continue
                rows.append({"targetId": other_id, "datumId": other["datum"]["id"], "qid": other["record"]["qid"],
                    "geometrySupportDigest": other["datum"]["geometrySupport"]["geometryDigest"],
                    "relationDigest": sha256_prefixed(canonical_json_bytes(relation)), "coverage": encoded_mask_record(coverage, alpha)})
                remaining = ImageChops.subtract(remaining, coverage)
            if remaining.getbbox():
                raise ValueError("native_area_pixel_coverage_incomplete")
            hidden = ImageChops.subtract(footprint, visible)
            preserved = ImageChops.subtract(footprint, remaining)
            receipt = {"kind": "native-area-owned-layer-preservation/v1", "targetId": target_id, "datumId": datum["id"],
                "qid": record["qid"], "geometrySupportDigest": support["geometryDigest"], "axisKey": support.get("axisKey"),
                "unitKey": support.get("unitKey"), "canvasMatrix": list(matrix), "deviceScaleFactor": dpr,
                "visibility": "preserved-hidden-by-owned" if hidden.getbbox() else "visible-native-contribution",
                "sourceVisible": not bool(hidden.getbbox()), **bundle,
                "preserved": encoded_mask_record(preserved, alpha), "hidden": encoded_mask_record(hidden, alpha),
                "residual": encoded_mask_record(remaining, alpha), "occluders": rows}
            resolved[target_id] = {"record": record, "datum": datum, "size": size, "matrix": matrix, "dpr": dpr,
                                   "alpha": alpha, "footprint": footprint, "preserved": preserved}
            receipts[target_id] = receipt
        # Validate every demand before any accepted target/obligation changes.
        for record, datum, raw in candidates:
            if record["id"] not in by_result:
                raise ValueError("native_area_target_support_missing")
            footprint, alpha = resolved[record["id"]]["footprint"], raw["alpha"]
            specifications = datum["geometrySupport"].get("visibilityObligations", [])
            if {item["id"] for item in specifications} != {item["supportId"] for item in by_result[record["id"]]["obligations"]}:
                raise ValueError("native_area_obligation_identity_invalid")
            for specification in specifications:
                selected = _intersection(boundary_mask(specification, alpha.size, raw["canvasMatrix"], raw["deviceScaleFactor"]), footprint)
                if not selected.getbbox():
                    raise ValueError("native_area_support_demand_empty")
        # Commit only after the entire DAG and every server demand terminate.
        for record, datum, raw in candidates:
            result = by_result[record["id"]]
            footprint, alpha = resolved[record["id"]]["footprint"], raw["alpha"]
            for result_obligation in result["obligations"]:
                specification = next(item for item in datum["geometrySupport"]["visibilityObligations"]
                                     if item["id"] == result_obligation["supportId"])
                selected = _intersection(boundary_mask(specification, alpha.size, raw["canvasMatrix"], raw["deviceScaleFactor"]), footprint)
                original = _intersection(selected, raw["originalContribution"])
                hidden_boundary = ImageChops.subtract(selected, original)
                result_obligation.update(complete=True, paintRetained=True, reason=None,
                    footprint=mask_record(selected, alpha), originalContribution=mask_record(original, alpha),
                    boundaryPixels=_count(selected), supportedPixels=_count(selected),
                    via="native-area-owned-layer-preservation/v1" if hidden_boundary.getbbox() else "original-boundary-contribution")
                if hidden_boundary.getbbox():
                    result_obligation["areaVisibilityTargetId"] = record["id"]
            result.update(complete=True, paintRetained=True, reason=None)
        return [receipts[record["id"]] for record, _, _ in candidates]
    except (KeyError, TypeError, ValueError, AttributeError):
        return []


def resolve_chart_support(chart,records,masks,joint_evidence):
    """Resolve manifest support alternatives only against measured native paint.

    ``joint_evidence`` additionally contains a ``boundaryContrast`` map keyed
    by obligation ID + "|" + neighbor target ID. Each entry contains the
    exact normal-pair witness and its raw footprint, geometric-pair,
    native-contrast and actual-contribution masks.
    Contribution regions come only from exact declared server geometry and
    measured native original-contribution masks. Other keys are exact
    equivalence IDs -> measured joint-class mask bundles.
    No already-resolved alternative may serve as another alternative's root.
    """
    pair_receipts, marker_pairs, pair_coverage = _resolve_native_pairs(chart, records, masks, joint_evidence)
    chart_id=chart["id"]
    by_id={record["id"]:record for record in records
           if record.get("chartId")==chart_id or str(record.get("id","")).startswith(chart_id+"/")}
    datums={datum["id"]:datum for datum in chart["datums"]}
    relationships=chart["compositionGroup"].get("relationships",[])
    observations={}
    errors={}
    semantic_reasons={"original_color_semantic_contribution_empty","reference_observable_contribution_empty"}
    required=("footprint","originalContribution","retained","ownedOverlap","altered","alpha")
    for target_id,record in by_id.items():
        try:
            bundle=masks[target_id]
            footprint=_binary(bundle["footprint"])
            measured={key:_binary(bundle[key],footprint.size) for key in required}
            matrix=_matrix(bundle["canvasMatrix"])
            dpr=_finite(bundle.get("deviceScaleFactor",bundle.get("dpr")))
            if dpr<=0:
                raise ValueError("chart_support_device_scale_missing")
            if record.get("complete") is not True and record.get("reason") not in semantic_reasons:
                raise ValueError(record.get("reason") or "chart_support_capture_incomplete")
            if isinstance(bundle.get("unproven"),Image.Image) and _binary(bundle["unproven"],footprint.size).getbbox():
                raise ValueError("chart_support_paint_unproven")
            measured.update(matrix=matrix,dpr=dpr,record=record,size=footprint.size,bundle=bundle)
            measured["foreignAltered"]=bool(measured["altered"].getbbox())
            observations[target_id]=measured
        except (KeyError,TypeError,ValueError) as error:
            errors[target_id]=str(error)

    obligations={}
    boundaries={}
    direct={}
    datum_targets={}
    for datum_id,datum in datums.items():
        for qid in datum.get("primitiveQids",[]):
            target_id=f"{chart_id}/primitive/{qid}/{datum_id}"
            datum_targets[target_id]=datum
            measured=observations.get(target_id)
            if measured is not None and (measured["record"].get("qid")!=qid or
                    measured["record"].get("kind")!="primitive" or datum_id not in measured["record"].get("datumIds",[])):
                errors[target_id]="native_support_target_identity_mismatch"
                observations.pop(target_id)
            for obligation in datum.get("geometrySupport",{}).get("visibilityObligations",[]):
                if obligation.get("primitiveQid")!=qid:
                    continue
                key=(target_id,obligation["id"])
                obligations[key]=obligation
                observation=observations.get(target_id)
                if observation is None:
                    continue
                try:
                    boundary=boundary_mask(obligation,observation["size"],observation["matrix"],observation["dpr"])
                    boundary=_strip(boundary,datum["geometrySupport"],observation["matrix"],observation["dpr"])
                    boundaries[key]=boundary
                    contribution=_intersection(boundary,observation["footprint"],observation["originalContribution"])
                    shared_stack=any(alternative.get("kind") in {"shared-stack-interface","bound-stack-value-glyphs"}
                                     for alternative in obligation.get("alternatives",[]))
                    direct[key]=(not shared_stack and bool(contribution.getbbox()) and
                                 not observation["foreignAltered"] and
                                 observation["record"].get("reason")!="reference_observable_contribution_empty")
                except (KeyError,TypeError,ValueError) as error:
                    errors[target_id]=str(error)

    contribution_cache={}
    def contribution(target_id,region_id,kind):
        observation=observations.get(target_id)
        datum=datum_targets.get(target_id)
        if observation is None or datum is None or observation["foreignAltered"]:
            return False
        support=datum.get("geometrySupport",{})
        target_support=observation["record"].get("geometrySupport",{})
        digest=support.get("geometryDigest")
        if not isinstance(digest,str) or target_support.get("geometryDigest")!=digest:
            raise ValueError("native_contribution_geometry_support_mismatch")
        if not isinstance(region_id,str):
            raise ValueError("native_contribution_region_reference_missing")
        regions=[region for region in support.get("contributionRegions",[]) if region.get("id")==region_id]
        if len(regions)!=1:
            raise ValueError("native_contribution_region_not_unique")
        region=regions[0]
        if region.get("kind")!=kind or region.get("primitiveQid")!=observation["record"].get("qid"):
            raise ValueError("native_contribution_region_identity_mismatch")
        key=(target_id,region_id)
        if key not in contribution_cache:
            selected=boundary_mask(region,observation["size"],observation["matrix"],observation["dpr"])
            selected=_strip(selected,support,observation["matrix"],observation["dpr"])
            contribution_cache[key]=_intersection(selected,observation["footprint"],observation["originalContribution"])
        return bool(contribution_cache[key].getbbox())

    def same_support(target_id,datum_id,digest):
        datum=datums.get(datum_id)
        return bool(datum and target_id in datum_targets and datum_targets[target_id]["id"]==datum_id and
                    datum["geometrySupport"].get("geometryDigest")==digest)

    def endpoint_reference(reference,datum,observation,endpoint_kind):
        fields={"datumId","targetId","supportId","geometrySupportDigest"}
        if not isinstance(reference,Mapping) or set(reference)!=fields:
            raise ValueError("bound_stack_endpoint_reference_invalid")
        endpoint=datums.get(reference["datumId"])
        endpoint_id=reference["targetId"]
        endpoint_key=(endpoint_id,reference["supportId"])
        if (endpoint is None or not same_support(endpoint_id,endpoint["id"],reference["geometrySupportDigest"]) or
            endpoint_key not in obligations or endpoint.get("categoryIndex")!=datum.get("categoryIndex") or
            endpoint.get("geometrySupport",{}).get("axisKey")!=datum["geometrySupport"].get("axisKey") or
            endpoint.get("geometrySupport",{}).get("unitKey")!=datum["geometrySupport"].get("unitKey")):
            raise ValueError("bound_stack_endpoint_identity_mismatch")
        source_qids=endpoint.get("primitiveQids",[])
        source=observations.get(endpoint_id)
        if (not source_qids or endpoint_id!=f"{chart_id}/primitive/{source_qids[0]}/{endpoint['id']}" or
            obligations[endpoint_key].get("primitiveQid")!=source_qids[0] or
            obligations[endpoint_key].get("kind")!=endpoint_kind or source is None or source["foreignAltered"] or
            source["record"].get("geometrySupport",{}).get("geometryDigest")!=reference["geometrySupportDigest"] or
            source["size"]!=observation["size"] or source["matrix"]!=observation["matrix"] or source["dpr"]!=observation["dpr"]):
            raise ValueError("bound_stack_endpoint_source_geometry_mismatch")
        chain=[item for item in datums.values() if item.get("categoryIndex")==datum.get("categoryIndex")]
        if any(type(item.get("seriesIndex")) is not int for item in chain):
            raise ValueError("bound_stack_category_chain_unproven")
        expected=(min if endpoint_kind=="value-base" else max)(item["seriesIndex"] for item in chain)
        if endpoint.get("seriesIndex")!=expected:
            raise ValueError("bound_stack_endpoint_category_chain_mismatch")
        return endpoint_key

    def bound_stack_candidate(alternative,target_id,record,obligation,datum,observation):
        fields={"kind","neighborDatumId","neighborTargetId","neighborSupportId","geometrySupportDigest",
                "cumulativeValue","grouping","axisKey","unitKey","datumValueGlyphTargetIds",
                "neighborValueGlyphTargetIds","datumInteriorSupportId","neighborInteriorSupportId",
                "stackBaseline","stackOuterTotal"}
        if set(alternative)!=fields or chart.get("chartType") not in {"bar","column"}:
            raise ValueError("bound_stack_alternative_shape_invalid")
        neighbor_id=alternative["neighborTargetId"]
        neighbor_key=(neighbor_id,alternative["neighborSupportId"])
        neighbor=datums.get(alternative["neighborDatumId"])
        support=datum["geometrySupport"]
        if (not same_support(neighbor_id,alternative["neighborDatumId"],alternative["geometrySupportDigest"]) or
            neighbor_key not in obligations or neighbor is None or
            obligations[neighbor_key].get("segments")!=obligation.get("segments") or
            not obligation.get("segments") or obligation.get("kind") not in {"value-base","value-end"} or
            obligations[neighbor_key].get("kind") not in {"value-base","value-end"} or
            obligation.get("kind")==obligations[neighbor_key].get("kind") or
            neighbor.get("categoryIndex")!=datum.get("categoryIndex") or
            abs(neighbor.get("seriesIndex")-datum.get("seriesIndex"))!=1 or
            alternative["axisKey"]!=support.get("axisKey") or
            alternative["axisKey"]!=neighbor["geometrySupport"].get("axisKey") or
            alternative["unitKey"]!=support.get("unitKey") or
            alternative["unitKey"]!=neighbor["geometrySupport"].get("unitKey") or
            alternative["grouping"] not in {"stacked","percentStacked"} or
            not isinstance(alternative["cumulativeValue"],str) or not alternative["cumulativeValue"] or
            record.get("geometrySupport",{}).get("geometryDigest")!=support.get("geometryDigest")):
            raise ValueError("bound_stack_seam_identity_mismatch")
        for own_datum,identifier in ((datum,target_id),(neighbor,neighbor_id)):
            qids=own_datum.get("primitiveQids",[])
            if not qids or identifier!=f"{chart_id}/primitive/{qids[0]}/{own_datum['id']}":
                raise ValueError("bound_stack_source_profile_mismatch")
        sibling_fields=("neighborDatumId","neighborTargetId","neighborSupportId","geometrySupportDigest",
                        "cumulativeValue","grouping","axisKey","datumInteriorSupportId","neighborInteriorSupportId")
        siblings=[item for item in obligation.get("alternatives",[]) if isinstance(item,Mapping) and
                  item.get("kind")=="shared-stack-interface" and item.get("neighborDatumId")==neighbor["id"]]
        if siblings and not any(all(item.get(field)==alternative[field] for field in sibling_fields) for item in siblings):
            raise ValueError("bound_stack_sibling_support_mismatch")
        neighbor_observation=observations.get(neighbor_id)
        if (neighbor_observation is None or neighbor_observation["foreignAltered"] or
            neighbor_observation["record"].get("geometrySupport",{}).get("geometryDigest")!=alternative["geometrySupportDigest"] or
            neighbor_observation["size"]!=observation["size"] or neighbor_observation["matrix"]!=observation["matrix"] or
            neighbor_observation["dpr"]!=observation["dpr"]):
            raise ValueError("bound_stack_neighbor_capture_unproven")
        for name,owner in (("datumValueGlyphTargetIds",datum),("neighborValueGlyphTargetIds",neighbor)):
            identifiers=alternative[name]
            if (not isinstance(identifiers,list) or not identifiers or
                any(not isinstance(identifier,str) for identifier in identifiers) or len(set(identifiers))!=len(identifiers)):
                raise ValueError("bound_stack_value_glyph_references_invalid")
            for identifier in identifiers:
                label=observations.get(identifier)
                if (label is None or label["record"].get("kind")!="glyph-range" or
                    "value" not in label["record"].get("roles",[]) or
                    set(label["record"].get("datumIds",[]))!={owner["id"]} or
                    label["record"].get("complete") is not True or label["record"].get("paintRetained") is not True or
                    label["foreignAltered"] or not label["originalContribution"].getbbox() or
                    label["size"]!=observation["size"] or label["matrix"]!=observation["matrix"] or label["dpr"]!=observation["dpr"]):
                    raise ValueError("bound_stack_value_glyph_measurement_unproven")
        if (not contribution(target_id,alternative["datumInteriorSupportId"],"interior-profile") or
            not contribution(neighbor_id,alternative["neighborInteriorSupportId"],"interior-profile")):
            raise ValueError("bound_stack_interior_profile_contribution_empty")
        baseline=endpoint_reference(alternative["stackBaseline"],datum,observation,"value-base")
        outer=endpoint_reference(alternative["stackOuterTotal"],datum,observation,"value-end")
        return baseline,outer

    class_results={}
    class_members={}
    for equivalent in chart["compositionGroup"].get("equivalenceClasses",[]):
        identifier=equivalent["id"]
        result={"classId":identifier,"complete":False,"paintRetained":False,"via":None,"reason":None}
        members=equivalent.get("members",[])
        valid=bool(members) and equivalent.get("requiresJointOriginalContribution") is True
        keys=[]
        for member in members:
            datum=datums.get(member.get("datumId"))
            key=(member.get("targetId"),member.get("supportId"))
            if (not datum or not same_support(key[0],datum["id"],member.get("geometrySupportDigest")) or
                key not in obligations or datum["geometrySupport"].get("axisKey")!=equivalent.get("axisKey") or
                datum["geometrySupport"].get("unitKey")!=equivalent.get("unitKey") or
                datum.get("categoryIndex")!=equivalent.get("categoryIndex") or datum.get("value")!=equivalent.get("value")):
                valid=False
            keys.append(key)
        if valid:
            first=obligations[keys[0]]
            identity=lambda obligation: (
                "incident-edge" if obligation.get("kind","").startswith("incident-edge") else obligation.get("kind"),
                sorted(sorted(segment) for segment in obligation.get("segments",[])), obligation.get("ellipse"))
            if any(identity(obligations[key])!=identity(first) for key in keys):
                valid=False
        class_members[identifier]=set(keys)
        if not valid:
            result["reason"]="equivalent_support_identity_mismatch"
        elif any(direct.get(key) for key in keys):
            contributor=next(key for key in keys if direct.get(key))
            result.update(complete=True,paintRetained=True,via="contributing-native-member",contributorTargetId=contributor[0])
        else:
            joint=joint_evidence.get(identifier)
            try:
                if not isinstance(joint,Mapping) or joint.get("complete") is not True:
                    raise ValueError("joint_original_contribution_measurement_missing")
                if set(joint.get("memberTargetIds",[]))!={key[0] for key in keys}:
                    raise ValueError("joint_support_members_mismatch")
                first=observations.get(keys[0][0])
                if first is None or any(key not in boundaries for key in keys):
                    raise ValueError("joint_support_boundary_measurement_missing")
                footprint=_binary(joint.get("footprint"),first["size"])
                contribution=_binary(joint.get("originalContribution"),first["size"])
                retained=_binary(joint.get("retained"),first["size"])
                altered=_binary(joint.get("altered"),first["size"])
                owned=_binary(joint.get("ownedOverlap",Image.new("L",first["size"],0)),first["size"])
                if altered.getbbox() or ImageChops.subtract(footprint,ImageChops.lighter(retained,owned)).getbbox():
                    raise ValueError("joint_native_paint_not_preserved")
                if isinstance(joint.get("unproven"),Image.Image) and _binary(joint["unproven"],first["size"]).getbbox():
                    raise ValueError("joint_native_paint_unproven")
                if not all(_intersection(boundaries[key],observations[key[0]]["footprint"],footprint,contribution).getbbox() for key in keys):
                    raise ValueError("joint_original_boundary_contribution_empty")
                result.update(complete=True,paintRetained=True,via="joint-original-native-contribution")
            except (KeyError,TypeError,ValueError) as error:
                result["reason"]=str(error)
        class_results[identifier]=result

    target_results=[]
    deferred=[]
    for target_id,record in by_id.items():
        observation=observations.get(target_id)
        results=[]
        for key,obligation in obligations.items():
            if key[0]!=target_id:
                continue
            result={"supportId":obligation["id"],"complete":False,"paintRetained":False,
                    "via":None,"reason":None,"boundaryPixels":0,"supportedPixels":0}
            boundary=boundaries.get(key)
            datum=datum_targets[target_id]
            if observation is None or boundary is None:
                result["reason"]=errors.get(target_id,"boundary_measurement_missing")
                results.append(result)
                continue
            result["boundaryPixels"]=_count(_intersection(boundary,observation["footprint"]))
            result["supportedPixels"]=_count(_intersection(boundary,observation["footprint"],observation["originalContribution"]))
            marker_pair = marker_pairs.get(key)
            if marker_pair is not None and all(direct.get((target_id, identifier)) is True
                                               for identifier in marker_pair["incidentSupportIds"]):
                result.update(complete=True, paintRetained=True, via="marker-joint-original-contribution/v1",
                    supportedPixels=result["boundaryPixels"], nativeCompositionPairId=marker_pair["request"]["id"])
            elif direct.get(key):
                result.update(complete=True,paintRetained=True,via="original-boundary-contribution")
            failures=[]
            for alternative in obligation.get("alternatives",[]):
                if result["complete"]:
                    break
                if not isinstance(alternative,Mapping):
                    failures.append("native_support_alternative_invalid")
                    continue
                kind=alternative.get("kind")
                try:
                    if kind=="marker-on-datum":
                        marker_id=alternative.get("markerTargetId")
                        marker_qid=alternative.get("markerQid")
                        digest=datum.get("geometrySupport",{}).get("geometryDigest")
                        source_qids=datum.get("primitiveQids",[])
                        if (chart.get("chartType") not in {"line","area"} or not source_qids or
                            source_qids[0]!=record.get("qid") or obligation.get("primitiveQid")!=record.get("qid") or
                            not (obligation.get("kind")=="value-anchor" or
                                 obligation.get("kind","").startswith("incident-edge")) or
                            alternative.get("geometrySupportDigest")!=digest or
                            observation["record"].get("geometrySupport",{}).get("geometryDigest")!=digest or
                            alternative.get("requiresEntireBoundaryCoverage") is not True or
                            alternative.get("requiresOriginalContribution") is not True or
                            not isinstance(marker_qid,str) or not marker_qid.startswith("svg-") or not marker_qid[4:].isdigit() or
                            marker_qid==record.get("qid") or int(marker_qid[4:])<=int(record["qid"][4:]) or
                            marker_id!=f"{chart_id}/primitive/{marker_qid}/{datum['id']}" or
                            not same_support(marker_id,datum["id"],digest)):
                            raise ValueError("marker_boundary_support_identity_mismatch")
                        marker=observations.get(marker_id)
                        if (marker is None or marker["size"]!=observation["size"] or
                            marker["matrix"]!=observation["matrix"] or marker["dpr"]!=observation["dpr"] or
                            marker["record"].get("qid")!=marker_qid or marker["foreignAltered"] or
                            marker["record"].get("geometrySupport",{}).get("geometryDigest")!=digest or
                            marker["record"].get("complete") is not True or marker["record"].get("paintRetained") is not True or
                            set(marker["record"].get("datumIds",[]))!={datum["id"]}):
                            raise ValueError("marker_boundary_capture_unproven")
                        demanded=_intersection(boundary,observation["footprint"])
                        marker_boundary=_intersection(demanded,marker["footprint"])
                        marker_contribution=_intersection(marker_boundary,marker["originalContribution"])
                        if (not demanded.getbbox() or ImageChops.subtract(demanded,marker_boundary).getbbox() or
                            ImageChops.subtract(demanded,marker_contribution).getbbox()):
                            raise ValueError("marker_entire_boundary_contribution_unproven")
                        result.update(complete=True,paintRetained=True,via=kind,supportTargetId=marker_id,
                            supportedPixels=_count(marker_contribution),markerBoundary={
                                "targetId":target_id,"supportId":obligation["id"],
                                "markerTargetId":marker_id,"markerQid":marker_qid,
                                "footprint":mask_record(marker_boundary,observation["bundle"]["alpha"]),
                                "originalContribution":mask_record(marker_contribution,observation["bundle"]["alpha"])})
                    elif kind=="equivalent-native-support":
                        identifier=alternative.get("classId")
                        equivalent=class_results.get(identifier)
                        if key not in class_members.get(identifier,set()) or not equivalent or not equivalent["complete"] or not equivalent["paintRetained"]:
                            raise ValueError("equivalent_native_support_unproven")
                        result.update(complete=True,paintRetained=True,via=kind,classId=identifier)
                    elif kind=="bound-stack-value-glyphs":
                        baseline,outer=bound_stack_candidate(alternative,target_id,record,obligation,datum,observation)
                        deferred.append({"key":key,"result":result,"alternative":alternative,
                            "baseline":baseline,"outer":outer})
                    elif kind=="shared-stack-interface":
                        neighbor_id=alternative.get("neighborTargetId")
                        neighbor_key=(neighbor_id,alternative.get("neighborSupportId"))
                        if (not same_support(neighbor_id,alternative.get("neighborDatumId"),alternative.get("geometrySupportDigest")) or
                            neighbor_key not in obligations or obligations[neighbor_key].get("segments")!=obligation.get("segments") or
                            alternative.get("axisKey")!=datum["geometrySupport"].get("axisKey") or
                            alternative.get("requiresBothInteriorContribution") is not True or alternative.get("requiresBoundaryContrast") is not True):
                            raise ValueError("shared_stack_support_identity_mismatch")
                        neighbor=datums[alternative["neighborDatumId"]]
                        if (neighbor.get("categoryIndex")!=datum.get("categoryIndex") or
                            abs(neighbor.get("seriesIndex")-datum.get("seriesIndex"))!=1 or
                            neighbor["geometrySupport"].get("axisKey")!=datum["geometrySupport"].get("axisKey") or
                            neighbor["geometrySupport"].get("unitKey")!=datum["geometrySupport"].get("unitKey")):
                            raise ValueError("shared_stack_datum_identity_mismatch")
                        neighbor_observation=observations.get(neighbor_id)
                        if (neighbor_observation is None or neighbor_observation["size"]!=observation["size"] or
                            neighbor_observation["matrix"]!=observation["matrix"] or neighbor_observation["dpr"]!=observation["dpr"]):
                            raise ValueError("shared_stack_capture_identity_mismatch")
                        own_region=alternative.get("datumInteriorSupportId")
                        neighbor_region=alternative.get("neighborInteriorSupportId")
                        if (not contribution(target_id,own_region,"interior-profile") or
                            not contribution(neighbor_id,neighbor_region,"interior-profile")):
                            raise ValueError("shared_stack_declared_interior_contribution_empty")
                        measured=joint_evidence.get("boundaryContrast",{}).get(obligation["id"]+"|"+neighbor_id)
                        if not isinstance(measured,Mapping) or measured.get("complete") is not True:
                            raise ValueError("stack_boundary_normal_measurement_missing")
                        witness=measured.get("witness")
                        expected_identity={"targetId":target_id,"supportId":obligation["id"],
                            "neighborTargetId":neighbor_id,"neighborSupportId":alternative["neighborSupportId"],
                            "datumInteriorSupportId":own_region,"neighborInteriorSupportId":neighbor_region,
                            "samplingRule":"normal-interior-native-pair/v1"}
                        if not isinstance(witness,Mapping) or any(witness.get(name)!=value for name,value in expected_identity.items()):
                            raise ValueError("stack_boundary_normal_witness_identity_mismatch")
                        expected_footprint=_intersection(boundary,observation["footprint"])
                        measured_footprint=_binary(measured.get("footprint"),observation["size"])
                        measured_pairs=_binary(measured.get("normalInteriorPairs"),observation["size"])
                        measured_native=_binary(measured.get("nativeContrast"),observation["size"])
                        measured_contribution=_binary(measured.get("originalContribution"),observation["size"])
                        if ImageChops.difference(expected_footprint,measured_footprint).getbbox():
                            raise ValueError("stack_boundary_native_footprint_mismatch")
                        expected_pairs=Image.new("L",observation["size"],0)
                        allowed={}
                        for pixel,negative_profile,positive_profile,horizontal in _stack_normal_profiles(
                                obligation,observation["matrix"],observation["dpr"],
                                observation["bundle"].get("nativeGeometryBounds"),
                                neighbor_observation["bundle"].get("nativeGeometryBounds"),expected_footprint):
                            expected_pairs.putpixel(pixel,255)
                            allowed.setdefault(pixel,[]).append((negative_profile[1:],positive_profile[1:],horizontal))
                        if (ImageChops.difference(expected_pairs,measured_pairs).getbbox() or
                            ImageChops.subtract(measured_native,measured_pairs).getbbox() or
                            ImageChops.subtract(measured_contribution,measured_native).getbbox()):
                            raise ValueError("stack_boundary_normal_pair_mask_mismatch")
                        for name,mask in (("footprint",measured_footprint),("normalInteriorPairs",measured_pairs),
                                          ("nativeContrast",measured_native),("originalContribution",measured_contribution)):
                            if witness.get(name)!=mask_record(mask,observation["bundle"]["alpha"]):
                                raise ValueError("stack_boundary_normal_mask_receipt_mismatch")
                        samples=witness.get("samplePairs")
                        if not isinstance(samples,list) or len(samples)!=_count(measured_contribution):
                            raise ValueError("stack_boundary_normal_sample_count_mismatch")
                        seen=set()
                        for sample in samples:
                            if not isinstance(sample,Mapping) or sample.get("kind") not in {"adjacent-fill","native-divider"}:
                                raise ValueError("stack_boundary_normal_sample_invalid")
                            coordinates=[]
                            for name in ("boundaryPixel","negativeInteriorPixel","positiveInteriorPixel"):
                                value=sample.get(name)
                                if not isinstance(value,list) or len(value)!=2 or any(type(item) is not int for item in value):
                                    raise ValueError("stack_boundary_normal_sample_invalid")
                                coordinates.append(tuple(value))
                            pixel,negative,positive=coordinates
                            def valid_depths(profile):
                                negative_limits,positive_limits,horizontal=profile
                                tangent=0 if horizontal else 1
                                normal=1-tangent
                                return (negative[tangent]==pixel[tangent]==positive[tangent] and
                                        negative_limits[0]<=negative[normal]<=negative_limits[1] and
                                        positive_limits[0]<=positive[normal]<=positive_limits[1])
                            if pixel in seen or not any(valid_depths(profile) for profile in allowed.get(pixel,[])) or not measured_contribution.getpixel(pixel):
                                raise ValueError("stack_boundary_normal_sample_association_mismatch")
                            seen.add(pixel)
                        if not measured_contribution.getbbox():
                            raise ValueError("stack_boundary_normal_contribution_empty")
                        result.update(complete=True,paintRetained=True,via=kind,supportTargetId=neighbor_id,
                            contributionRegionIds=[own_region,neighbor_region],stackBoundaryContrast=dict(witness))
                    elif kind=="calibrated-value-line":
                        if (alternative.get("axisKey")!=datum["geometrySupport"].get("axisKey") or
                            alternative.get("unitKey")!=datum["geometrySupport"].get("unitKey") or
                            alternative.get("requiresDatumInteriorContribution") is not True or alternative.get("requiresIncidentSideContribution") is not True):
                            raise ValueError("calibrated_support_identity_mismatch")
                        grid_id=alternative.get("gridTargetId")
                        grid=observations.get(grid_id)
                        if grid_id!=f"{chart_id}/primitive/{alternative.get('gridQid')}/furniture" or grid is None or grid["record"].get("complete") is not True or grid["foreignAltered"]:
                            raise ValueError("calibrated_grid_paint_unproven")
                        if (grid["size"]!=observation["size"] or grid["matrix"]!=observation["matrix"] or
                            grid["dpr"]!=observation["dpr"] or grid["record"].get("qid")!=alternative.get("gridQid")):
                            raise ValueError("calibrated_grid_capture_identity_mismatch")
                        grid_boundary=_intersection(boundary,observation["footprint"],grid["footprint"])
                        grid_contribution=_intersection(grid_boundary,grid["originalContribution"])
                        if not grid_contribution.getbbox():
                            raise ValueError("calibrated_grid_original_contribution_empty")
                        labels=alternative.get("tickTargetIds",[])+alternative.get("titleTargetIds",[])
                        if not alternative.get("tickTargetIds"):
                            raise ValueError("calibrated_tick_binding_missing")
                        for label_id in labels:
                            label=observations.get(label_id)
                            if label is None or label["record"].get("kind")!="glyph-range" or label["record"].get("complete") is not True or label["foreignAltered"] or not label["originalContribution"].getbbox():
                                raise ValueError("calibrated_tick_or_title_paint_unproven")
                        interior_region=alternative.get("datumInteriorSupportId")
                        side_region=alternative.get("datumSideSupportId")
                        if not contribution(target_id,interior_region,"interior-profile"):
                            raise ValueError("calibrated_declared_interior_contribution_empty")
                        if not contribution(target_id,side_region,"incident-sides"):
                            raise ValueError("calibrated_declared_incident_side_contribution_empty")
                        result.update(complete=True,paintRetained=True,via=kind,supportTargetId=grid_id,
                            contributionRegionIds=[interior_region,side_region],calibratedGridBoundary={
                                "targetId":target_id,"supportId":obligation["id"],
                                "gridTargetId":grid_id,"gridQid":alternative["gridQid"],
                                "tickTargetIds":list(alternative["tickTargetIds"]),
                                "titleTargetIds":list(alternative.get("titleTargetIds",[])),
                                "datumInteriorSupportId":interior_region,"datumSideSupportId":side_region,
                                "footprint":mask_record(grid_boundary,observation["bundle"]["alpha"]),
                                "originalContribution":mask_record(grid_contribution,observation["bundle"]["alpha"])})
                    else:
                        raise ValueError("native_support_alternative_unknown")
                except (KeyError,TypeError,ValueError) as error:
                    failures.append(str(error))
            if (result["complete"] and record.get("reason")=="reference_observable_contribution_empty"):
                opaque=observation["bundle"].get("opaqueOwned")
                approved=result["via"] in {"marker-on-datum","equivalent-native-support","marker-joint-original-contribution/v1"}
                try:
                    covered=not ImageChops.subtract(observation["footprint"],_binary(opaque,observation["size"])).getbbox()
                except ValueError:
                    covered=False
                if not approved or not covered:
                    result.update(complete=False,paintRetained=False,reason="opaque_local_native_support_unproven")
            if not result["complete"] and result["reason"] is None:
                result["reason"]=failures[0] if failures else "original_boundary_contribution_empty"
            if observation["foreignAltered"]:
                result["paintRetained"]=False
                result["reason"]="foreign_paint_altered"
            results.append(result)
        if results:
            complete=observation is not None and all(item["complete"] for item in results)
            retained=complete and not observation["foreignAltered"] and all(item["paintRetained"] for item in results)
        else:
            complete=observation is not None and record.get("complete") is True
            retained=complete and record.get("paintRetained") is True and not observation["foreignAltered"]
        target_results.append({"targetId":target_id,"complete":complete,"paintRetained":retained,
            "reason":errors.get(target_id) if observation is None else None,"obligations":results})
    # Freeze only first-pass obligation roots; deferred glyph candidates never
    # become roots for another candidate, regardless of their iteration order.
    first_pass={(item["targetId"],support["supportId"]):dict(support)
                for item in target_results for support in item["obligations"]
                if support["complete"] and support["paintRetained"] and
                support.get("via") in {"original-boundary-contribution","calibrated-value-line"}}
    def endpoint_terminated(endpoint_key):
        resolved=first_pass.get(endpoint_key)
        if resolved is None:
            return False
        source=observations.get(endpoint_key[0])
        if source is None or source["foreignAltered"]:
            return False
        if resolved["via"]=="original-boundary-contribution":
            return direct.get(endpoint_key) is True
        witness=resolved.get("calibratedGridBoundary")
        if not isinstance(witness,Mapping):
            return False
        specification=obligations[endpoint_key]
        identity_fields=("gridTargetId","gridQid","tickTargetIds","titleTargetIds",
                         "datumInteriorSupportId","datumSideSupportId")
        for alternative in specification.get("alternatives",[]):
            if not isinstance(alternative,Mapping) or alternative.get("kind")!="calibrated-value-line":
                continue
            expected={"targetId":endpoint_key[0],"supportId":endpoint_key[1],
                      **{name:alternative.get(name,[]) if name=="titleTargetIds" else alternative.get(name)
                         for name in identity_fields}}
            if any(witness.get(name)!=value for name,value in expected.items()):
                continue
            grid=observations.get(alternative.get("gridTargetId"))
            if grid is None or grid["foreignAltered"] or grid["size"]!=source["size"]:
                continue
            selected=_intersection(boundaries[endpoint_key],source["footprint"],grid["footprint"])
            original=_intersection(selected,grid["originalContribution"])
            if (selected.getbbox() and original.getbbox() and
                witness.get("footprint")==mask_record(selected,source["bundle"]["alpha"]) and
                witness.get("originalContribution")==mask_record(original,source["bundle"]["alpha"])):
                return True
        return False
    for candidate in deferred:
        result=candidate["result"]
        if result["complete"]:
            continue
        observation=observations[candidate["key"][0]]
        if observation["foreignAltered"]:
            result["reason"]="foreign_paint_altered"
            continue
        if observation["record"].get("reason")=="reference_observable_contribution_empty":
            result["reason"]="opaque_local_native_support_unproven"
            continue
        if not endpoint_terminated(candidate["baseline"]) or not endpoint_terminated(candidate["outer"]):
            result["reason"]="bound_stack_endpoint_support_unproven"
            continue
        result.update(complete=True,paintRetained=True,via="bound-stack-value-glyphs",reason=None,
            supportTargetId=candidate["alternative"]["neighborTargetId"],
            contributionRegionIds=[candidate["alternative"]["datumInteriorSupportId"],
                                   candidate["alternative"]["neighborInteriorSupportId"]],
            boundStackValueGlyphs={**candidate["alternative"],"targetId":candidate["key"][0],
                                  "supportId":candidate["key"][1]})
    for item in target_results:
        if not item["obligations"]:
            continue
        observation=observations.get(item["targetId"])
        item["complete"]=observation is not None and all(support["complete"] for support in item["obligations"])
        item["paintRetained"]=(item["complete"] and not observation["foreignAltered"] and
                              all(support["paintRetained"] for support in item["obligations"]))
    missing=[target_id for target_id in datum_targets if target_id not in by_id]
    for target_id in missing:
        target_results.append({"targetId":target_id,"complete":False,"paintRetained":False,
            "reason":"native_target_measurement_missing","obligations":[]})
    for target_result in target_results:
        target_id = target_result["targetId"]
        polar_pairs = [row for row in pair_receipts if row["request"]["kind"] == "proved-polar-adjacency"
                       and row["request"]["targetId"] == target_id]
        if not polar_pairs:
            continue
        raw = masks[target_id]
        for obligation_result in target_result["obligations"]:
            key = (target_id, obligation_result["supportId"])
            selected = _intersection(boundaries[key], raw["footprint"])
            identifiers = []
            for pair in polar_pairs:
                if _intersection(selected, pair_coverage[pair["request"]["id"]]).getbbox():
                    identifiers.append(pair["request"]["id"])
            if identifiers and obligation_result["complete"]:
                obligation_result.update(via="polar-adjacency-joint-original-contribution/v1",
                                          nativeCompositionPairIds=identifiers)
    area_receipts = _resolve_area_layers(chart, records, masks, target_results)
    complete=bool(target_results) and all(item["complete"] for item in target_results)
    graph = {"complete":complete,"paintRetained":complete and all(item["paintRetained"] for item in target_results),
        "targets":target_results,"equivalenceClasses":list(class_results.values()),
        "unproven":[{"targetId":item["targetId"],"reason":item["reason"] or "visibility_obligation_unproven"}
                    for item in target_results if not item["complete"]]}
    if area_receipts or pair_receipts:
        graph["nativeComposition"] = {"schema": "native-composition-pixel-partition/v1",
                                      "areaTargets": area_receipts, "jointPairs": pair_receipts}
    return graph
