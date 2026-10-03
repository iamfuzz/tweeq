extends Control
## Model Swapper. Pick a ZONE (left) -> its NPC groups -> pane A shows that NPC's model, pane B a
## second character (default: the next different model in the zone; override from the model list).
## Then record a swap (race -> model, in chosen zones), see compatibility findings, Apply / Restore.
## All work goes through Backend (the eqswap engine); this file builds the UI and shuffles JSON.

const HELP_TEXT := """[b]What this app does[/b]
It changes which 3D model an EverQuest race uses, choosing from any model installed in your client. A swap is two small text edits (the client's race table and a zone's model list); the app never copies or ships game files, and everything it writes can be undone.

[b]How to use it[/b]
[b]1. Choose the model to replace (pane A).[/b] Either open the [b]Zones[/b] tab, pick a zone, then click an NPC in that zone, or open the [b]Races[/b] tab and click a race. Pane A shows that model. A swap affects every NPC of that race, not just the one you clicked.
[b]2. Choose the replacement (pane B).[/b] Click any model in the list under the viewers. Type in the box above the list to filter by name or tag.
[b]3. Press Swap A -> B.[/b] This records the swap and writes it to your EverQuest folder straight away. Restart EverQuest completely to see it (the client keeps models cached until it exits).

A swap applies in [b]every zone[/b], so the race never falls back to a default model in a zone you did not think of. (Limiting a swap to certain zones is not supported yet.)

[b]The viewers[/b]
Drag to rotate, mouse wheel to zoom. The two viewers work independently. The first time a model is shown it is converted, which takes a moment.

[b]Native height[/b]
The scale the client uses for the model. The app fills in the replacement model's own value when it knows it; leave it unless a model looks the wrong size.

[b]The notes under the viewers[/b]
ERROR means the swap can't work and the Swap button is disabled. WARNING means something will probably look or behave differently (a missing animation, a weapon that sits wrong, zones the race spawns in that you did not select). INFO is for your information.

[b]Recorded swaps[/b]
The list on the right is every swap you have made: which race uses which model. It is saved by the app. [b]ON[/b] swaps are active in your game files; [b]off[/b] swaps are kept but not active. Select a swap and use Enable, Disable or Remove; each takes effect in your game files immediately. [b]Disable all[/b] switches every swap off and puts your original files back, while keeping the swaps listed so you can enable them again later.

[b]After a game patch[/b]
A patch or the launcher can put the original files back. When you open the app it notices and re-applies your swaps by itself. If a patch changed one of the files in a way the app does not recognise, a red message appears with a button to re-apply your swaps on top of the new files.

[b]Other buttons[/b]
Refresh reloads everything. Find EverQuest searches for your EverQuest folder; you can also type a folder in the box and press Enter. "Adopt existing edits" appears if your install already contains edits the app did not make; adopt them first so the app can restore the true originals. Until then the Swap button is switched off.

[b]Limits[/b]
The app swaps which model a race uses. It does not change meshes or textures inside a model file.
"""

var races: Array = []
var models: Array = []
var swaps: Array = []
var sel_race: Dictionary = {}
var sel_tag := ""
var zones_all: Array = []
var cur_zone := ""
var npc_groups: Array = []
var check_worst := "ok"
var height_auto := false
var model_names := {}  # tag -> display name ("cth" -> "Cazic-Thule (new)")
var pane_want := {}  # pane -> model tag it should show (for async preview results)

var tabs: TabContainer
var zpick_filter: LineEdit
var zpick_list: ItemList
var npc_list: ItemList
var race_filter: LineEdit
var race_list: ItemList
var model_filter: LineEdit
var model_list: ItemList
var height_edit: LineEdit
var detail_label: Label
var warn_label: Label
var findings_box: RichTextLabel
var add_btn: Button
var swap_summary: Label
var swap_list: ItemList
var log_box: RichTextLabel
var status_label: Label
var eq_edit: LineEdit
var mode_label: Label
var adopt_btn: Button
var drift_btn: Button
var launch_btn: Button
var drifted: Array = []
var _auto_applied := false
var unmanaged: Array = []
var pane_a: ViewerPane
var pane_b: ViewerPane


func _ready() -> void:
	_build()
	Backend.preview_ready.connect(_on_preview_ready)
	Backend.cli_done.connect(_on_cli_done)
	if Backend.first_run:
		status_label.text = "first launch: looking for your EverQuest folder ..."
		_discover()
	else:
		refresh_all()


