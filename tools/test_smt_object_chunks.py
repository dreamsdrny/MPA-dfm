import json

import pytest

from src.adapters.smt_evidence import load_evidence,evidence_fingerprints,verify_evidence_fingerprints,sha256_file
from src.application.smt_workflow import evidence_template


def make(tmp_path):
    board=tmp_path/"board.brd";board.write_bytes(b"fixture")
    child=tmp_path/"pads.json";child.write_text(json.dumps([dict(refdes="U1",pin="1")]))
    d=evidence_template(board);d["coordinate_alignment_confirmed"]=True
    d["object_files"]=[dict(field="pads",path=child.name,sha256=sha256_file(child))]
    path=tmp_path/"objects.json";path.write_text(json.dumps(d))
    return board,path,child,d


def test_chunks_are_hash_bound_and_part_of_report_snapshot(tmp_path):
    b,p,c,d=make(tmp_path)
    assert load_evidence(p,b)["pads"]==[dict(refdes="U1",pin="1")]
    snapshot=evidence_fingerprints(p,b)
    c.write_text("[]")
    with pytest.raises(ValueError,match="哈希"):load_evidence(p,b)
    with pytest.raises(ValueError,match="已变化"):verify_evidence_fingerprints(snapshot)


def test_chunks_cannot_escape_directory_or_duplicate_inline_rows(tmp_path):
    b,p,c,d=make(tmp_path)
    d["object_files"][0]["path"]="../outside.json";p.write_text(json.dumps(d))
    with pytest.raises(ValueError,match="同目录"):load_evidence(p,b)
    d["object_files"][0]["path"]=c.name;d["pads"]=[dict(refdes="U2")];p.write_text(json.dumps(d))
    with pytest.raises(ValueError,match="同时内嵌和分块"):load_evidence(p,b)
