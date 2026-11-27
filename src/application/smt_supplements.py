"""BRD remains authoritative. Verify supplementary identity before using geometry."""
import json
import math
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

from shapely import from_wkb
from shapely.geometry import Polygon

from src.adapters.odb_reader import parse_odb
from src.adapters.step_reader import parse_step
from src.infrastructure.resources import read_bounded
from src.infrastructure.snapshots import capture_sources,verify_sources

CHECKS={
    "odb_brd_identity":("BRD/ODB++位号与面别核对","原生BRD位号、明确单板step、CMP/面别/料号/DNP"),
    "odb_brd_geometry":("BRD/ODB++引脚与焊盘核对","同版同原点确认、精确位号/脚号、实际铜盘；不按最近位置猜"),
    "step_model_association":("STP/BRD机械模型绑定","STP单位/实体结构与有来源确认模型导出；不是点云外接盒"),
}


def inspect_supplements(design,data=None,*,odb_path="",odb_step="",odb_confirmed=False,step_path="",model_path=""):
    if not isinstance(odb_confirmed,bool):raise ValueError("ODB++原点确认必须为布尔值")
    if model_path and not step_path:raise ValueError("机械模型映射需要同时选择对应STP")
    if (odb_path or step_path) and not design.get("_input_brd"):raise ValueError("当前SMT协同核对需要BRD主资料")
    paths=[p for p in (design.get("_input_brd"),odb_path,step_path,model_path) if p]
    sources=capture_sources(paths)
    native=design.get("_native_payload") or {}
    if native and native.get("source_brd_sha256")!=sources[0]["sha256"]:raise ValueError("BRD原生核对清单来自不同版本")
    prepared=deepcopy(data) if data is not None else None
    summary={};findings=[];coverage=[];films={}
    def finding(key,reason,status="review",**attrs):
        findings.append(dict(id="ASM-"+key,field=key,rule=key,label=CHECKS[key][0],status=status,reason=reason,
                             severity="high" if status=="fail" else "medium",unit="mm",source=odb_path if key.startswith("odb") else step_path,
                             advice="保留BRD原始值；修正版本/原点/模型映射后重查",**attrs))
    def state(key,checked,missing):
        coverage.append(dict(check_key=key,label=CHECKS[key][0],phase="partial" if checked and missing else "checked" if checked else "unavailable",
                             verdict="fail" if any(f["field"]==key and f["status"]=="fail" for f in findings) else "review" if missing or not checked or any(f["field"]==key for f in findings) else "within_scope",
                             checked=checked,missing=missing,source=odb_path if key.startswith("odb") else step_path,required_data=CHECKS[key][1],full_board_complete=False))
    brd_rows=native.get("components") or (data or {}).get("components") or []
    by_ref={c["refdes"].upper():c for c in brd_rows if c.get("refdes")}
    brd_duplicate=len(by_ref)!=len(brd_rows)
    association_conflict=False
    for c in (data or {}).get("components",[]):
        ref=c.get("refdes","").upper()
        if ref in by_ref:
            original=by_ref[ref]
            if native and any(c.get(k) is not None and original.get(k) is not None and c[k]!=original[k] for k in ("side","x_mm","y_mm","rotation")):
                association_conflict=True
                finding("odb_brd_geometry","对象资料与原生BRD放置身份冲突；不使用补充资料覆盖主设计",refdes=ref)
            by_ref[ref]=dict(original,**{k:v for k,v in c.items() if k not in ("side","x_mm","y_mm","rotation")})
    if odb_path:
        odb=parse_odb(odb_path,step=odb_step or None,object_checks_only=False)
        summary["odb"]=dict(path=odb_path,selected_step=odb.get("selected_step"),steps=odb.get("steps"),warnings=odb["warnings"],
                            components=len(odb["component_records"]),copper_layers=odb.get("copper_layer_count"),films_used=[],alignment_confirmed=odb_confirmed)
        grouped=defaultdict(list)
        for c in odb["component_records"]:grouped[c["refdes"].upper()].append(c)
        checked=0;missing=int(not odb.get("component_complete") or not by_ref or brd_duplicate);conflict=brd_duplicate or association_conflict
        metadata_missing=0
        for ref in sorted(set(by_ref)|set(grouped)):
            cad=by_ref.get(ref);rows=grouped[ref]
            if cad is None or len(rows)!=1:
                conflict=True;missing+=1
                finding("odb_brd_identity","ODB++位号未在BRD/重复/未取得；不能自动增删器件",refdes=ref)
                continue
            other=rows[0];checked+=1
            if cad.get("side") not in ("TOP","BOTTOM") or other.get("side") not in ("TOP","BOTTOM"):
                missing+=1;conflict=True;continue
            if cad["side"]!=other["side"]:
                conflict=True;finding("odb_brd_identity","同位号BRD/ODB++面别冲突","fail",refdes=ref,side=cad["side"])
            if cad.get("footprint") and other.get("package") and cad["footprint"].strip().upper()!=other["package"].strip().upper():
                metadata_missing+=1
                finding("odb_brd_identity","BRD/ODB++封装名称不同；可能为命名别名，需核对，不能据名称单独判实体尺寸错误",refdes=ref,side=cad["side"])
            if cad.get("part_number") and other.get("part_number") and cad["part_number"].upper()!=other["part_number"].upper():
                conflict=True;finding("odb_brd_identity","BRD/ODB++同位号料号冲突","fail",refdes=ref,side=cad["side"])
            elif not cad.get("part_number") or not other.get("part_number"):metadata_missing+=1
            # File-global no_pop attribute availability does not establish a
            # populated value on each CMP, so compare only explicit per-record DNP.
            if isinstance(cad.get("dnp"),bool) and other.get("dnp_explicit") is True and cad["dnp"]!=other["dnp"]:
                conflict=True;finding("odb_brd_identity","BRD/BOM与ODB++明确DNP冲突","fail",refdes=ref)
            elif not isinstance(cad.get("dnp"),bool) or other.get("dnp_explicit") is not True:metadata_missing+=1
        summary["odb"]["identity_metadata_missing"]=metadata_missing
        state("odb_brd_identity",checked,missing+metadata_missing)
        geometry_checked=0;geometry_missing=0
        pin_map=defaultdict(list)
        for pin in (data or {}).get("native_pins",native.get("objects",{}).get("pins",[])):
            pin_map[(pin["refdes"].upper(),str(pin["pin"]))].append(pin)
        expected_by_ref=defaultdict(set)
        for owner,pin in pin_map:expected_by_ref[owner].add(pin)
        for ref,rows in grouped.items():
            if ref not in by_ref or len(rows)!=1:continue
            cad=by_ref[ref];other=rows[0]
            if not odb_confirmed:geometry_missing+=1;continue
            geometry_checked+=1
            if any(cad.get(k) is None for k in ("x_mm","y_mm")):
                geometry_missing+=1;conflict=True;continue
            if any(isinstance(cad[k],bool) or not math.isfinite(float(cad[k])) for k in ("x_mm","y_mm")):
                raise ValueError("BRD坐标必须为有限毫米数")
            distance=math.hypot(cad["x_mm"]-other["x_mm"],cad["y_mm"]-other["y_mm"])
            if distance>.01:
                conflict=True;finding("odb_brd_geometry","同位号放置原点差大于0.01mm；未执行自动平移/旋转","fail",actual=round(distance,6),requirement=.01,refdes=ref,side=cad["side"],x_mm=cad["x_mm"],y_mm=cad["y_mm"])
            for pin in other.get("pins",[]):
                matches=pin_map[(ref,str(pin["pin"]))]
                if len(matches)!=1:geometry_missing+=1;conflict=True;continue
                bp=matches[0];geometry_checked+=1
                distance=math.hypot(pin["x_mm"]-bp["x_mm"],pin["y_mm"]-bp["y_mm"])
                if distance>.01:
                    conflict=True;finding("odb_brd_geometry","同位号脚号原点不符；旋转/镜像/版本需核对","fail",refdes=ref,pin=pin["pin"],actual=round(distance,6),requirement=.01,side=cad["side"],x_mm=bp["x_mm"],y_mm=bp["y_mm"])
                if bp.get("net") and pin.get("net") and bp["net"]!=pin["net"]:
                    conflict=True;finding("odb_brd_geometry","同位号脚号BRD/ODB++网络名不符（不当全网短路证明）",refdes=ref,pin=pin["pin"])
            if not other.get("pins"):geometry_missing+=1;conflict=True
            expected_pins=expected_by_ref[ref]
            odb_pins=[str(p["pin"]) for p in other.get("pins",[])]
            if set(odb_pins)!=expected_pins or len(odb_pins)!=len(set(odb_pins)):
                geometry_missing+=1;conflict=True
                finding("odb_brd_geometry","同位号引脚清单缺失/重复/额外；不将一致子集当完整对应",refdes=ref)
        if not odb_confirmed:finding("odb_brd_geometry","ODB++版本/原点/旋转/镜像未确认，仅做身份核对，不用于BRD空间判定")
        layer_names=(data or {}).get("layer_names") or native.get("updates",{}).get("layers",[])
        if layer_names and len(layer_names)!=odb.get("copper_layer_count"):
            conflict=True;finding("odb_brd_geometry","BRD/ODB++铜层数不一致","fail")
        profile=(data or {}).get("board_outline") or native.get("objects",{}).get("board_outline")
        if odb_confirmed and profile and odb.get("profile"):
            a=Polygon(profile);b=from_wkb(odb["profile"]["geometry_wkb"])
            geometry_checked+=1
            if odb["profile"].get("status")!="parsed":geometry_missing+=1;conflict=True
            elif a.hausdorff_distance(b)>.01:
                conflict=True;finding("odb_brd_geometry","BRD/ODB++板框不一致；单板/拼板或切口需核对","fail")
        else:geometry_missing+=1
        # Match actual FID pads by exact identity, not proximity.
        cad_pads=defaultdict(list)
        for p in (data or {}).get("pads",native.get("objects",{}).get("pads",[])):
            if p.get("object_kind")!="via":cad_pads[(str(p.get("refdes","")).upper(),str(p.get("pin","")),p.get("side"))].append(p)
        fid_checked=0;role_groups=defaultdict(list);matched_pad_keys=set()
        for film in odb["films"]:
            role=film["role"];side=film.get("side")
            if role=="copper":
                side="TOP" if film.get("copper_index")==1 else "BOTTOM" if film.get("copper_index")==odb.get("copper_layer_count") else ""
                if odb_confirmed and side:
                    for feature in film.get("records",[]):
                        if not feature.get("refdes") or not feature.get("geometry_wkb"):continue
                        matches=cad_pads[(feature["refdes"].upper(),str(feature.get("pin","")),side)]
                        if len(matches)!=1:geometry_missing+=1;conflict=True;continue
                        a=Polygon(matches[0]["outline"]);b=from_wkb(feature["geometry_wkb"])
                        fid_checked+=1;geometry_checked+=1
                        matched_pad_keys.add((feature["refdes"].upper(),str(feature.get("pin","")),side))
                        if a.hausdorff_distance(b)>.01:
                            conflict=True;finding("odb_brd_geometry","精确FID脚铜盘与BRD铜盘轮廓不一致","fail",refdes=feature["refdes"],pin=feature.get("pin"),side=side,x_mm=a.centroid.x,y_mm=a.centroid.y)
            if role in ("copper","mask","paste","silkscreen") and side in ("TOP","BOTTOM"):
                role_groups[role+"_"+side.lower()].append(film)
        expected_pad_keys={key for key in cad_pads if key[2] in ("TOP","BOTTOM") and key[0] in by_ref}
        if odb_confirmed and expected_pad_keys-matched_pad_keys:
            geometry_missing+=len(expected_pad_keys-matched_pad_keys);conflict=True
            finding("odb_brd_geometry","ODB++精确FID铜盘未覆盖BRD外层铜盘清单，制造层未用于空间判定")
        if not fid_checked:geometry_missing+=1
        if odb_confirmed and not layer_names:
            geometry_missing+=1;conflict=True
            finding("odb_brd_geometry","BRD铜层顺序未取得，不能用ODB层名猜对应外层")
        valid=odb_confirmed and not conflict and not missing and fid_checked>0 and odb.get("component_complete") is True
        if valid:
            for role,rows in role_groups.items():
                if len(rows)!=1 or rows[0].get("warnings") or rows[0].get("status")!="parsed":
                    geometry_missing+=1;continue
                film=rows[0];films[role]=dict(role=role,geometry=from_wkb(film["geometry_wkb"]),path=film["source"],brd_alignment=True,objects=[])
            for role in ("copper_top","copper_bottom","mask_top","mask_bottom","paste_top","paste_bottom"):
                if role not in films:geometry_missing+=1
            summary["odb"]["films_used"]=list(films)
        else:geometry_missing+=1
        summary["odb"].update(fid_pads_compared=fid_checked,spatial_gate_passed=valid,
                             brd_native_status=native.get("status","手工哈希绑定对象清单"))
        state("odb_brd_geometry",geometry_checked,geometry_missing)
    if step_path:
        step=parse_step(step_path);summary["step"]=step
        checked=0;missing=1
        finding("step_model_association","STP已只读识别；未解析CAD实体/装配变换，文件导入不证明高度/碰撞已检测")
        if model_path:
            models=json.loads(read_bounded(model_path,8*1024*1024).decode("utf-8-sig"))
            if not isinstance(models,dict):raise ValueError("机械模型导出需要JSON对象")
            digests={s["path"]:s["sha256"] for s in sources}
            if models.get("schema_version")!=1 or models.get("unit")!="mm" or models.get("coordinate_system")!="brd" or models.get("alignment_confirmed") is not True:
                raise ValueError("机械模型导出必须明确mm、BRD原点和对齐确认")
            if models.get("brd_sha256")!=digests[str(Path(design["_input_brd"]).resolve())] or models.get("step_sha256")!=digests[str(Path(step_path).resolve())]:
                raise ValueError("机械模型导出未绑定当前BRD/STP哈希")
            if step["unit_to_mm"] is None:raise ValueError("STP长度单位未确认，不能采用模型导出")
            if prepared is None:raise ValueError("机械模型补充需要BRD对象资料，先启用自动关联")
            entries=models.get("components",[])
            if not isinstance(entries,list) or len(entries)>10000:raise ValueError("机械模型记录超限")
            target={c["refdes"].upper():c for c in prepared["components"]};seen=set()
            for row in entries:
                if not isinstance(row,dict):raise ValueError("模型记录必须为JSON对象")
                ref=str(row.get("refdes","")).upper()
                if ref in seen or ref not in target:raise ValueError("机械模型位号重复或不在BRD")
                seen.add(ref);c=target[ref]
                if row.get("confirmed") is not True or not row.get("source") or row.get("side")!=c.get("side") or not row.get("outline"):
                    raise ValueError("模型补充缺来源/实体投影/面别确认")
                if not row.get("step_product") or row["step_product"] not in step["products"]:
                    raise ValueError("模型补充需要明确关联STP中的PRODUCT标识，不以文件名猜位号")
                for field in ("part_number","manufacturer_part_number"):
                    if row.get(field) and c.get(field) and str(row[field]).strip().upper()!=str(c[field]).strip().upper():
                        raise ValueError("STP模型与当前位号料号/MPN不符")
                shape=Polygon(row["outline"])
                if any(len(p)!=2 or any(isinstance(v,bool) or not math.isfinite(float(v)) for v in p) for p in row["outline"]):raise ValueError("模型投影坐标必须为有限毫米数")
                if not shape.is_valid or shape.is_empty or shape.area<=0:raise ValueError("模型实体投影无效")
                height=row.get("height_mm")
                if isinstance(height,bool) or height is None or not math.isfinite(float(height)) or float(height)<=0:raise ValueError("模型高度无效")
                # A mapped STEP body cannot establish physical lead/toe/polarity.
                if c.get("boundary")=="body" and c.get("outline") and Polygon(c["outline"]).hausdorff_distance(shape)>.01:
                    finding("step_model_association","确认STP投影与已有确认本体冲突；保留原本体，不覆盖","fail",refdes=ref,side=c["side"])
                    continue
                if c.get("height_mm") is not None and c.get("height_source") and not c["height_source"].startswith("BRD") and abs(c["height_mm"]-float(height))>.01:
                    finding("step_model_association","模型高度与已有确认BOM高度冲突；保留原值","fail",refdes=ref,side=c["side"])
                    continue
                c.update(outline=row["outline"],boundary="body",body_source=row["source"],height_mm=float(height),height_source=row["source"],
                         model_body_confirmed=True,model_step_source=step_path)
                checked+=1
            missing=max(0,len(target)-len(seen))+1  # External confirmed export, not in-program CAD kernel certification.
            summary["step"]["mapped_components"]=checked
        state("step_model_association",checked,missing)
    verify_sources(sources)
    return dict(data=prepared,films=films,summary=summary,findings=findings,coverage=coverage,sources=sources)
