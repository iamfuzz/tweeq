extends SceneTree
## Headless end-to-end check:  Godot --headless --path <project> --script res://scripts/smoke_test.gd
## Drives the real Main scene against the SANDBOX install through the real engine.

var failures := 0


func check(cond: bool, what: String) -> void:
	print(("PASS  " if cond else "FAIL  ") + what)
	if not cond:
		failures += 1


func _initialize() -> void:
	call_deferred("_run")


func Backend_path(backend: Node, tag: String) -> String:
	return backend.preview_path(tag)


func _find_model(list: ItemList, tag: String) -> int:
	for i in list.item_count:
		if str((list.get_item_metadata(i) as Dictionary).tag) == tag:
			return i
	return -1


func _find_item(list: ItemList, key: String) -> int:
	for i in list.item_count:
		if list.get_item_text(i).strip_edges().begins_with(key):
			return i
	return -1


func _run() -> void:
	var backend = root.get_node_or_null("Backend")
	check(backend != null, "Backend autoload present")
	if backend == null:
		quit(1)
		return
	# the default mode is the real install now; the test must NEVER touch it, so force the sandbox first
	backend.first_run = false  # never auto-discover / save config during tests
	backend.cfg.mode = "sandbox"
	backend.cfg.eq = backend.cfg.sandbox_eq
	backend.cfg.vault = backend.cfg.sandbox_vault
	check(str(backend.cfg.eq).contains("sandbox"), "test is pinned to the sandbox, not the real install")
	check(backend.to_engine_path("C:\\Users\\Public\\Daybreak Game Company\\X") == "/mnt/c/Users/Public/Daybreak Game Company/X", "Windows paths are converted for the WSL launcher")
	check(backend.to_engine_path("/mnt/c/already/ok") == "/mnt/c/already/ok", "WSL paths pass through unchanged")
	var r: Dictionary = backend.call_cli(["info"])
	check(r.get("ok", false), "engine reachable through wsl.exe (info)")
	if not r.get("ok", false):
		print(r)
		quit(1)
		return

	var main_scene: PackedScene = load("res://scenes/main.tscn")
	var m: Control = main_scene.instantiate()
	root.add_child(m)
	await process_frame
	check(str(m.swap_summary.text) == "" and not m.detail_label.visible, "no placeholder text before anything is picked")
	check(not m.mode_label.visible, "no mode banner (REAL INSTALL / SANDBOX text is gone)")
	var button_texts: Array = []
	var stack: Array = [m]
	while not stack.is_empty():
		var n: Node = stack.pop_back()
		if n is Button:
			button_texts.append(str((n as Button).text))
		stack.append_array(n.get_children())
	for gone in ["Use my EverQuest", "Use sandbox", "Apply all", "Restore all"]:
		check(not button_texts.has(gone), "'%s' button is gone" % gone)
	check(not button_texts.any(func(x): return x.begins_with("Use this NPC")), "no 'use NPC as B' button")
	for want in ["Launch EverQuest", "Help", "Disable all", "Swap A  ->  B", "Find EverQuest", "Refresh"]:
		check(button_texts.has(want), "button present: '%s'" % want)
	check(m.get("files_list") == null, "the Files box is gone")
	check(str(m.launch_btn.text) == "Launch EverQuest", "launch button exists (not pressed in tests)")
	check(backend.to_windows_path("/mnt/c/Users/Public/Daybreak Game Company/EverQuest") == "C:\\Users\\Public\\Daybreak Game Company\\EverQuest", "engine path -> Windows path for launching")
	check(backend.to_windows_path("C:\\Already\\Windows") == "C:\\Already\\Windows", "Windows paths pass through unchanged")
	m._show_help()
	await process_frame
	var help_ok := false
	for c in m.get_children():
		if c is AcceptDialog:
			var lbl := c.get_child(c.get_child_count() - 1)
			for n in c.get_children():
				if n is RichTextLabel and (n as RichTextLabel).text.contains("Recorded swaps") and (n as RichTextLabel).text.contains("Disable all"):
					help_ok = true
			c.queue_free()
	check(help_ok, "Help opens and explains Recorded swaps / Disable all")
	check(not m.adopt_btn.visible and m.unmanaged.is_empty(), "no untracked edits in the vanilla sandbox")
	var tags_in_list := []
	for i in m.model_list.item_count:
		tags_in_list.append(str((m.model_list.get_item_metadata(i) as Dictionary).tag))
	for obj in ["boat", "launch", "ship", "i11", "t00", "dest_cgg", "box", "chs", "bnx", "ghostship"]:
		check(not tags_in_list.has(obj), "object '%s' is not in the model list" % obj)
	check(tags_in_list.has("cth") and tags_in_list.has("tmt") and tags_in_list.has("bat"), "characters (cth, tmt, bat) are still listed")
	check(m.race_list.item_count > 300, "race list populated (%d)" % m.race_list.item_count)
	check(m.model_list.item_count > 600, "model list populated (%d)" % m.model_list.item_count)
	check(m.zones_all.size() > 400, "zone lists found (%d)" % m.zones_all.size())

	# ---- MVP scenario: pick a zone, see two characters side by side ------------------------
	m.zpick_filter.text = "gfaydark"
	m._fill_zpick()
	var zi: int = _find_item(m.zpick_list, "gfaydark")
	check(zi >= 0, "zone list contains gfaydark")
	m.zpick_list.select(zi)
	m._on_zone_picked(zi)
	check(m.npc_groups.size() > 2, "gfaydark NPC groups loaded (%d)" % m.npc_groups.size())
	check(m.npc_list.item_count == m.npc_groups.size(), "NPC list shows every group")
	var tag_a: String = str(m.pane_want.get(m.pane_a, ""))
	check(tag_a != "", "picking a zone selects its first NPC: pane A wants '%s'" % tag_a)
	check(str(m.pane_want.get(m.pane_b, "")) == "" and m.sel_tag == "" and not m.pane_b.has_model(), "an NPC pick changes ONLY pane A: pane B is untouched")
	check(str(m.cur_zone) == "gfaydark", "the zone you picked is remembered")
	check(m.npc_list.item_activated.get_connections().is_empty(), "double-click on an NPC does nothing")
	check(m.get("use_b_btn") == null, "the 'use NPC as B' button is gone")
	# B comes from the model list
	m.model_filter.text = "elf"
	m._fill_models()
	var ei: int = _find_model(m.model_list, "elf")
	check(ei >= 0, "model list contains 'elf'")
	m.model_list.select(ei)
	m._on_model_selected(ei)
	var tag_b: String = str(m.pane_want.get(m.pane_b, ""))
	check(tag_b == "elf" and m.sel_tag == "elf", "clicking a model in the list sets pane B (%s)" % tag_b)
	check(m.pane_b.title.contains("WITH THIS") and m.pane_a.title.contains("REPLACE THIS"), "panes are labelled by role: '%s' / '%s'" % [m.pane_a.title, m.pane_b.title])
	var waited := 0.0
	while waited < 150.0 and not (m.pane_a.has_model() and m.pane_b.has_model()):
		if str(m.pane_a._msg.text).begins_with("no preview") or str(m.pane_b._msg.text).begins_with("no preview"):
			print("      preview failed: A='%s' B='%s'" % [m.pane_a._msg.text, m.pane_b._msg.text])
			break
		await create_timer(0.5).timeout
		waited += 0.5
	check(m.pane_a.has_model(), "pane A shows %s after %.1fs" % [tag_a, waited])
	check(m.pane_b.has_model(), "pane B shows %s after %.1fs" % [tag_b, waited])
	var pb: String = Backend_path(backend, tag_b)
	check(pb != "" and FileAccess.file_exists(pb), "preview cache file exists for %s" % tag_b)
	var before: int = m.pane_b._holder.get_child_count()
	for i in 5:
		m.pane_b.set_model(pb)
		await process_frame
	await process_frame
	await process_frame
	check(m.pane_b._holder.get_child_count() <= before + 1, "5 reloads leave one model (holder children %d)" % m.pane_b._holder.get_child_count())
	check(str(m.swap_summary.text).begins_with("Replace "), "swap bar explains the pending swap: " + str(m.swap_summary.text))
	# picking another NPC changes A and leaves B alone
	var a_before: String = str(m.pane_want[m.pane_a])
	var other := -1
	for i in m.npc_list.item_count:
		var gg: Dictionary = m.npc_list.get_item_metadata(i)
		if bool(gg.has_model) and str(gg.tag).to_lower() != a_before:
			other = i
			break
	m.npc_list.select(other)
	m._on_npc_picked(other)
	check(str(m.pane_want[m.pane_a]) != a_before and m.sel_tag == "elf" and str(m.pane_want[m.pane_b]) == "elf", "picking another NPC changes A only; B stays on elf")
	m.model_filter.text = ""
	m._fill_models()

	m.race_filter.text = "caz"
	m._fill_races()
	var ri: int = _find_item(m.race_list, "95")
	check(ri >= 0, "filter finds race 95 (Cazic)")
	m.race_list.select(ri)
	m._on_race_selected(ri)
	check(int(m.sel_race.race) == 95, "race 95 selected")
	check(m.get("zone_list") == null and m.get("zone_sel") == null, "the zone picker is gone: swaps apply in every zone")

	m.model_filter.text = "cazic"
	m._fill_models()
	check(m.model_list.item_count >= 2, "filtering the model list by name 'cazic' finds both Cazic models (%d)" % m.model_list.item_count)
	var mi: int = _find_model(m.model_list, "cth")
	check(mi >= 0, "model filter finds cth")
	m.model_list.select(mi)
	m._on_model_selected(mi)
	check(m.sel_tag == "cth", "model cth selected")
	check(str(m.model_names.get("cth", "")).contains("Cazic"), "model names are human-readable: cth = '%s'" % str(m.model_names.get("cth", "")))
	check(str(m.model_names.get("tmt", "")) == "Quarm", "tmt is named 'Quarm' (boss rule), got '%s'" % str(m.model_names.get("tmt", "")))
	check(str(m.model_list.get_item_text(mi)).contains("Cazic"), "model list row shows the name: '%s'" % str(m.model_list.get_item_text(mi)).strip_edges())
	check(m.warn_label.text.contains("cth,cth"), "warning explains the zone list line: " + m.warn_label.text)

	check(m.findings_box.text.contains("ARMR_WEAP"), "findings shown: CTH weapon-bone defect is called out")
	check(m.height_edit.text == "6", "recommended native height (6) was prefilled, got '%s'" % m.height_edit.text)
	check(m.check_worst == "warn", "worst finding level is warn")
	check(not m.findings_box.text.contains("also spawns in"), "no 'zones not covered' warning: every swap covers every zone")
	m._run_check()
	m._update_add_state()
	check(not m.add_btn.disabled, "Swap A->B is enabled once a race and a model are chosen")
	m._on_add_swap()
	check(m.swaps.size() == 1 and m.swap_list.item_count == 1, "swap recorded and listed")
	check(bool(m.swaps[0].get("all_zones", false)) and m.swap_list.get_item_text(0).contains("all zones"), "the swap is recorded as all zones: " + m.swap_list.get_item_text(0))
	var stz: Dictionary = backend.call_cli(["status"])
	check(stz.ok and stz.data.size() > 400, "every zone list is managed by the swap (%d files)" % stz.data.size())
	var rr: Dictionary = backend.call_cli(["races", "--filter", "cth"])
	check(rr.ok and rr.data.size() > 0 and bool(rr.data[0].swapped), "Swap A->B applied it at once: racedata reports the swap")
	var st: Dictionary = backend.call_cli(["status"])
	check(st.ok and str(st.data).contains("applied") and not str(st.data).contains("needs-apply"), "files are in sync right after swapping: " + str(st.data))
	# a patch / the launcher restores the originals: the app re-applies by itself
	backend.call_cli(["restore"])
	var rr0: Dictionary = backend.call_cli(["races", "--filter", "caz"])
	check(rr0.ok and not bool(rr0.data[0].swapped), "(simulated patch) originals are back")
	m._auto_applied = false
	m._refresh_swaps()
	var rr1: Dictionary = backend.call_cli(["races", "--filter", "cth"])
	check(rr1.ok and rr1.data.size() > 0 and bool(rr1.data[0].swapped), "the app re-applied the swap by itself after the files were restored")
	# Disable all: originals back, swap stays listed as off
	m._on_disable_all()
	var rr2: Dictionary = backend.call_cli(["races", "--filter", "caz"])
	check(rr2.ok and not bool(rr2.data[0].swapped), "Disable all put race 95 back to CAZ")
	check(m.swaps.size() == 1 and not bool(m.swaps[0].enabled) and m.swap_list.get_item_text(0).begins_with("off"), "the swap stays listed, marked off")
	# Enable: applied again
	m.swap_list.select(0)
	m._swap_action("enable")
	var rr3: Dictionary = backend.call_cli(["races", "--filter", "cth"])
	check(rr3.ok and bool(rr3.data[0].swapped) and m.swap_list.get_item_text(0).begins_with("ON"), "Enable applies the swap again")
	# Remove: gone and restored
	m.swap_list.select(0)
	m._swap_action("remove")
	var rr4: Dictionary = backend.call_cli(["races", "--filter", "caz"])
	check(m.swaps.size() == 0 and rr4.ok and not bool(rr4.data[0].swapped), "Remove deletes the swap and restores the original")

	# swapping a race to the model it already uses is an error and must disable Add swap
	m.race_filter.text = "caz"
	m._fill_races()
	var ci: int = _find_item(m.race_list, "95")
	m.race_list.select(ci)
	m._on_race_selected(ci)
	m.model_filter.text = "caz"
	m._fill_models()
	var mj: int = _find_model(m.model_list, "caz")
	m.model_list.select(mj)
	m._on_model_selected(mj)
	m._update_add_state()
	check(m.check_worst == "error" and m.add_btn.disabled, "same-model swap raises an error and disables Add swap")

	await _enhance_checks(m, backend)

	print("DONE failures=%d" % failures)
	quit(1 if failures > 0 else 0)