# ------------------------------------------------------------------ UI construction
func _build() -> void:
	set_anchors_preset(Control.PRESET_FULL_RECT)
	var root := VBoxContainer.new()
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.add_theme_constant_override("separation", 6)
	add_child(root)

	var top := HBoxContainer.new()
	root.add_child(top)
	top.add_child(_label("EverQuest folder:"))
	eq_edit = LineEdit.new()
	eq_edit.text = str(Backend.cfg.eq)
	eq_edit.custom_minimum_size.x = 260
	eq_edit.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	eq_edit.text_submitted.connect(_on_path_typed)
	top.add_child(eq_edit)
	top.add_child(_btn("Refresh", refresh_all))
	top.add_child(_btn("Find EverQuest", _discover))
	top.add_child(_btn("Help", _show_help))
	adopt_btn = _btn("Adopt existing edits", _on_adopt)
	adopt_btn.visible = false
	top.add_child(adopt_btn)
	drift_btn = _btn("Re-apply swaps to patched files", _on_accept_drift)
	drift_btn.visible = false
	top.add_child(drift_btn)
	status_label = _label("")
	top.add_child(status_label)
	mode_label = _label("")
	mode_label.visible = false
	mode_label.add_theme_font_size_override("font_size", 15)
	root.add_child(mode_label)

	var split := HSplitContainer.new()
	split.size_flags_vertical = Control.SIZE_EXPAND_FILL
	root.add_child(split)

	# left: zone-first browsing (tab 1) and the raw race list (tab 2)
	tabs = TabContainer.new()
	tabs.custom_minimum_size.x = 400
	tabs.size_flags_stretch_ratio = 0.8
	split.add_child(tabs)
	var zt := VBoxContainer.new()
	zt.name = "Zones"
	tabs.add_child(zt)
	zpick_filter = LineEdit.new()
	zpick_filter.placeholder_text = "filter zones, e.g. gfaydark"
	zpick_filter.text_changed.connect(func(_t: String) -> void: _fill_zpick())
	zt.add_child(zpick_filter)
	zpick_list = ItemList.new()
	zpick_list.size_flags_vertical = Control.SIZE_EXPAND_FILL
	zpick_list.size_flags_stretch_ratio = 0.8
	zpick_list.item_selected.connect(_on_zone_picked)
	zt.add_child(zpick_list)
	zt.add_child(_label("NPCs in this zone"))
	npc_list = ItemList.new()
	npc_list.size_flags_vertical = Control.SIZE_EXPAND_FILL
	npc_list.size_flags_stretch_ratio = 1.2
	npc_list.item_selected.connect(_on_npc_picked)
	zt.add_child(npc_list)
	var rt := VBoxContainer.new()
	rt.name = "Races"
	tabs.add_child(rt)
	var rlabel := _label("Races  -  click one to choose the model to REPLACE (A). Every NPC of that race changes.")
	rlabel.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	rlabel.custom_minimum_size.x = 100
	rt.add_child(rlabel)
	race_filter = LineEdit.new()
	race_filter.placeholder_text = "filter: id, model tag or NPC name"
	race_filter.text_changed.connect(func(_t: String) -> void: _fill_races())
	rt.add_child(race_filter)
	race_list = ItemList.new()
	race_list.size_flags_vertical = Control.SIZE_EXPAND_FILL
	race_list.item_selected.connect(_on_race_selected)
	rt.add_child(race_list)

	var split2 := HSplitContainer.new()
	split2.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	split.add_child(split2)

	# centre: preview + pickers
	var centre := VBoxContainer.new()
	centre.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	split2.add_child(centre)
	detail_label = _label("")
	detail_label.visible = false
	detail_label.add_theme_font_size_override("font_size", 16)
	detail_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	detail_label.custom_minimum_size.x = 100
	centre.add_child(detail_label)
	var panes := HBoxContainer.new()
	centre.add_child(panes)
	pane_a = ViewerPane.new("A: REPLACE THIS")
	pane_a.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	pane_a.custom_minimum_size = Vector2(300, 270)
	panes.add_child(pane_a)
	pane_b = ViewerPane.new("B: WITH THIS")
	pane_b.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	pane_b.custom_minimum_size = Vector2(300, 270)
	panes.add_child(pane_b)
	var steps := _label("HOW IT WORKS   1) Choose the model to REPLACE (pane A): pick a Zone and then an NPC, or pick a Race  -  both are tabs on the left."
			+ "    2) Choose the model to replace it WITH (pane B): click any model in the list below."
			+ "    3) Press Swap A -> B, then Apply all.")
	steps.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	steps.custom_minimum_size.x = 100
	steps.modulate = Color(0.75, 0.85, 1.0)
	centre.add_child(steps)
	var bar := HBoxContainer.new()
	centre.add_child(bar)
	swap_summary = _label("")
	swap_summary.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	swap_summary.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	bar.add_child(swap_summary)
	add_btn = _btn("Swap A  ->  B", _on_add_swap)
	add_btn.disabled = true
	add_btn.custom_minimum_size = Vector2(150, 40)
	bar.add_child(add_btn)

	var pickers := HSplitContainer.new()
	pickers.size_flags_vertical = Control.SIZE_EXPAND_FILL
	pickers.custom_minimum_size.y = 230
	centre.add_child(pickers)
	var mbox := VBoxContainer.new()
	mbox.custom_minimum_size.x = 300
	mbox.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	pickers.add_child(mbox)
	mbox.add_child(_label("B: the REPLACEMENT  -  click ANY installed model"))
	model_filter = LineEdit.new()
	model_filter.placeholder_text = "filter models, e.g. cth"
	model_filter.text_changed.connect(func(_t: String) -> void: _fill_models())
	mbox.add_child(model_filter)
	model_list = ItemList.new()
	model_list.size_flags_vertical = Control.SIZE_EXPAND_FILL
	model_list.item_selected.connect(_on_model_selected)
	mbox.add_child(model_list)
	var opts := HBoxContainer.new()
	mbox.add_child(opts)
	opts.add_child(_label("Native height (optional):"))
	height_edit = LineEdit.new()
	height_edit.placeholder_text = "e.g. 6"
	height_edit.custom_minimum_size.x = 70
	height_edit.text_changed.connect(func(_t: String) -> void: height_auto = false)
	opts.add_child(height_edit)
	warn_label = _label("")
	warn_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	warn_label.modulate = Color(1, 0.85, 0.5)
	centre.add_child(warn_label)
	findings_box = RichTextLabel.new()
	findings_box.bbcode_enabled = true
	findings_box.custom_minimum_size.y = 84
	centre.add_child(findings_box)

	# right: swaps + files
	var right := VBoxContainer.new()
	right.custom_minimum_size.x = 400
	split2.add_child(right)
	right.add_child(_label("Recorded swaps"))
	swap_list = ItemList.new()
	swap_list.size_flags_vertical = Control.SIZE_EXPAND_FILL
	right.add_child(swap_list)
	var sb := HBoxContainer.new()
	right.add_child(sb)
	sb.add_child(_btn("Enable", func() -> void: _swap_action("enable")))
	sb.add_child(_btn("Disable", func() -> void: _swap_action("disable")))
	sb.add_child(_btn("Remove", func() -> void: _swap_action("remove")))
	sb.add_child(_btn("Disable all", _on_disable_all))

	log_box = RichTextLabel.new()
	log_box.custom_minimum_size.y = 80
	log_box.scroll_following = true
	log_box.bbcode_enabled = true
	root.add_child(log_box)
	var bottom := CenterContainer.new()
	root.add_child(bottom)
	launch_btn = _btn("Launch EverQuest", _launch_eq)
	launch_btn.custom_minimum_size = Vector2(220, 38)
	bottom.add_child(launch_btn)


