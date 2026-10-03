extends SceneTree
## Simulates the very first launch (no config file): auto-discovery of the EverQuest folder, then a usable
## window. READ-ONLY on the game (never applies anything). Removes the config it creates afterwards.
var failures := 0


func check(cond: bool, what: String) -> void:
	print(("PASS  " if cond else "FAIL  ") + what)
	if not cond:
		failures += 1


func _initialize() -> void:
	call_deferred("_run")


func _run() -> void:
	var backend = root.get_node("Backend")
	var cfg_file := ProjectSettings.globalize_path("user://config.json")
	# Set the user's real config aside (restored at the end) so this always starts as a true first launch.
	var saved_cfg := ""
	if FileAccess.file_exists("user://config.json"):
		saved_cfg = FileAccess.get_file_as_string("user://config.json")
		DirAccess.remove_absolute(cfg_file)
		backend.first_run = true
	check(not FileAccess.file_exists("user://config.json"), "no config file exists (true first launch)")
	check(backend.first_run, "Backend knows it is the first run")
	var m: Control = (load("res://scenes/main.tscn") as PackedScene).instantiate()
	root.add_child(m)
	var t := 0.0
	while t < 90.0 and backend.first_run:
		await create_timer(0.5).timeout
		t += 0.5
	await create_timer(1.0).timeout
	check(not backend.first_run, "discovery finished after %.1fs" % t)
	check(str(backend.cfg.eq) == "/mnt/c/Users/Public/Daybreak Game Company/Installed Games/EverQuest", "discovered the install: " + str(backend.cfg.eq))
	check(str(m.eq_edit.text) == str(backend.cfg.eq), "folder box shows it")
	check(not m.mode_label.visible, "no mode banner, no warning (nothing untracked)")
	check(m.swaps.size() == 1 and int(m.swaps[0].race) == 95, "the adopted PoTime Cazic swap is listed (same vault as before)")
	check(str(backend.cfg.vault).ends_with("/Tweeq/vault"), "the original install keeps its original vault: " + str(backend.cfg.vault))
	check(m.race_list.item_count > 500 and m.model_list.item_count > 800, "lists populated (%d races, %d models)" % [m.race_list.item_count, m.model_list.item_count])
	# a different install gets its OWN vault and no vanilla guard
	check(backend.vault_for("D:\\Games\\EverQuest") != backend.cfg.real_vault and str(backend.vault_for("D:\\Games\\EverQuest")).contains("vault_"), "another install gets its own vault: " + backend.vault_for("D:\\Games\\EverQuest"))
	check(backend.is_default_install("C:\\Users\\Public\\Daybreak Game Company\\Installed Games\\EverQuest"), "Windows spelling of the default install is recognised")
	# typing a path validates it first
	var before: String = str(backend.cfg.eq)
	m._on_path_typed("C:\\Windows")
	check(str(backend.cfg.eq) == before, "typing a non-EverQuest folder is rejected and changes nothing")
	m._on_path_typed("C:\\Users\\Public\\Daybreak Game Company\\Installed Games\\EverQuest")
	check(str(backend.cfg.eq) == before and not m.mode_label.visible, "typing the real folder (Windows spelling) is accepted")
	# several installs -> a chooser dialog is built without error
	m._choose_install([{"path": "/mnt/c/a", "display": "C:\\a", "source": "x"}, {"path": "/mnt/d/b", "display": "D:\\b", "source": "y"}])
	await process_frame
	var dlg_found := false
	for c in m.get_children():
		if c is ConfirmationDialog:
			dlg_found = true
			c.queue_free()
	check(dlg_found, "chooser dialog appears when several installs are found")
	# leave no trace: put the user's config back exactly as it was (or remove ours if there was none)
	if FileAccess.file_exists("user://config.json"):
		DirAccess.remove_absolute(cfg_file)
	if saved_cfg != "":
		var f := FileAccess.open("user://config.json", FileAccess.WRITE)
		f.store_string(saved_cfg)
		f.close()
	check(FileAccess.file_exists("user://config.json") == (saved_cfg != ""), "the user's config is back as it was")
	print("DONE failures=%d" % failures)
	quit(1 if failures > 0 else 0)
