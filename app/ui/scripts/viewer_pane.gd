class_name ViewerPane
extends Control
## One isolated 3D model pane (own World3D, own input), loadable at runtime from any .glb.
## Two of these side by side give the A/B comparison; dragging one never moves the other
## because each pane's mouse overlay only receives events inside its own rect.

var title := ""
var _vp: SubViewport
var _pivot: Node3D
var _cam: Camera3D
var _holder: Node3D
var _model: Node = null
var _msg: Label
var _title_label: Label
var _dragging := false
var _yaw := 0.6
var _pitch := -0.25
var _dist := 6.0
var _target := Vector3(0, 1.5, 0)
var _anim: AnimationPlayer = null


func _init(t: String = "") -> void:
	title = t


func _ready() -> void:
	if custom_minimum_size == Vector2.ZERO:
		custom_minimum_size = Vector2(250, 200)
	var svc := SubViewportContainer.new()
	svc.stretch = true
	svc.set_anchors_preset(Control.PRESET_FULL_RECT)
	svc.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(svc)
	_vp = SubViewport.new()
	_vp.own_world_3d = true
	_vp.size = Vector2i(250, 200)
	svc.add_child(_vp)
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color(0.11, 0.12, 0.14)
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.7, 0.7, 0.75)
	var we := WorldEnvironment.new()
	we.environment = env
	_vp.add_child(we)
	var light := DirectionalLight3D.new()
	light.rotation_degrees = Vector3(-40, 35, 0)
	_vp.add_child(light)
	_holder = Node3D.new()
	_vp.add_child(_holder)
	_pivot = Node3D.new()
	_vp.add_child(_pivot)
	_cam = Camera3D.new()
	_pivot.add_child(_cam)
	var overlay := Control.new()  # captures this pane's mouse input only
	overlay.set_anchors_preset(Control.PRESET_FULL_RECT)
	overlay.mouse_filter = Control.MOUSE_FILTER_STOP
	overlay.gui_input.connect(_on_gui_input)
	add_child(overlay)
	_title_label = Label.new()
	_title_label.text = title
	_title_label.position = Vector2(6, 2)
	_title_label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(_title_label)
	_msg = Label.new()
	_msg.set_anchors_preset(Control.PRESET_CENTER)
	_msg.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_msg.modulate = Color(1, 1, 1, 0.6)
	add_child(_msg)
	_apply_camera()
	show_message("no model")


func show_message(text: String) -> void:
	_msg.text = text
	_msg.visible = text != ""


func set_title(t: String) -> void:
	title = t
	if _title_label:
		_title_label.text = t


## Idempotent: tears down the previous instance (nodes + animation player) before loading.
## Returns OK or an Error code. `path` is an absolute filesystem path to a .glb/.gltf.
func set_model(path: String) -> int:
	clear_model()
	var doc := GLTFDocument.new()
	var state := GLTFState.new()
	var err := doc.append_from_file(path, state)
	if err != OK:
		show_message("load failed (%d)" % err)
		return err
	var scene := doc.generate_scene(state)
	if scene == null:
		show_message("no scene in file")
		return ERR_INVALID_DATA
	_holder.add_child(scene)
	_model = scene
	_anim = _find_anim(scene)
	if _anim and _anim.get_animation_list().size() > 0:
		var names := _anim.get_animation_list()
		var pick: StringName = names[0]
		for n in names:
			var l := String(n).to_lower()
			if "idle" in l or "stnd" in l:
				pick = n
				break
		_anim.get_animation(pick).loop_mode = Animation.LOOP_LINEAR
		_anim.play(pick)
	_frame(scene)
	show_message("")
	return OK


func clear_model() -> void:
	if _model:
		_model.queue_free()
		_model = null
	_anim = null


func has_model() -> bool:
	return _model != null


func _find_anim(n: Node) -> AnimationPlayer:
	if n is AnimationPlayer:
		return n
	for c in n.get_children():
		var r := _find_anim(c)
		if r:
			return r
	return null


func _collect_aabb(n: Node, acc: Dictionary) -> void:
	if n is MeshInstance3D:
		var mi := n as MeshInstance3D
		var box: AABB = mi.global_transform * mi.get_aabb()
		acc["box"] = (acc["box"] as AABB).merge(box) if acc.has("box") else box
	for c in n.get_children():
		_collect_aabb(c, acc)


func _frame(scene: Node) -> void:
	var acc := {}
	_collect_aabb(scene, acc)
	if not acc.has("box"):
		return
	var box: AABB = acc["box"]
	_target = box.get_center()
	# fit the model's height (vertical FOV 75deg: visible height = 1.53 * distance) plus its depth
	# fit height AND horizontal reach (long, low models like raptors), plus depth margin
	var horiz := maxf(box.size.x, box.size.z)
	_dist = maxf(maxf(box.size.y * 0.8, horiz * 0.75) + horiz * 0.5, 1.0)
	_apply_camera()


func _apply_camera() -> void:
	if _pivot == null:
		return
	_pivot.position = _target
	_pivot.rotation = Vector3(_pitch, _yaw, 0)
	_cam.position = Vector3(0, 0, _dist)


func _on_gui_input(ev: InputEvent) -> void:
	if ev is InputEventMouseButton:
		var mb: InputEventMouseButton = ev
		if mb.button_index == MOUSE_BUTTON_LEFT:
			_dragging = mb.pressed
		elif mb.pressed and mb.button_index == MOUSE_BUTTON_WHEEL_UP:
			_dist = maxf(_dist * 0.9, 0.5)
			_apply_camera()
		elif mb.pressed and mb.button_index == MOUSE_BUTTON_WHEEL_DOWN:
			_dist *= 1.1
			_apply_camera()
	elif ev is InputEventMouseMotion and _dragging:
		var mm: InputEventMouseMotion = ev
		_yaw -= mm.relative.x * 0.01
		_pitch = clampf(_pitch - mm.relative.y * 0.01, -1.4, 1.4)
		_apply_camera()