## "Cazic-Thule (new)  [cth]" for a model tag.
func _nm(tag: String) -> String:
	return "%s  [%s]" % [model_names.get(tag.to_lower(), tag.to_upper()), tag.to_lower()]


## Help window: how to use the app, and what the Recorded swaps and Files panels mean.
func _show_help() -> void:
	var dlg := AcceptDialog.new()
	dlg.title = "Help"
	dlg.ok_button_text = "Close"
	dlg.min_size = Vector2i(820, 640)
	var rt := RichTextLabel.new()
	rt.bbcode_enabled = true
	rt.scroll_active = true
	rt.custom_minimum_size = Vector2(780, 560)
	rt.text = HELP_TEXT
	dlg.add_child(rt)
	dlg.canceled.connect(func() -> void: dlg.queue_free())
	dlg.confirmed.connect(func() -> void: dlg.queue_free())
	add_child(dlg)
	dlg.popup_centered()


func _set_detail(text: String) -> void:
	detail_label.text = text
	detail_label.visible = text != ""


func _label(t: String) -> Label:
	var l := Label.new()
	l.text = t
	return l


func _btn(t: String, cb: Callable) -> Button:
	var b := Button.new()
	b.text = t
	b.pressed.connect(cb)
	return b


func log_line(text: String, err := false) -> void:
	log_box.append_text(("[color=#ff7777]%s[/color]\n" if err else "%s\n") % text)


