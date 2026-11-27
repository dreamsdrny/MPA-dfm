from unittest.mock import patch

import pytest
import tkinter as tk


def _root():
    """本机 Tcl/Tk 资源偶发缺文件时跳过，不把环境问题当产品缺陷。"""
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tcl/Tk 初始化资源不可用：{exc}")
    root.withdraw()
    return root


def test_supplement_controls_invalidate_and_clear_on_board_change():
    from src.ui.smt_panel import SMTPanel
    root = _root()
    try:
        class Tasks:
            pending={}
        panel=SMTPanel(root,lambda:dict(_input_brd="board.brd"),Tasks(),lambda *args:None)
        panel.last_result=dict(items=[])
        panel.odb_path.set("job.zip")
        assert panel.last_result is None and "job.zip" in panel.supplement_paths.get()
        panel.odb_confirmed.set(True)
        with patch("src.ui.smt_panel.filedialog.askopenfilename",return_value="other.zip"):panel._browse_odb()
        assert not panel.odb_confirmed.get()
        panel.step_path.set("body.stp");panel.model_path.set("mapping.json")
        panel.invalidate(clear_inputs=True)
        assert not panel.odb_path.get() and not panel.step_path.get() and not panel.model_path.get()
    finally:root.destroy()


def test_supplier_choice_is_explicit_and_profiles_show_business_names():
    from src.ui.smt_panel import SMTPanel
    root = _root()
    try:
        class Tasks: pending={}
        panel=SMTPanel(root,lambda:dict(_input_brd="board.brd"),Tasks(),lambda *args:None)
        # 界面文字被截断/乱码时仍按厂商标识匹配，不能悄悄退回另一家能力。
        panel.supplier.set("??? / TXD");panel.load_builtin()
        assert "TXD" in panel.doc["meta"]["factory"]
        labels=list(panel.combo.cget("values"))
        assert panel.key.get()!="automotive" and panel.combo_label.get()==labels[0]
        assert "SMT" in labels[0] and "COB" in labels[1]
        panel.combo_label.set(labels[1]);panel._select_profile()
        assert panel.key.get()=="smt_cob" and len(panel.review_tree.get_children())==54
        panel.supplier.set("未登记厂商");before=panel.doc;panel.load_builtin()
        assert panel.doc is before and "未识别" in panel.summary.get()
        panel.supplier.set("外部配置");assert panel._builtin_profile() is None
    finally:root.destroy()
