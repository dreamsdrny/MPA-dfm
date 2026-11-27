"""Direct, read-only PCB Editor trace search; no catalogue/plugin handoff UI."""
import math
import os

from src.adapters.allegro_bridge import AllegroSession
from src.adapters.allegro_skill import _short_path

TRACE_FORM = '''FILE_TYPE=FORM_DEFN VERSION=2
FORM
FIXED
PORT 72 13
HEADER "MPA-DFM Trace Search"
TILE
TEXT "Temporary highlights. Source BRD is read-only."
TLOC 1 1
ENDTEXT
FIELD status
FLOC 1 4
STRFILLIN 68 255
INFO_ONLY
ENDFIELD
FIELD clear
FLOC 1 8
MENUBUTTON "Clear highlights" 25 3
ENDFIELD
FIELD close
FLOC 40 8
MENUBUTTON "Close" 25 3
ENDFIELD
ENDTILE
ENDFORM
'''


TRACE_SKILL = r'''; Read actual cline segments in the current licensed PCB Editor.
; All dbids are local to this interactive command, not Python/timer state.
procedure(mpa_trace_reply(state count)
  let((fp)
    fp = outfile(mpa_trace_ack "w")
    when(fp fprintf(fp "0 %s %d\n" state count) close(fp))))

procedure(mpa_trace_action(form)
  case(form->curField
    ("clear" axlFormClose(form))
    ("close" axlFormClose(form)))
  t)

procedure(mpa_trace_scan(layer width spacing)
  prog((d segments seen objects remaining gap result minimum bb ob dx dy bound unit fp se)
    d = axlDBGetDesign()
    unless(d error("No active design.\n"))
    unless(member(layer axlGetParam("paramLayerGroup:ETCH")->groupMembers)
      error("Requested copper layer does not exist in the active BRD.\n"))
    unit = car(axlDBGetDesignUnits())
    seen = makeTable('mpa_trace_seen nil)
    foreach(net d->nets
      foreach(branch net->branches
        foreach(path branch->children
          when(path->objType == "path"
            foreach(seg path->segments
              when(seg->isEtch && seg->layer == strcat("ETCH/" layer) && !seen[seg]
                unless(member(seg->objType list("line" "arc")) error("Unsupported trace geometry.\n"))
                seen[seg] = t segments = cons(seg segments)))))))
    when(length(segments) > 100000 error("Too many segments on this layer.\n"))
    remaining = 2000000
    foreach(seg segments
      unless(numberp(seg->width) && seg->width > 0 error("Missing actual trace width.\n"))
      when(null(width) || abs(axlMKSConvert(seg->width unit "mils")-width) <= 0.01
        minimum = nil
        when(spacing
          bb = seg->bBox
          foreach(other segments
            unless(other == seg || other->net == seg->net
              remaining = remaining - 1
              when(remaining < 0 error("Spacing search budget exceeded. No success reported.\n"))
              ob = other->bBox
              dx = max(0.0 caar(bb)-caadr(ob) caar(ob)-caadr(bb))
              dy = max(0.0 cadar(bb)-cadadr(ob) cadar(ob)-cadadr(bb))
              bound = sqrt(dx*dx+dy*dy)
              when(null(minimum) || bound <= minimum
                result = axlAirGap(seg other seg->layer)
                unless(listp(result) && length(result) == 3 && numberp(caddr(result))
                  error("Actual spacing measurement failed.\n"))
                gap = max(0.0 caddr(result))
                when(null(minimum) || gap < minimum minimum = gap)))))
        when(spacing && null(minimum) error("No other-net trace on this layer; spacing cannot be measured.\n"))
        when(null(spacing) || (minimum != nil && abs(axlMKSConvert(minimum unit "mils")-spacing) <= 0.01)
          objects = cons(seg objects))))
    when(length(objects) > 30000 error("Too many highlight objects. Specify width/spacing.\n"))
    return(objects)))

procedure(mpa_trace_open()
  prog((objects marked form visible answer cleanup)
    mpa_trace_reply("SEARCHING" 0)
    answer = errset(progn(
      objects = mpa_trace_scan(mpa_trace_layer mpa_trace_width mpa_trace_spacing)
      form = axlFormCreate(gensym() mpa_trace_form nil 'mpa_trace_action nil t)
      unless(form error("Cannot create native trace result form.\n"))
      visible = axlVisibleGet()
      axlVisibleLayer(strcat("ETCH/" mpa_trace_layer) t)
      axlVisibleUpdate(t)
      foreach(obj objects
        unless(axlHighlightObject(list(obj) nil) error("Temporary highlight failed.\n"))
        marked = cons(obj marked))
      axlFormSetField(form "status" sprintf(nil "%d matching segments / ETCH/%s" length(objects) mpa_trace_layer))
      axlUIWUpdate(nil)
      mpa_trace_reply(if(objects then "HIGHLIGHTED" else "NO_MATCH") length(objects))
      ; Keep interactive command active while temporary highlighting is displayed.
      axlFormDisplay(form)
      t) t)
    cleanup = errset(progn(
      unless(answer when(form axlFormClose(form)))
      when(marked axlDehighlightObject(marked nil))
      when(visible axlVisibleSet(visible) axlVisibleUpdate(t))
      t) t)
    if(answer && cleanup then mpa_trace_reply("CLEARED" length(objects)) else mpa_trace_reply("FAILED" 0))
    return(answer && cleanup)))

axlCmdRegister("MPA_TRACE_SEARCH" 'mpa_trace_open ?cmdType "interactive")
'''


def trace_number(text, label):
    text = str(text).strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"{label}必须为正数，或留空表示不限") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label}必须为有限正数")
    return value


class AllegroTraceSession(AllegroSession):
    def prepare_trace(self, layer, width="", spacing=""):
        from src.adapters.impedance_plugin import quote_skill
        if not layer or any(ord(ch) < 32 for ch in layer):
            raise ValueError("请先选择当前 BRD 的铜层")
        width = trace_number(width, "线宽")
        spacing = trace_number(spacing, "线距")
        il = self.folder / "mpa_trace.il"
        form = self.folder / "mpa_trace.form"
        il.write_text(TRACE_SKILL, encoding="ascii")
        form.write_text(TRACE_FORM, encoding="ascii")
        def path(value):
            return quote_skill(_short_path(str(value).replace("\\", "/")))
        lines = [f"skill load({path(il)})", f"skill mpa_trace_ack = {path(self.ack)}",
                 f"skill mpa_trace_form = {path(form)}", f"skill mpa_trace_layer = {quote_skill(layer)}",
                 f"skill mpa_trace_width = {'nil' if width is None else format(width, '.12g')}",
                 f"skill mpa_trace_spacing = {'nil' if spacing is None else format(spacing, '.12g')}",
                 "MPA_TRACE_SEARCH"]
        self.script.write_text("\n".join(lines) + "\n", encoding="mbcs" if os.name == "nt" else "utf-8")
        return self.script

    def match_count(self):
        try:
            return int(self.ack.read_text(encoding="ascii").split()[2])
        except (OSError, ValueError, IndexError):
            return 0