# ------------------------------------------------------------------ data
## Run an engine command; log and return data (null on error).
func _call(args: Array) -> Variant:
	var r: Dictionary = Backend.call_cli(args)
	if not r.get("ok", false):
		log_line("%s: %s" % [" ".join(args.slice(0, 2)), r.get("error", "?")], true)
		return null
	return r.get("data")


func refresh_all() -> void:
	Backend.cfg.eq = eq_edit.text.strip_edges()
	var info = _call(["info"])
	if info == null:
		status_label.text = "engine not reachable / bad folder"
		return
	status_label.text = "%d models, %d swaps%s" % [int(info.models if info.models != null else 0),
			int(info.swaps), "" if info.peq else ", no PEQ import (no NPC lists)"]
	unmanaged = info.get("unmanaged_edits", [])
	_update_banner()
	var rr = _call(["races"])
	races = rr if rr != null else []
	var mm = _call(["models"]) if str(Backend.cfg.index) != "" else []
	models = mm if mm != null else []
	model_names.clear()
	for md in models:
		model_names[str(md.tag)] = str(md.name)
	var z = _call(["zones"])
	zones_all = z.all if z != null else []
	_fill_zpick()
	_fill_races()
	_fill_models()
	_refresh_swaps()


## Make it unmistakable which folder Apply/Restore will write to, and block writes while the install
## carries edits the app does not track (it would treat them as the vanilla originals).
func _update_banner() -> void:
	adopt_btn.visible = not unmanaged.is_empty()
	drift_btn.visible = unmanaged.is_empty() and not drifted.is_empty()
	mode_label.visible = not unmanaged.is_empty() or not drifted.is_empty()
	if not unmanaged.is_empty():
		mode_label.text = "%d edited file(s) are not tracked yet (%s). Click 'Adopt existing edits' before swapping anything." % [
				unmanaged.size(), ", ".join(unmanaged)]
		mode_label.modulate = Color(1.0, 0.45, 0.45)
	elif not drifted.is_empty():
		mode_label.text = "A game patch or another tool changed %s. Your swaps are not on top of the new version yet: click 'Re-apply swaps to patched files'." % ", ".join(drifted)
		mode_label.modulate = Color(1.0, 0.45, 0.45)
	else:
		mode_label.text = ""
	_update_add_state()


func _on_accept_drift() -> void:
	var r = _call(["apply", "--accept-drift"])
	if r != null:
		log_line("re-applied your swaps on top of the changed files")
		_refresh_swaps()


func _on_adopt() -> void:
	var r = _call(["adopt"])
	if r == null:
		return
	if r.adopted.is_empty():
		log_line("nothing to adopt")
	for a in r.adopted:
		log_line("adopted: race %d  %s -> %s  in %s%s" % [int(a.race), a["from"], a.to,
				",".join(a.zones) if not a.zones.is_empty() else "(global)",
				("  height " + str(a.height)) if a.height != null else ""])
	refresh_all()


func _refresh_swaps() -> void:
	var l = _call(["list"])
	swaps = l if l != null else []
	swap_list.clear()
	for d in swaps:
		swap_list.add_item("%s race %d  %s -> %s   [%s]" % ["ON " if d.enabled else "off", int(d.race),
				d.original_tag, d.model_tag, "all zones" if bool(d.get("all_zones", false)) else ",".join(d.zones)])
	var st = _call(["status"])
	drifted = []
	var stale := false
	for f in (st if st != null else []):
		if f.state == "drifted":
			drifted.append(f.path)
		elif f.state == "needs-apply":
			stale = true
	# A patch (or the launcher) can restore the original files: put the swaps back by themselves.
	if stale and drifted.is_empty() and unmanaged.is_empty() and not _auto_applied:
		_auto_applied = true
		if _apply_now():
			log_line("a patch or the launcher had restored the original files; your swaps were re-applied")
	_update_banner()


