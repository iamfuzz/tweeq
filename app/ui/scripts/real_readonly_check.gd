extends SceneTree
## READ-ONLY check of the window in its default (real install) mode: never calls apply/restore/swap.
var failures := 0


func check(cond: bool, what: String) -> void:
	print(("PASS  " if cond else "FAIL  ") + what)
	if not cond:
		failures += 1


func _initialize() -> void:
	call_deferred("_run")


func _run() -> void:
	var backend = root.get_node("Backend")
	backend.first_run = false  # read-only check: no discovery, no config writes
	check(not str(backend.cfg.eq).contains("sandbox"), "default mode points at the real install: " + str(backend.cfg.eq))
	var m: Control = (load("res://scenes/main.tscn") as PackedScene).instantiate()
	root.add_child(m)
	await process_frame
	check(str(m.eq_edit.text).contains("EverQuest") and not str(m.eq_edit.text).contains("sandbox"), "folder box shows the real EverQuest folder")
	check(not m.mode_label.visible, "no mode banner, no warning (nothing untracked)")
	check(m.unmanaged.is_empty() and not m.adopt_btn.visible, "no untracked edits (adopted)")
	check(m.swaps.size() == 1 and int(m.swaps[0].race) == 95 and str(m.swaps[0].model_tag) == "CTH", "the PoTime Cazic swap is listed")
	check(m.swap_list.item_count == 1, "swap shown in the Recorded swaps list: " + (m.swap_list.get_item_text(0) if m.swap_list.item_count > 0 else ""))
	check(m.drifted.is_empty(), "no drifted files")
	check(m.get("files_list") == null and m.launch_btn != null, "Files box gone, launch button present (never pressed here)")
	check(m.model_list.item_count > 800, "model list loaded (%d) from the real install" % m.model_list.item_count)
	print("DONE failures=%d" % failures)
	quit(1 if failures > 0 else 0)