func _wait_idle(panel, limit_s: float) -> bool:
	var t := 0.0
	while panel.is_busy() and t < limit_s:
		await create_timer(0.5).timeout
		t += 0.5
	return not panel.is_busy()


func _wait_plan(panel, limit_s: float) -> void:
	var t := 0.0
	while str(panel._estimate.text).begins_with("working out") and t < limit_s:
		await create_timer(0.5).timeout
		t += 0.5


func _state_of(backend: Node, path: String) -> String:
	var st: Dictionary = backend.call_cli(["status"])
	for f in st.data:
		if str(f.path) == path:
			return str(f.state)
	return "absent"


## Enhance panel, end to end against the sandbox (built-in resize engine: no AI upscaler is installed there).
func _enhance_checks(m: Control, backend: Node) -> void:
	var p = m.enhance_panel
	check(p != null, "Enhance panel is part of the window")
	var ci := _find_model(m.model_list, "orc")
	m.model_filter.text = ""
	m._fill_models()
	ci = _find_model(m.model_list, "orc")
	m.model_list.select(ci)
	m._on_model_selected(ci)
	check(p._go_btn.disabled and "classic model" in str(p._target.text), "a classic (WLD) model cannot be enhanced: %s" % str(p._target.text).left(60))
	var cth := _find_model(m.model_list, "cth")
	m.model_list.select(cth)
	m._on_model_selected(cth)
	check(not p._passes.disabled and "Cazic" in str(p._target.text), "an EQG model can: %s" % str(p._target.text))
	check(p._go_btn.disabled, "nothing chosen yet: Enhance is off")
	p._passes.select(1)
	p._on_options_changed()
	await _wait_plan(p, 30.0)
	check("7,754" in str(p._estimate.text) and "12,540" in str(p._estimate.text), "plan shows the new counts: %s" % str(p._estimate.text).replace("\n", " | ").left(110))
	check(not p._go_btn.disabled and not p._preview_btn.disabled, "Enhance and Preview are enabled once the plan is fine")
	p._passes.select(0)
	p._tex.select(1)  # 512: CTH's textures are already 512, so there is nothing to do
	p._on_options_changed()
	await _wait_plan(p, 30.0)
	check(p._go_btn.disabled and "nothing to do" in str(p._estimate.text), "a choice that changes nothing is refused: %s" % str(p._estimate.text).left(70))
	p._tex.select(0)
	p._passes.select(1)
	p._on_options_changed()
	await _wait_plan(p, 30.0)

	# preview: shows the enhanced model in B, touches nothing
	p._on_preview()
	check(await _wait_idle(p, 120.0), "preview finished")
	check(m.pane_b.has_model() and m.pane_b_enhanced == "cth" and "enhanced preview" in str(m.pane_b.title), "pane B shows the enhanced preview")
	check(_state_of(backend, "cth.eqg") == "absent", "previewing recorded nothing")

	# enhance for real (in the sandbox)
	p._on_enhance()
	check(await _wait_idle(p, 180.0), "enhance finished")
	m._refresh_swaps()
	check(_state_of(backend, "cth.eqg") == "applied", "the enhanced archive is applied (status: %s)" % _state_of(backend, "cth.eqg"))
	var row := -1
	for i in m.swap_list.item_count:
		if "enhance" in m.swap_list.get_item_text(i):
			row = i
	check(row >= 0 and "4x polygons" in m.swap_list.get_item_text(row), "Recorded swaps lists it: %s" % (m.swap_list.get_item_text(row) if row >= 0 else "-"))
	# disable -> original back; enable -> back again (cached build, fast); remove -> original and gone
	m.swap_list.select(row)
	m._swap_action("disable")
	check(_state_of(backend, "cth.eqg") == "clean", "Disable puts the original archive back")
	m.swap_list.select(row)
	m._swap_action("enable")
	check(_state_of(backend, "cth.eqg") == "applied", "Enable applies the cached enhanced archive again")
	m.swap_list.select(row)
	m._swap_action("remove")
	check(_state_of(backend, "cth.eqg") == "clean" and m.swap_list.item_count == 0, "Remove restores the original and clears the list")