## Write every enabled swap to the game files. False (and logged) when the engine refuses.
func _apply_now() -> bool:
	return _call(["apply"]) != null


func _fill_zpick() -> void:
	zpick_list.clear()
	var f := zpick_filter.text.strip_edges().to_lower()
	for z in zones_all:
		if f != "" and f not in str(z):
			continue
		var i := zpick_list.add_item(str(z))
		zpick_list.set_item_metadata(i, z)


func _fill_races() -> void:
	race_list.clear()
	var f := race_filter.text.strip_edges().to_lower()
	for r in races:
		var tags := ",".join((r.tags as Dictionary).values())
		var nm: String = str(r.name) if str(r.name) != "" else str(r.example)
		var line := "%4d  %-6s %s%s" % [int(r.race), tags, nm, ("  (e.g. %s)" % str(r.example)) if str(r.name) != "" and str(r.example) != "" else ""]
		if bool(r.swapped):
			line = "* " + line
		if f != "" and f not in line.to_lower():
			continue
		var i := race_list.add_item(line)
		race_list.set_item_metadata(i, r)


func _fill_models() -> void:
	model_list.clear()
	var f := model_filter.text.strip_edges().to_lower()
	for m in models:
		var fmt: String = "EQG" if str(m.format).begins_with("eqg") else "WLD"
		var line := "%-34s %-5s %s" % [str(m.name).left(34), str(m.tag).to_upper(), fmt]
		if f != "" and f not in line.to_lower():
			continue
		var i := model_list.add_item(line)
		model_list.set_item_metadata(i, m)


# ------------------------------------------------------------------ zone-first flow
func _on_zone_picked(idx: int) -> void:
	cur_zone = str(zpick_list.get_item_metadata(idx))
	npc_list.clear()
	var g = _call(["npcs", cur_zone])
	npc_groups = g if g != null else []
	for grp in npc_groups:
		var line := "%-24s %-26s x%-3d L%d%s" % [str(grp.example).left(24), str(grp.model_name).left(26),
				int(grp.npcs), int(grp.level), "" if bool(grp.has_model) else "   (no model)"]
		var i := npc_list.add_item(line)
		npc_list.set_item_metadata(i, grp)
	if npc_groups.is_empty():
		_set_detail("%s: no NPC spawn data (needs the PEQ import)" % cur_zone)
		return
	for i in npc_list.item_count:
		if bool((npc_list.get_item_metadata(i) as Dictionary).has_model):
			npc_list.select(i)
			_on_npc_picked(i)
			return


func _on_npc_picked(idx: int) -> void:
	var g: Dictionary = npc_list.get_item_metadata(idx)
	var race_info := {}
	for r in races:
		if int(r.race) == int(g.race):
			race_info = r
			break
	if race_info.is_empty():
		log_line("race %d has no racedata row" % int(g.race), true)
		return
	_apply_race(race_info)
	_set_detail("%s  -  %s  (%s, %d NPC types here)" % [cur_zone, str(g.example),
			str(g.model_name), int(g.npcs)])
	_want(pane_a, str(g.tag), "A: REPLACE THIS  -  %s" % _nm(str(g.tag)))
	_run_check()
	_update_add_state()


func _describe_candidate(tag: String) -> void:
	for m in models:
		if str(m.tag) == tag.to_lower():
			var bits: Array = []
			if m.list_archive == null:
				bits.append("already loaded globally: no zone list line needed")
			else:
				bits.append("will add '%s,%s' to each chosen zone's list" % [m.tag, m.list_archive])
			bits.append_array(m.notes)
			warn_label.text = " | ".join(bits)
			return


# ------------------------------------------------------------------ race / model / zone events
func _on_race_selected(idx: int) -> void:
	_apply_race(race_list.get_item_metadata(idx))
	var cur: String = str((sel_race.tags as Dictionary).values()[0])
	_want(pane_a, cur, "A: REPLACE THIS  -  %s" % _nm(cur))
	_run_check()
	_update_add_state()


## Make `r` the selected race.
func _apply_race(r: Dictionary) -> void:
	sel_race = r
	var gs := ",".join((sel_race.tags as Dictionary).values())
	var was := ",".join((sel_race.original_tags as Dictionary).values())
	_set_detail("Race %d  -  model %s%s   %s" % [int(sel_race.race), gs,
			("  (original %s)" % was) if bool(sel_race.swapped) else "", sel_race.example])


