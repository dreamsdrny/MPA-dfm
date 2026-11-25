; Native PCB Editor command. Read current DB, never save or modify copper/rules.
imp_dir = nil
imp_token = nil
imp_form = nil
imp_marked = nil
imp_old_color = nil
imp_design = nil
imp_selected_layer = nil
imp_visible = nil
imp_budget = 2000000
imp_max_segments = 100000
imp_results = nil
imp_net_rules = nil
imp_guard_board = nil
imp_guard_token = nil
imp_defaults = nil
imp_scope_cache = nil

procedure(imp_text(v)
  cond((stringp(v) v) (null(v) "") (t sprintf(nil "%L" v))))

procedure(imp_csv(fp v)
  let((s ch)
    s = imp_text(v)
    fprintf(fp "\"")
    for(i 1 strlen(s)
      ch = substring(s i 1)
      if(ch == "\"" then fprintf(fp "\"\"") else fprintf(fp "%s" ch)))
    fprintf(fp "\"")))

procedure(imp_line(fp type name value detail)
  imp_csv(fp type) fprintf(fp ",") imp_csv(fp name) fprintf(fp ",")
  imp_csv(fp value) fprintf(fp ",") imp_csv(fp detail) fprintf(fp "\n"))

procedure(imp_finish_export(name signature count)
  prog((fp ch sum n code)
    fp = infile(strcat(imp_dir "/" name ".csv"))
    unless(fp error("Cannot verify native export.\n"))
    sum = 0 n = 0
    while(ch = getc(fp)
      code = charToInt(ch)
      sum = mod(sum*31 + code 65521)
      n = n + 1)
    close(fp)
    fp = outfile(strcat(imp_dir "/" name ".done") "w")
    unless(fp error("Cannot write native completion record.\n"))
    fprintf(fp "%s %s %d %d:%d\n" signature imp_token count sum n)
    close(fp)
    return(t)))

procedure(imp_number(text)
  let((value data)
    if(text == "" then nil else
      data = errset(linereadstring(text) nil)
      unless(data && length(car(data)) == 1 && numberp(caar(data)) && caar(data) >= 0
        error("Enter one non-negative number or leave blank.\n"))
      value = caar(data)
      value)))

procedure(imp_mil(value) axlMKSConvert(value car(axlDBGetDesignUnits()) "mils"))

procedure(imp_layers()
  let((layers parameter)
    parameter = axlGetParam("paramLayerGroup:ETCH")
    when(parameter layers = parameter->groupMembers)
    unless(layers layers = axlSubclassRoute())
    unless(layers error("No current copper layer names.\n"))
    layers))

procedure(imp_prop(obj name)
  let((props value)
    props = axlDBGetProperties(obj)
    foreach(p props
      when(upperCase(imp_text(car(p))) == name value = cadr(p)))
    value))

procedure(imp_flat_nets(obj)
  setof(item imp_members(obj) item->objType == "net"))

