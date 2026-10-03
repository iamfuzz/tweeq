extends SceneTree
## Dev tool: load one .glb into a ViewerPane and save PNGs at a few moments of its animation.
##   Godot --path <project> --script res://scripts/model_shot.gd -- <glb> <out_prefix> [yaw_deg] [anim]
func _initialize() -> void:
	call_deferred("_run")


func _run() -> void:
	var a := OS.get_cmdline_user_args()
	var glb: String = a[0]
	var prefix: String = a[1]
	var yaw: float = float(a[2]) if a.size() > 2 else 35.0
	root.size = Vector2i(700, 700)
	var pane := ViewerPane.new("")
	pane.custom_minimum_size = Vector2(700, 700)
	pane.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.add_child(pane)
	await process_frame
	var err: int = pane.set_model(glb)
	print("load err=%d" % err)
	if a.size() > 3 and pane._anim:
		pane._anim.play(a[3])
	pane._yaw = deg_to_rad(yaw)
	pane._apply_camera()
	for i in 3:
		await create_timer(1.0).timeout
		await RenderingServer.frame_post_draw
		root.get_texture().get_image().save_png("%s_%d.png" % [prefix, i])
	print("anims: %s" % str(pane._anim.get_animation_list() if pane._anim else []))
	quit(0)