func _on_model_selected(idx: int) -> void:
	var m: Dictionary = model_list.get_item_metadata(idx)
	sel_tag = str(m.tag)
	_describe_candidate(sel_tag)
	_want(pane_b, sel_tag, "B: WITH THIS  -  %s" % _nm(sel_tag))
	_run_check()
	_update_add_state()


## Select every zone where the chosen race spawns (the starred ones), so no spawn zone is left without the model.
func _update_summary() -> void:
	if sel_race.is_empty() or sel_tag == "":
		swap_summary.text = ""
		return
	var a: String = str((sel_race.tags as Dictionary).values()[0])
	var line := "Replace %s (race %d: every NPC of this race) with %s in every zone" % [_nm(a), int(sel_race.race), _nm(sel_tag)]
	swap_summary.text = ("Cannot swap: " + line.substr(0, 1).to_lower() + line.substr(1)) if check_worst == "error" else line


func _update_add_state() -> void:
	_update_summary()
	add_btn.disabled = sel_race.is_empty() or sel_tag == "" or check_worst == "error" \
			or not unmanaged.is_empty()


## Pre-swap compatibility findings for the chosen race + model + zones.
func _run_check() -> void:
	check_worst = "ok"
	if sel_race.is_empty() or sel_tag == "":
		findings_box.text = ""
		return
	var d = _call(["check", str(int(sel_race.race)), sel_tag])
	if d == null:
		findings_box.text = "[color=#ff7777]compatibility check unavailable[/color]"
		return
	check_worst = str(d.worst)
	var colors := {"error": "#ff6666", "warn": "#ffcc55", "info": "#9fb4c8"}
	var out := ""
	for f in d.findings:
		out += "[color=%s][b]%s[/b][/color] %s\n" % [colors.get(f.level, "#ffffff"), str(f.level).to_upper(), f.message]
	findings_box.text = out if out != "" else "[color=#77dd77]No compatibility concerns found.[/color]"
	if d.suggest_height != null and (height_edit.text.strip_edges() == "" or height_auto):
		height_edit.text = str(d.suggest_height)
		height_auto = true
	elif d.suggest_height == null and height_auto:
		height_edit.text = ""
		height_auto = false


# ------------------------------------------------------------------ previews
## Show a model in a pane: instantly if cached, otherwise convert on a worker thread.
func _want(pane: ViewerPane, tag: String, title: String) -> void:
	tag = tag.to_lower()
	pane.set_title(title)
	pane_want[pane] = tag
	var p: String = Backend.preview_path(tag)
	if p != "":
		pane.set_model(p)  # show the cached file at once ...
	else:
		pane.clear_model()
		pane.show_message("converting %s ..." % tag.to_upper())
	Backend.ensure_preview_async(tag)  # ... and always let the engine revalidate it (patches, converter fixes)


func _on_preview_ready(tag: String, r: Dictionary) -> void:
	for pane in [pane_a, pane_b]:
		if pane_want.get(pane, "") != tag:
			continue
		var data: Dictionary = r.get("data", {}) if r.get("ok", false) else {}
		var st: String = str(data.get("status", "error"))
		if st == "ok" or st == "cached":
			if st == "cached" and pane.has_model():
				continue  # unchanged: leave the already-displayed model alone
			var p: String = Backend.preview_path(tag)
			if p != "":
				pane.set_model(p)
				continue
		var why: String = str(data.get("error", r.get("error", "failed")))
		pane.clear_model()
		pane.show_message("no preview for %s\n%s" % [tag.to_upper(), why.left(120)])
		log_line("preview %s: %s" % [tag, why.left(200)], true)


# ------------------------------------------------------------------ swaps
func _on_add_swap() -> void:
	var args: Array = ["swap", str(int(sel_race.race)), sel_tag, "--zones", "all"]
	if height_edit.text.strip_edges() != "":
		args.append_array(["--height", height_edit.text.strip_edges()])
	var d = _call(args)
	if d == null:
		return
	if _apply_now():
		log_line("swapped: race %d %s -> %s in every zone (applied; restart EverQuest to see it)" % [int(d.race),
				d.original_tag, d.model_tag])
	else:
		_call(["remove", str(d.id)])  # keep the list honest: a swap that could not be applied is not recorded
		log_line("the swap could not be applied, so it was not kept", true)
	_refresh_swaps()