procedure(imp_members(obj)
  ; Flatten only documented logical group membership; cycles stop explicitly.
  let((items todo seen current)
    todo = list(obj) seen = makeTable('imp_member_seen nil)
    while(todo
      current = car(todo) todo = cdr(todo)
      unless(seen[current]
        seen[current] = t items = cons(current items)
        foreach(member current->groupMembers todo = cons(member todo))))
    items))

procedure(imp_pair_map(d)
  let((map legs left right info)
    map = makeTable('imp_pair_by_net nil)
    foreach(dp d->diffpair
      legs = dp->groupMembers
      unless(length(legs) == 2 error("Differential pair does not have two legs.\n"))
      left = imp_flat_nets(car(legs)) right = imp_flat_nets(cadr(legs))
      foreach(net left
        when(map[net->name] error("Net occurs in multiple differential pairs.\n"))
        info = list(nil 'name dp->name 'object dp 'mate right 'leg car(legs))
        map[net->name] = info)
      foreach(net right
        when(map[net->name] error("Net occurs in multiple differential pairs.\n"))
        map[net->name] = list(nil 'name dp->name 'object dp 'mate left 'leg cadr(legs))))
    map))

procedure(imp_impedance_keys()
  let((keys)
    foreach(key axlCNSEcsetValueGet(nil)
      when(rexMatchp("[Ii][Mm][Pp][Ee][Dd][Aa][Nn][Cc][Ee]" imp_text(key))
        keys = cons(key keys)))
    keys))

procedure(imp_target(raw)
  ; Only parse documented net-level ALL:ALL:target:tolerance syntax or scalar.
  ; Pin-pair clauses are retained as raw evidence, never guessed to all segments.
  prog((parts number data)
    when(numberp(raw) return(raw))
    when(stringp(raw)
      parts = parseString(raw ":")
      when(length(parts) == 4 && car(parts) == "ALL" && cadr(parts) == "ALL"
        data = errset(linereadstring(caddr(parts)) nil)
        when(data && length(car(data)) == 1 && numberp(caar(data)) number = caar(data)))
      when(length(parts) == 1
        data = errset(linereadstring(raw) nil)
        when(data && length(car(data)) == 1 && numberp(caar(data)) number = caar(data))))
    return(number)))

procedure(imp_rule(net layer pair keys)
  let((raw target targets evidence pset width gap temp owners seen ecset candidates widthSets gapSets assignment unresolved)
    owners = list(net)
    seen = makeTable('imp_owner_seen nil)
    seen[net] = t
    foreach(parent net->parentGroups
      unless(seen[parent] owners = cons(parent owners) seen[parent] = t))
    when(imp_scope_cache
      foreach(group imp_scope_cache[net]
        unless(seen[group] owners = cons(group owners) seen[group] = t)
        foreach(parent group->parentGroups
          unless(seen[parent] owners = cons(parent owners) seen[parent] = t))))
    when(pair owners = append(list(pair->object pair->leg) owners))
    ; Effective net electrical constraints and explicit flattening are both read.
    foreach(key keys
      raw = axlNetEcsetValueGet(net key)
      when(raw
        evidence = strcat(imp_text(evidence) imp_text(key) "=" imp_text(raw) ";")
        target = imp_target(raw)
        when(null(target) unresolved = t)
        when(target && target > 0 && !member(target targets) targets = cons(target targets)))
      raw = axlCnsNetFlattened(net key)
      when(raw
        evidence = strcat(imp_text(evidence) "flattened:" imp_text(key) "=" imp_text(raw) ";")
        target = imp_target(raw)
        when(null(target) unresolved = t)
        when(target && target > 0 && !member(target targets) targets = cons(target targets))))
    ; Explicit pair/Xnet/net properties include overrides at those levels.
    foreach(owner owners
      raw = imp_prop(owner "IMPEDANCE")
      when(raw
        evidence = strcat(imp_text(evidence) "IMPEDANCE=" imp_text(raw) ";")
        target = imp_target(raw)
        when(null(target) unresolved = t)
        when(target && target > 0 && !member(target targets) targets = cons(target targets)))
      ecset = imp_prop(owner "ELECTRICAL_CONSTRAINT_SET")
      when(ecset
        foreach(key keys
          raw = axlCNSEcsetValueGet(ecset key t)
          when(raw
            evidence = strcat(imp_text(evidence) "ECset:" imp_text(ecset) ":" imp_text(key) "=" imp_text(raw) ";")
            target = imp_target(raw)
            when(null(target) unresolved = t)
            when(target && target > 0 && !member(target targets) targets = cons(target targets)))))
      foreach(prop axlDBGetProperties(owner)
        when(rexMatchp("[Ii][Mm][Pp][Ee][Dd][Aa][Nn][Cc][Ee]" imp_text(car(prop)))
          raw = cadr(prop)
          evidence = strcat(imp_text(evidence) "property:" imp_text(car(prop)) "=" imp_text(raw) ";")
          target = imp_target(raw)
          when(null(target) unresolved = t)
          when(target && target > 0 && !member(target targets) targets = cons(target targets))))
      temp = imp_prop(owner "PHYSICAL_CONSTRAINT_SET")
      when(temp && !member(temp candidates) candidates = cons(temp candidates)))
    unless(candidates candidates = list("DEFAULT"))
    pset = sprintf(nil "%L" candidates)
    foreach(cset candidates
      width = axlCNSGetPhysical(cset layer 'width_min)
      gap = axlCNSGetPhysical(cset layer 'dp_primary_gap)
      when(numberp(width) && width > 0 && !member(width widthSets) widthSets = cons(width widthSets))
      when(numberp(gap) && gap > 0 && !member(gap gapSets) gapSets = cons(gap gapSets)))
    ; Explicit overrides win only when consistent among owners. Unresolved
    ; region/class-class inheritance is not claimed to be the effective rule.
    foreach(owner owners
      temp = imp_prop(owner "MIN_LINE_WIDTH")
      when(temp evidence = strcat(imp_text(evidence) "WidthOverride=" imp_text(temp) ";"))
      temp = imp_prop(owner "DP_PRIMARY_GAP")
      when(temp evidence = strcat(imp_text(evidence) "GapOverride=" imp_text(temp) ";")))
    width = if(length(widthSets) == 1 then car(widthSets) else nil)
    gap = if(length(gapSets) == 1 then car(gapSets) else nil)
    list(nil 'raw imp_text(evidence) 'targets targets 'physical pset 'width width 'gap gap
      'state cond((length(targets) > 1 "CONFLICT") (unresolved "UNRESOLVED") (targets "IMPEDANCE")
                  (evidence "UNRESOLVED") (t "NO_IMPEDANCE")))))

procedure(imp_setup(directory token)
  when(imp_form error("Close the existing IMP_HIGHLIGHT form before changing plugin directory.\n"))
  imp_clear()
  imp_dir = directory imp_token = token
  imp_results = nil imp_guard_board = nil imp_guard_token = nil imp_defaults = nil
  t)

procedure(imp_scopes(d)
  let((table)
    table = makeTable('imp_scope_by_net nil)
    foreach(group append(d->xnet d->netclass)
      foreach(net imp_flat_nets(group)
        table[net] = cons(group table[net])))
    table))

procedure(imp_refresh()
  prog((d layers fp count pairmap rule keys palettes rgb report answer)
    unless(imp_dir error("Run imp_setup(directory token) first.\n"))
    imp_invalidate_export("catalog")
    d = axlDBGetDesign()
    unless(d error("Open a BRD in licensed PCB Editor first.\n"))
    layers = imp_layers()
    unless(layers error("No routing layers returned by PCB Editor.\n"))
    when(length(layers)*length(d->nets) > 200000 error("Constraint catalogue budget exceeded.\n"))
    pairmap = imp_pair_map(d)
    imp_scope_cache = imp_scopes(d)
    answer = errset(imp_impedance_keys() nil)
    keys = if(answer then car(answer) else nil)
    imp_net_rules = makeTable('imp_rules nil)
    fp = outfile(strcat(imp_dir "/catalog.csv") "w")
    unless(fp error("Cannot write catalogue.\n"))
    fprintf(fp "TYPE,NAME,VALUE,DETAIL\n") count = 0
    imp_line(fp "BOARD" axlGetDrawingName() length(layers) car(axlDBGetDesignUnits())) count = count + 1
    foreach(layer layers
      imp_line(fp "LAYER" layer axlLayerGet(strcat("ETCH/" layer))->color "copper") count = count + 1)
    foreach(net d->nets
      imp_line(fp "NET" net->name "" if(pairmap[net->name] then pairmap[net->name]->name else "")) count = count + 1)
    foreach(dp d->diffpair
      imp_line(fp "PAIR" dp->name "" sprintf(nil "%L" foreach(mapcar leg dp->groupMembers
        foreach(mapcar net imp_flat_nets(leg) net->name)))) count = count + 1)
    for(index 1 axlColorGet('count)-1
      rgb = axlColorGet(index)
      imp_line(fp "COLOR" index sprintf(nil "%d,%d,%d" car(rgb) cadr(rgb) caddr(rgb)) "Allegro palette") count = count + 1)
    foreach(net d->nets
      foreach(layer layers
        answer = errset(imp_rule(net layer pairmap[net->name] keys) nil)
        rule = if(answer then car(answer) else
          list(nil 'state "READ_ERROR" 'raw "Constraint API unavailable; geometry mode only." 'physical "" 'width nil 'gap nil))
        imp_line(fp "RULE" net->name layer strcat(rule->state ";" rule->raw ";Physical=" imp_text(rule->physical)
          ";WidthUU=" imp_text(rule->width) ";DPGapUU=" imp_text(rule->gap))) count = count + 1))
    close(fp)
    imp_finish_export("catalog" "IMP_CATALOG" count)
    when(imp_form
      axlFormBuildPopup(imp_form "layer" layers)
      palettes = nil
      for(index 1 axlColorGet('count)-1
        palettes = cons(list(sprintf(nil "Color %d" index) index index) palettes))
      axlFormBuildPopup(imp_form "color" reverse(palettes))
      axlFormSetField(imp_form "layer" car(layers))
      axlFormSetField(imp_form "color" 1)
      axlFormSetField(imp_form "status" sprintf(nil "%d copper layers / %d nets / %d pairs" length(layers) length(d->nets) length(d->diffpair))))
    axlUIWPrint(nil "IMP_REFRESH: current database catalogue exported.")
    return(t)))

procedure(imp_segments(layer)
  prog((enabled buttons selected visible objects segments restore answer seen count)
    enabled = axlGetFindFilter() buttons = axlGetFindFilter(t) selected = axlGetSelSet() visible = axlVisibleGet()
    seen = makeTable('imp_seen nil)
    count = 0
    answer = errset(progn(
      axlVisibleDesign(nil)
      axlVisibleLayer(strcat("ETCH/" layer) t)
      axlClearSelSet()
      axlSetFindFilter(?enabled list("noall" "clinesegs") ?onButtons list("noall" "clinesegs"))
      axlAddSelectAll()
      objects = axlGetSelSet()
      foreach(seg objects
        when(seg->isEtch && seg->layer == strcat("ETCH/" layer) && !seen[seg]
          unless(member(seg->objType list("line" "arc")) error("Unsupported segment type.\n"))
          seen[seg] = t segments = cons(seg segments) count = count + 1
          when(count > imp_max_segments error("Layer segment budget exceeded.\n"))))
      t) t)
    restore = errset(progn(
      axlClearSelSet()
      axlSetFindFilter(?enabled cons("noall" enabled) ?onButtons cons("noall" buttons))
      when(selected axlAddSelectObject(selected))
      axlVisibleSet(visible) axlVisibleUpdate(t) t) nil)
    unless(answer && restore error("Layer enumeration/restoration failed.\n"))
    return(reverse(segments))))

procedure(imp_clear()
  prog((answer remaining)
    when((imp_marked || imp_old_color || imp_visible) && imp_design != axlGetDrawingName()
      error("Board changed; do not restore the previous board's display on this board.\n"))
    when(imp_marked
      unless(imp_design == axlGetDrawingName() error("Board changed; previous display cannot be cleared here.\n"))
      foreach(obj imp_marked
        answer = errset(progn(
          unless(axlDBRefreshId(obj) error("Stale highlighted object; use Display redraw.\n"))
          axlDehighlightObject(obj nil)
          t) nil)
        unless(answer remaining = cons(obj remaining)))
      imp_marked = remaining)
    when(imp_old_color
      axlDBControl('tempColor imp_old_color) imp_old_color = nil)
    when(imp_visible
      axlVisibleSet(imp_visible) axlVisibleUpdate(t) imp_visible = nil)
    when(imp_marked error("Some temporary highlights could not be cleared; redraw/re-scan.\n"))
    imp_results = nil
    return(t)))

procedure(imp_invalidate_export(name)
  let((fp)
    fp = outfile(strcat(imp_dir "/" name ".done") "w")
    unless(fp error("Cannot invalidate previous native export.\n"))
    fprintf(fp "INCOMPLETE\n") close(fp)))

procedure(imp_spacing(segment neighbours)
  prog((gap result minimum count bb otherBox dx dy bound)
    count = 0
    bb = segment->bBox
    foreach(other neighbours
      unless(other == segment || other->net == segment->net
        count = count + 1
        imp_remaining = imp_remaining - 1
        when(imp_remaining < 0 error("Spacing candidate budget exceeded; narrow scope.\n"))
        otherBox = other->bBox
        dx = max(0.0 caar(bb)-caadr(otherBox) caar(otherBox)-caadr(bb))
        dy = max(0.0 cadar(bb)-cadadr(otherBox) cadar(otherBox)-cadadr(bb))
        bound = sqrt(dx*dx+dy*dy)
        when(null(minimum) || bound <= minimum
          result = axlAirGap(segment other segment->layer)
          unless(listp(result) && length(result) == 3 && numberp(caddr(result))
            error("Allegro failed a spacing measurement; no highlight applied.\n"))
          gap = max(0.0 caddr(result))
          when(null(minimum) || gap < minimum minimum = gap))))
    return(minimum)))

procedure(imp_run_impl(layer widthText spacingText toleranceText mode netName pairName impedanceText color)
  prog((d layers segments pairs keys rules width spacing tolerance target objects results rule pair neighbours gap number eligible fp bb se state netIndex answer matchedNet)
    unless(imp_dir error("Run imp_setup first.\n"))
    when(imp_guard_board
      unless(imp_guard_board == axlGetDrawingName() && imp_guard_token == imp_token
        error("Plugin session or active board differs from the main GUI catalogue; refresh first.\n")))
    imp_invalidate_export("results")
    imp_clear()
    d = axlDBGetDesign()
    unless(d error("No active BRD.\n"))
    layers = imp_layers()
    unless(member(layer layers) error("Select an actual copper layer from IMP_REFRESH.\n"))
    unless(member(mode list("geometry" "impedance" "pair")) error("Unknown recognition mode.\n"))
    unless(integerp(color) && color > 0 && color < axlColorGet('count) error("Invalid Allegro palette index.\n"))
    width = imp_number(widthText) spacing = imp_number(spacingText) tolerance = imp_number(toleranceText)
    target = imp_number(impedanceText)
    unless(tolerance error("Tolerance required.\n"))
    when(mode == "geometry" && target error("Impedance target requires impedance recognition mode.\n"))
    when(pairName != "" && mode != "pair" && mode != "impedance"
      error("Pair selection requires pair or impedance mode.\n"))
    segments = imp_segments(layer)
    rules = makeTable('imp_run_rules nil) pairs = makeTable('imp_run_pairs nil)
    unless(mode == "geometry"
      imp_scope_cache = imp_scopes(d)
      pairs = imp_pair_map(d) keys = imp_impedance_keys()
      foreach(net d->nets rules[net->name] = imp_rule(net layer pairs[net->name] keys)))
    netIndex = makeTable('imp_segments_by_net nil)
    foreach(seg segments
      when(seg->net && !stringp(seg->net)
        netIndex[seg->net] = cons(seg netIndex[seg->net])))
    imp_remaining = imp_budget
    foreach(seg segments
      unless(seg->layer == strcat("ETCH/" layer) error("A foreign-layer segment leaked into selection.\n"))
      unless(listp(seg->startEnd) && length(seg->startEnd) == 2 && listp(seg->bBox)
        error("Segment geometry incomplete.\n"))
      number = seg->width
      unless(numberp(number) && number > 0 error("A segment has no actual width; scan incomplete.\n"))
      rule = if(seg->net && !stringp(seg->net) then rules[seg->net->name] else nil)
      pair = if(seg->net && !stringp(seg->net) then pairs[seg->net->name] else nil)
      eligible = (netName == "" || (seg->net && !stringp(seg->net) && seg->net->name == netName)) &&
        (pairName == "" || (pair && pair->name == pairName)) &&
        (mode == "geometry" || (mode == "impedance" && rule && rule->state == "IMPEDANCE") ||
         (mode == "pair" && pair && (!rule || rule->state != "CONFLICT"))) &&
        (null(target) || (rule && rule->state == "IMPEDANCE" && length(rule->targets) == 1 && abs(car(rule->targets)-target) <= 0.01)) &&
        (null(width) || abs(imp_mil(number)-width) <= tolerance)
      when(eligible
        gap = nil
        when(spacing
          unless(seg->net && !stringp(seg->net) error("Spacing cannot classify a netless segment.\n"))
          if(pair then
            neighbours = nil
            foreach(mate pair->mate neighbours = append(neighbours netIndex[mate]))
          else
            when(mode == "pair" error("Pair membership missing.\n"))
            neighbours = segments)
          unless(neighbours error("No paired-leg/other-net segments on selected layer; spacing unavailable.\n"))
          gap = imp_spacing(seg neighbours)
          when(null(gap) error("No valid spacing measurement for selected segment.\n")))
        when(null(spacing) || (gap != nil && abs(imp_mil(gap)-spacing) <= tolerance)
          objects = cons(seg objects)
          results = cons(list(seg pair rule gap) results))))
    when(length(objects) > 30000 error("Too many highlight objects; choose Net or pair.\n"))
    ; All measurements finish before any display change. Temporary, not permanent.
    imp_design = axlGetDrawingName()
    imp_selected_layer = layer
    imp_visible = axlVisibleGet()
    ;; All retained dbids/geometry are used only while this interactive form
    ;; stays open. Never use Python timers or claim a disk hash proves unsaved edits.
    axlVisibleLayer(strcat("ETCH/" layer) t)
    imp_old_color = axlDBControl('tempColor)
    answer = errset(axlDBControl('tempColor color) nil)
    unless(answer imp_clear() error("Cannot set Allegro temporary highlight color.\n"))
    foreach(obj objects
      answer = errset(axlHighlightObject(obj nil) nil)
      unless(answer && car(answer)
        imp_clear() error("Temporary segment highlight failed; no success reported.\n"))
      imp_marked = cons(obj imp_marked))
    axlUIWUpdate(nil)
    imp_results = reverse(results)
    fp = outfile(strcat(imp_dir "/results.csv") "w")
    unless(fp imp_clear() error("Cannot export native results.\n"))
    fprintf(fp "NET,LAYER,WIDTH_MIL,SPACING_MIL,PAIR,IMPEDANCE_RULE,PHYSICAL_CSET,X1_UU,Y1_UU,X2_UU,Y2_UU,BRD,UNITS,MODE,KIND,CLINE\n")
    foreach(row imp_results
      obj = car(row) pair = cadr(row) rule = caddr(row) gap = cadddr(row) se = obj->startEnd
      imp_csv(fp if(obj->net && !stringp(obj->net) then obj->net->name else "")) fprintf(fp ",")
      imp_csv(fp obj->layer) fprintf(fp ",%.9f," imp_mil(obj->width))
      when(gap != nil fprintf(fp "%.9f" imp_mil(gap))) fprintf(fp ",")
      imp_csv(fp if(pair then pair->name else "")) fprintf(fp ",")
      imp_csv(fp if(rule then rule->raw else "")) fprintf(fp ",")
      imp_csv(fp if(rule then rule->physical else ""))
      fprintf(fp ",%.9f,%.9f,%.9f,%.9f," caar(se)*1.0 cadar(se)*1.0 caadr(se)*1.0 cadadr(se)*1.0)
      imp_csv(fp axlGetDrawingName()) fprintf(fp ",") imp_csv(fp car(axlDBGetDesignUnits()))
      fprintf(fp ",") imp_csv(fp mode) fprintf(fp ",") imp_csv(fp obj->objType)
      fprintf(fp ",") imp_csv(fp obj->parent) fprintf(fp "\n"))
    close(fp)
    imp_finish_export("results" "IMP_RESULTS" length(objects))
    state = sprintf(nil "%d selected segments / %s / mode=%s" length(objects) layer mode)
    axlUIWPrint(nil state)
    when(imp_form axlFormSetField(imp_form "status" state))
    return(length(objects))))

procedure(imp_run(layer widthText spacingText toleranceText mode netName pairName impedanceText color)
  prog((answer cleanup)
    answer = errset(imp_run_impl(layer widthText spacingText toleranceText mode netName pairName impedanceText color) nil)
    unless(answer
      cleanup = errset(imp_clear() nil)
      when(imp_dir errset(imp_invalidate_export("results") nil))
      error("Highlight failed (layer/net/rule/measurement/display/export). No successful result exported.\n"))
    return(car(answer))))

procedure(imp_action(form)
  let((answer)
    case(form->curField
      ("refresh" answer = errset(imp_refresh() t)
        unless(answer axlFormSetField(form "status" "Refresh FAILED - check PCB Editor log.")))
      ("clear" answer = errset(imp_clear() t)
        axlFormSetField(form "status" if(answer then "Temporary highlights cleared." else "Clear FAILED - check PCB Editor log.")))
      ("inspect" answer = errset(imp_inspect() t)
        unless(answer axlFormSetField(form "status" "Pick FAILED - run Highlight again.")))
      ("close" imp_clear() axlFormClose(form) imp_form = nil)
      ("apply"
        answer = errset(imp_run(axlFormGetField(form "layer") axlFormGetField(form "width")
          axlFormGetField(form "spacing") axlFormGetField(form "tolerance") axlFormGetField(form "mode")
          axlFormGetField(form "net") axlFormGetField(form "pair") axlFormGetField(form "impedance")
          atoi(axlFormGetField(form "color"))) t)
        unless(answer axlFormSetField(form "status" "FAILED - check PCB Editor command log, re-scan."))))
    t))

procedure(imp_open()
  prog((fields index defaults answer)
    unless(imp_dir error("Run imp_setup first; use load_imp.scr.\n"))
    when(imp_form error("IMP_HIGHLIGHT already active; close the existing native form.\n"))
    defaults = imp_defaults imp_defaults = nil
    when(defaults && (imp_guard_board != axlGetDrawingName() || imp_guard_token != imp_token)
      error("Active board/session changed. Re-import the current catalogue before running the script.\n"))
    imp_form = axlFormCreate(gensym() strcat(imp_dir "/imp_highlight.form") nil 'imp_action nil t)
    unless(imp_form error("Cannot create native IMP_HIGHLIGHT form.\n"))
    axlFormSetField(imp_form "width" "5") axlFormSetField(imp_form "spacing" "")
    axlFormSetField(imp_form "tolerance" "0.01") axlFormSetField(imp_form "mode" "geometry")
    answer = errset(imp_refresh() t)
    unless(answer
      axlFormClose(imp_form) imp_form = nil
      error("Native catalogue failed; inspect the PCB Editor command log.\n"))
    if(defaults then
      fields = list("layer" "width" "spacing" "tolerance" "mode" "net" "pair" "impedance" "color")
      index = 0
      foreach(field fields
        axlFormSetField(imp_form field nth(index defaults)) index = index + 1)
      answer = errset(imp_run(nth(0 defaults) nth(1 defaults) nth(2 defaults) nth(3 defaults)
        nth(4 defaults) nth(5 defaults) nth(6 defaults) nth(7 defaults) atoi(nth(8 defaults))) t)
      unless(answer axlFormSetField(imp_form "status" "FAILED - see command log. No success assumed."))
    else imp_guard_board = axlGetDrawingName() imp_guard_token = imp_token)
    ; Blocking interactive form retains temporary segment highlight lifetime.
    axlFormDisplay(imp_form)
    imp_clear()
    imp_form = nil
    return(t)))

procedure(imp_prepare(board token layer width spacing tolerance mode net pair impedance color)
  imp_guard_board = board imp_guard_token = token
  imp_defaults = list(layer width spacing tolerance mode net pair impedance sprintf(nil "%d" color))
  t)

procedure(imp_inspect()
  prog((enabled buttons selected answer row rule pair gap lookup se current)
    enabled = axlGetFindFilter() buttons = axlGetFindFilter(t) selected = axlGetSelSet()
    answer = errset(progn(
      unless(imp_design == axlGetDrawingName() error("Board changed; run Highlight again.\n"))
      lookup = makeTable('imp_pick_lookup nil)
      foreach(item imp_results lookup[car(item)] = item)
      axlSetFindFilter(?enabled list("noall" "clinesegs") ?onButtons list("noall" "clinesegs"))
      axlClearSelSet()
      while(axlSelect(?prompt "Pick highlighted segment; right-click Done")
        foreach(obj axlGetSelSet()
          row = lookup[obj]
          when(row
            unless(axlDBRefreshId(obj) error("Stale picked segment.\n"))
            rule = caddr(row) pair = cadr(row) gap = cadddr(row)
            axlUIWPrint(nil sprintf(nil "Net=%s Layer=%s Width=%.6f mil Spacing=UNVERIFIED (rerun Highlight) Pair=%s Impedance=%s"
              if(obj->net && !stringp(obj->net) then obj->net->name else "(no net)")
              obj->layer imp_mil(obj->width)
              if(pair then pair->name else "") if(rule then rule->raw else "")))))
        axlClearSelSet()) t) t)
    axlClearSelSet()
    axlSetFindFilter(?enabled cons("noall" enabled) ?onButtons cons("noall" buttons))
    when(selected axlAddSelectObject(selected))
    unless(answer error("Pick failed; re-run Highlight.\n"))
    return(t)))

axlCmdRegister("IMP_HIGHLIGHT" 'imp_open ?cmdType "interactive")
axlCmdRegister("IMP_REFRESH" 'imp_refresh ?cmdType "general")
axlCmdRegister("IMP_CLEAR" 'imp_clear ?cmdType "general")
axlCmdRegister("IMP_INSPECT" 'imp_inspect ?cmdType "interactive")
