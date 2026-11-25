"""Application-owned engine dispatch; orchestration never lives in Adapters."""


def analyze_pcb(request, progress=None):
    from src.application.pcb_workflow import run_pcb_analysis
    return run_pcb_analysis(request, progress=progress)


def analyze_smt(doc, key, facts, **options):
    from src.application.smt_workflow import analyze_smt_workflow
    return analyze_smt_workflow(doc, key, facts, **options)


def analyze_checklist(request, log=None):
    from dataclasses import asdict

    from src.application.checklist_workflow import run
    return run(**asdict(request), log=log)


def analyze_brd(request, log=None):
    from dataclasses import asdict

    from src.application.brd_runner import run_all
    return run_all(**asdict(request), log=log)


def load_manufacturing(request):
    from dataclasses import asdict

    from src.application.coordination_workflow import load_manufacturing_project
    return load_manufacturing_project(**asdict(request))


def analyze_manufacturing_objects(project):
    from src.odb_dfm import analyze_odb
    return analyze_odb(project["sources"][0]["path"], step=project["layout"]["selected_step"])
