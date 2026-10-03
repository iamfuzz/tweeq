extends SceneTree
## Windowed run that saves a PNG of the real UI:
##   Godot --path <project> --script res://scripts/screenshot.gd -- <zone> <out.png>
## Not headless: needs a display. Used to eyeball the MVP flow.

func _initialize() -> void:
	call_deferred("_run")


func _run() -> void:
	var args := OS.get_cmdline_user_args()
	var zone: String = args[0] if args.size() > 0 else "gfaydark"
	var out: String = args[1] if args.size() > 1 else "C:/Users/brian/tweeq_ui/shot.png"
	root.size = Vector2i(1900, 1040)  # roughly what a maximized window is on a 1080p monitor
	var be = root.get_node("Backend")  # screenshots always use the sandbox
	be.first_run = false
	be.cfg.mode = "sandbox"
	be.cfg.eq = be.cfg.sandbox_eq
	be.cfg.vault = be.cfg.sandbox_vault
	var m: Control = (load("res://scenes/main.tscn") as PackedScene).instantiate()
	root.add_child(m)
	await process_frame
	m.zpick_filter.text = zone
	m._fill_zpick()
	for i in m.zpick_list.item_count:
		if m.zpick_list.get_item_text(i) == zone:
			m.zpick_list.select(i)
			m._on_zone_picked(i)
			break
	var t := 0.0
	while t < 120.0 and not (m.pane_a.has_model() and m.pane_b.has_model()):
		await create_timer(0.5).timeout
		t += 0.5
	t = 0.0
	var backend = root.get_node("Backend")
	while t < 120.0 and not backend._pending.is_empty():  # background revalidation / conversions
		await create_timer(0.5).timeout
		t += 0.5
	await create_timer(0.5).timeout
	for i in 20:  # let animations advance and the cameras settle
		await process_frame
	await create_timer(1.0).timeout
	await RenderingServer.frame_post_draw
	var img := root.get_texture().get_image()
	var err := img.save_png(out)
	print("SHOT %s err=%d size=%s models=%s/%s tags=%s/%s t=%.1f" % [out, err, img.get_size(),
			m.pane_a.has_model(), m.pane_b.has_model(), m.pane_want.get(m.pane_a), m.pane_want.get(m.pane_b), t])
	quit(0)
