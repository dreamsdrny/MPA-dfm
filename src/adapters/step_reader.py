"""Bounded STEP inventory, never a point-cloud approximation of assembly solids."""
import re
from pathlib import Path

from src.infrastructure.resources import read_bounded


def parse_step(path):
    text=read_bounded(path,64*1024*1024).decode("ascii",errors="strict")
    if not text.lstrip().upper().startswith("ISO-10303-21;"):
        raise ValueError("不是STEP ISO-10303-21文本文件")
    if not re.search(r"END-ISO-10303-21\s*;",text,re.I):
        raise ValueError("STEP文件未完整结束，不能把截断文件当成功")
    # Ignore comments and quoted content before counting records; do not treat
    # CARTESIAN_POINT (possibly a direction/control point) as a physical body.
    clean=re.sub(r"/\*.*?\*/","",text,flags=re.S)
    schema=re.search(r"FILE_SCHEMA\s*\(\s*\((.*?)\)\s*\)",clean,re.I|re.S)
    records=re.findall(r"(?m)^\s*#(\d+)\s*=\s*(.*?);",clean,re.S)
    if len(records)>500000 or len({i for i,_ in records})!=len(records):
        raise ValueError("STEP实体数量超限或编号重复")
    entities={};length_units={};context_refs=[];products=[]
    for identity,body in records:
        names=re.findall(r"\b([A-Z_][A-Z_0-9]*)\s*\(",re.sub(r"'(?:''|[^'])*'","''",body.upper()))
        for name in names:entities[name]=entities.get(name,0)+1
        if "LENGTH_UNIT" in names and "SI_UNIT" in names:
            match=re.search(r"SI_UNIT\s*\(\s*(\$|\.[A-Z]+\.)\s*,\s*\.METRE\.\s*\)",body,re.I)
            factors={"$":1000.0,".MILLI.":1.0,".CENTI.":10.0,".MICRO.":.001}
            length_units[identity]=factors.get(match[1].upper()) if match else None
        elif "LENGTH_UNIT" in names:
            length_units[identity]=None  # Conversion-based units need a CAD unit graph, not the word INCH.
        if "GLOBAL_UNIT_ASSIGNED_CONTEXT" in names:
            match=re.search(r"GLOBAL_UNIT_ASSIGNED_CONTEXT\s*\(\s*\((.*?)\)\s*\)",body,re.I|re.S)
            if match:context_refs.extend(re.findall(r"#(\d+)",match[1]))
        if "PRODUCT" in names:
            match=re.search(r"\bPRODUCT\s*\(\s*'((?:''|[^'])*)'",body,re.I)
            if match:products.append(match[1].replace("''","'"))
    if not records:raise ValueError("STEP没有可读取实体记录")
    refs=[r for r in context_refs if r in length_units]
    factors={length_units[r] for r in refs}
    factor=next(iter(factors)) if len(factors)==1 and None not in factors else None
    warnings=["未配置三维CAD内核：未解算实体、装配变换/镜像，不能用点集合/文件名猜本体或高度"]
    if factor is None:warnings.append("STEP长度上下文缺失/冲突/换算单位未支持，不能用于机械几何")
    return dict(status="partial",schema=re.findall(r"'([^']+)'",schema[1]) if schema else [],entity_counts=entities,
                products=products[:1000],unit_to_mm=factor,unit_context_count=len(refs),mechanical_bodies=None,
                height_mm=None,assembly_space=None,source=str(Path(path).resolve()),warnings=warnings)