func _swap_action(action: String) -> void:
	var sel := swap_list.get_selected_items()
	if sel.is_empty():
		log_line("select a swap in the list first", true)
		return
	var id: String = swaps[sel[0]].id
	if _call([action, id]) != null:
		_apply_now()
		log_line("%s %s" % [action, id.left(8)])
		_refresh_swaps()


func _on_disable_all() -> void:
	if swaps.is_empty():
		return
	if _call(["disable-all"]) != null:
		_apply_now()
		log_line("all swaps disabled; your original files are back (the swaps stay listed so you can enable them again)")
		_refresh_swaps()


## ---- finding the EverQuest folder ----------------------------------------------------------------
func _discover() -> void:
	status_label.text = "looking for your EverQuest folder ..."
	Backend.call_cli_async("discover", ["discover"], false)


func _on_cli_done(key: String, r: Dictionary) -> void:
	if key != "discover":
		return
	var cands: Array = r.get("data", {}).get("candidates", []) if r.get("ok", false) else []
	if cands.is_empty():
		status_label.text = "EverQuest folder not found"
		log_line("Could not find EverQuest automatically. Type its folder in the box above and press Enter.", true)
		if Backend.first_run:
			Backend.first_run = false
		return
	if cands.size() == 1:
		_use_install(str(cands[0].path), str(cands[0].source))
		return
	_choose_install(cands)


func _choose_install(cands: Array) -> void:
	var dlg := ConfirmationDialog.new()
	dlg.title = "Which EverQuest folder?"
	dlg.ok_button_text = "Use this one"
	var box := VBoxContainer.new()
	box.add_child(_label("Found %d EverQuest installs. Pick the one you play:" % cands.size()))
	var il := ItemList.new()
	il.custom_minimum_size = Vector2(620, 160)
	for c in cands:
		il.add_item("%s    (%s)" % [str(c.display), str(c.source)])
	il.select(0)
	box.add_child(il)
	dlg.add_child(box)
	dlg.confirmed.connect(func() -> void:
		var s := il.get_selected_items()
		var pick: Dictionary = cands[s[0] if not s.is_empty() else 0]
		_use_install(str(pick.path), str(pick.source))
		dlg.queue_free())
	dlg.canceled.connect(func() -> void: dlg.queue_free())
	add_child(dlg)
	dlg.popup_centered()


## Make `path` the install the app manages: own vault per install, vanilla-edit guard only for the
## original install (the vanilla backups belong to it).
func _use_install(path: String, source: String) -> void:
	var enginep: String = Backend.to_engine_path(path)
	Backend.cfg.mode = "real"
	Backend.cfg.eq = enginep
	Backend.cfg.real_eq = enginep
	Backend.cfg.vault = Backend.vault_for(enginep)
	Backend.cfg.models = enginep
	if not Backend.is_default_install(enginep):
		Backend.cfg.vanilla = ""
	Backend.first_run = false
	Backend.save_config()
	eq_edit.text = enginep
	log_line("Using EverQuest folder: %s  (found via %s)" % [enginep, source])
	refresh_all()


func _on_path_typed(t: String) -> void:
	t = t.strip_edges()
	var chk = _call_no_install(["discover", "--check", t])
	if chk == null:
		return
	if not bool(chk.valid):
		log_line("That folder doesn't look like an EverQuest install (missing: %s)." % ", ".join(chk.missing), true)
		status_label.text = "not an EverQuest folder"
		return
	_use_install(str(chk.path), "typed path")


func _call_no_install(args: Array) -> Variant:
	var r: Dictionary = Backend.call_cli(args, false)
	if not r.get("ok", false):
		log_line("%s: %s" % [args[0], r.get("error", "?")], true)
		return null
	return r.get("data")


## Start EverQuest with patchme (skips the patcher so the swapped files survive).
func _launch_eq() -> void:
	match Backend.launch_eq():
		"started":
			log_line("Launching EverQuest (patchme) ...")
		"running":
			log_line("EverQuest is already running. Close it completely, then launch again to see your swaps (the client keeps models cached until it exits).", true)
		_:
			log_line("Could not find eqgame.exe in %s" % Backend.to_windows_path(str(Backend.cfg.eq)), true)
