extends VBoxContainer
## "Enhance model B": more polygons and/or bigger textures for an EQG model.
## Talks to the engine only through Backend; tells the main window what happened through signals.

signal log_line(text: String, is_error: bool)
signal preview_loaded(path: String, title: String)
signal enhanced_changed

const PROGRESS_FILE := "enhance_progress.json"
const CANCEL_FILE := "enhance_cancel"

var _tag := ""
var _name := ""
var _enhanceable := false
var _blocked_reason := ""        # set by the main window (e.g. untracked edits)
var _upscaler := {}
var _plan_ok := false
var _plan_sig := ""
var _busy := false
var _can_cancel := true
var _pending_id := ""
var _stamp := 0

var _target: Label
var _passes: OptionButton
var _tex: OptionButton
var _estimate: Label
var _preview_btn: Button
var _go_btn: Button
var _cancel_btn: Button
var _up_btn: Button
var _bar: ProgressBar
var _stage: Label
var _timer: Timer


func _ready() -> void:
	add_theme_constant_override("separation", 4)
	var head := Label.new()
	head.text = "Enhance model B"
	head.add_theme_font_size_override("font_size", 15)
	add_child(head)
	_target = Label.new()
	_target.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_target.custom_minimum_size.x = 100
	add_child(_target)

	var row := HBoxContainer.new()
	add_child(row)
	var pl := Label.new()
	pl.text = "Polygons"
	pl.custom_minimum_size.x = 70
	row.add_child(pl)
	_passes = OptionButton.new()
	_passes.add_item("Unchanged", 0)
	_passes.add_item("4x more (1 pass)", 1)
	_passes.add_item("16x more (2 passes)", 2)
	_passes.item_selected.connect(func(_i: int) -> void: _on_options_changed())
	_passes.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(_passes)
	var row2 := HBoxContainer.new()
	add_child(row2)
	var tl := Label.new()
	tl.text = "Textures"
	tl.custom_minimum_size.x = 70
	row2.add_child(tl)
	_tex = OptionButton.new()
	_tex.add_item("Unchanged", 0)
	_tex.add_item("512 x 512", 512)
	_tex.add_item("1024 x 1024", 1024)
	_tex.item_selected.connect(func(_i: int) -> void: _on_options_changed())
	_tex.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row2.add_child(_tex)

	_estimate = Label.new()
	_estimate.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_estimate.custom_minimum_size = Vector2(100, 70)
	add_child(_estimate)

	var btns := HBoxContainer.new()
	add_child(btns)
	_preview_btn = Button.new()
	_preview_btn.text = "Preview in B"
	_preview_btn.pressed.connect(_on_preview)
	btns.add_child(_preview_btn)
	_go_btn = Button.new()
	_go_btn.text = "Enhance"
	_go_btn.custom_minimum_size.x = 100
	_go_btn.pressed.connect(_on_enhance)
	btns.add_child(_go_btn)
	_cancel_btn = Button.new()
	_cancel_btn.text = "Cancel"
	_cancel_btn.visible = false
	_cancel_btn.pressed.connect(_on_cancel)
	btns.add_child(_cancel_btn)

	_bar = ProgressBar.new()
	_bar.max_value = 1.0
	_bar.step = 0.001
	_bar.visible = false
	add_child(_bar)
	_stage = Label.new()
	_stage.visible = false
	add_child(_stage)
	_up_btn = Button.new()
	_up_btn.pressed.connect(_on_upscaler_button)
	add_child(_up_btn)

	_timer = Timer.new()
	_timer.wait_time = 0.4
	_timer.timeout.connect(_poll_progress)
	add_child(_timer)
	Backend.cli_done.connect(_on_cli_done)
	_refresh_state()


# ------------------------------------------------------------------ inputs from the main window
func set_target(tag: String, display: String, enhanceable: bool) -> void:
	_tag = tag
	_name = display
	_enhanceable = enhanceable
	_refresh_state()
	_request_plan()


func set_blocked(reason: String) -> void:
	_blocked_reason = reason
	_refresh_state()


func set_upscaler(info: Dictionary) -> void:
	_upscaler = info
	_up_btn.text = "Remove AI upscaler" if bool(info.get("installed", false)) else "Get AI upscaler (optional, 45 MB)"
	_request_plan()
	_refresh_state()


func is_busy() -> bool:
	return _busy


## Build enhanced archives that are wanted but not built yet (after a patch, or after installing the upscaler).
func build_pending() -> void:
	_pending_id = ""
	_begin("building ...")
	Backend.call_cli_async("enh_apply", _progress_args() + ["apply"])


# ------------------------------------------------------------------ state
func _opts() -> Dictionary:
	return {"passes": _passes.get_selected_id(), "tex": _tex.get_selected_id()}


func _chosen() -> bool:
	var o := _opts()
	return int(o.passes) > 0 or int(o.tex) > 0


func _refresh_state() -> void:
	if _tag == "":
		_target.text = "Click a model in the list to enhance it."
	elif not _enhanceable:
		_target.text = "%s is a classic model; only EQG (Luclin and later) models can be enhanced for now." % _name
	else:
		_target.text = _name
	var idle := _tag != "" and _enhanceable and not _busy
	_passes.disabled = not idle
	_tex.disabled = not idle
	var can := idle and _chosen() and _plan_ok and _blocked_reason == ""
	_preview_btn.disabled = not can
	_go_btn.disabled = not can
	_cancel_btn.visible = _busy and _can_cancel
	_up_btn.disabled = _busy
	_up_btn.visible = not _upscaler.is_empty()
	if _blocked_reason != "" and _enhanceable and _tag != "":
		_estimate.text = _blocked_reason
		_estimate.modulate = Color(1, 0.45, 0.45)


func _on_options_changed() -> void:
	_refresh_state()
	_request_plan()


func _args(extra: Array = []) -> Array:
	var o := _opts()
	var a: Array = [_tag, "--passes", str(int(o.passes)), "--tex-size", str(int(o.tex))]
	a.append_array(extra)
	return a


func _request_plan() -> void:
	_plan_ok = false
	if _tag == "" or not _enhanceable or not _chosen():
		_estimate.text = "" if (_tag == "" or not _enhanceable) else "Choose more polygons and/or a texture size."
		_estimate.modulate = Color(1, 1, 1)
		_refresh_state()
		return
	_plan_sig = "%s|%d|%d" % [_tag, int(_opts().passes), int(_opts().tex)]
	_estimate.text = "working out what this would do ..."
	_estimate.modulate = Color(0.75, 0.85, 1.0)
	Backend.call_cli_async("enh_plan:" + _plan_sig, ["enhance-plan"] + _args())


func _fmt_bytes(n: int) -> String:
	return "%.1f MB" % (float(n) / 1048576.0)


func _show_plan(d: Dictionary) -> void:
	var lines: Array = []
	for m in d.models:
		var line := "%s: %s vertices" % [m.name, _commas(int(m.verts_after))]
		if int(m.verts_after) != int(m.verts_before):
			line = "%s: %s -> %s vertices (%s faces)" % [m.name, _commas(int(m.verts_before)),
					_commas(int(m.verts_after)), _commas(int(m.faces_after))]
		lines.append(line)
	var n_tex := 0
	for t in d.textures:
		if str(t.action) == "color" or str(t.action) == "normal":
			n_tex += 1
	if n_tex > 0:
		lines.append("%d texture(s) -> %d x %d, %s" % [n_tex, int(_tex.get_selected_id()), int(_tex.get_selected_id()),
				"AI upscaler" if str(d.engine) == "esrgan" else "built-in resize"])
	lines.append("Archive %s -> up to about %s" % [_fmt_bytes(int(d.bytes_before)), _fmt_bytes(int(d.bytes_after_estimate))])
	for w in d.warnings:
		lines.append("Note: " + str(w))
	if d.blocked != null:
		lines.push_front("Cannot enhance: " + str(d.blocked))
		_estimate.modulate = Color(1, 0.45, 0.45)
		_plan_ok = false
	else:
		_estimate.modulate = Color(1, 1, 1)
		_plan_ok = true
	_estimate.text = "\n".join(lines)
	_refresh_state()


func _commas(n: int) -> String:
	var s := str(n)
	var out := ""
	for i in range(s.length()):
		if i > 0 and (s.length() - i) % 3 == 0:
			out += ","
		out += s[i]
	return out


# ------------------------------------------------------------------ actions
func _io_paths() -> Array:
	var base := str(Backend.cfg.previews_wsl).get_base_dir()
	return [base + "/" + PROGRESS_FILE, base + "/" + CANCEL_FILE]


func _begin(label: String, cancellable := true) -> void:
	_busy = true
	_can_cancel = cancellable
	_cancel_btn.disabled = false
	_bar.value = 0.0
	_bar.visible = true
	_stage.text = label
	_stage.visible = true
	var cancel_local := ProjectSettings.globalize_path("user://" + CANCEL_FILE)
	if FileAccess.file_exists(cancel_local):
		DirAccess.remove_absolute(cancel_local)
	_timer.start()
	_refresh_state()


func _end() -> void:
	_busy = false
	_timer.stop()
	_bar.visible = false
	_stage.visible = false
	_refresh_state()


func _progress_args() -> Array:
	var p := _io_paths()
	return ["--progress-file", p[0], "--cancel-file", p[1]]


func _poll_progress() -> void:
	var f := "user://" + PROGRESS_FILE
	if not FileAccess.file_exists(f):
		return
	var parsed = JSON.parse_string(FileAccess.get_file_as_string(f))
	if parsed is Dictionary:
		_bar.value = float(parsed.get("pct", 0.0))
		_stage.text = str(parsed.get("stage", ""))


func _on_cancel() -> void:
	var f := FileAccess.open("user://" + CANCEL_FILE, FileAccess.WRITE)
	if f:
		f.store_string("cancel")
	_stage.text = "cancelling ..."
	_cancel_btn.disabled = true


func _on_preview() -> void:
	_begin("starting ...")
	Backend.call_cli_async("enh_prev:" + _tag, _progress_args() + ["enhance-preview"] + _args(["--out", str(Backend.cfg.previews_wsl)]))


func _on_enhance() -> void:
	_begin("recording ...")
	Backend.call_cli_async("enh_add", ["enhance"] + _args())


func _add_with_take_over() -> void:
	_begin("recording ...")
	Backend.call_cli_async("enh_add", ["enhance"] + _args(["--take-over"]))


func _on_cli_done(key: String, r: Dictionary) -> void:
	if key.begins_with("enh_plan:"):
		if key.substr(9) != _plan_sig:
			return  # a newer choice has been made since
		if r.get("ok", false):
			_show_plan(r.data)
		else:
			_plan_ok = false
			_estimate.text = str(r.get("error", "could not work this out"))
			_estimate.modulate = Color(1, 0.45, 0.45)
			_refresh_state()
		return
	if key.begins_with("enh_prev:"):
		_end()
		var data: Dictionary = r.get("data", {}) if r.get("ok", false) else {}
		var st := str(data.get("status", "error"))
		if st == "ok" or st == "cached":
			preview_loaded.emit(Backend.to_windows_path(str(data.path)), "B: %s (enhanced preview)" % _name)
		else:
			log_line.emit("enhanced preview: %s" % str(data.get("error", r.get("error", "failed"))), true)
		return
	if key == "enh_add":
		if not r.get("ok", false):
			_end()
			var err := str(r.get("error", ""))
			if "not the original file" in err:
				_confirm_take_over(err)
			else:
				log_line.emit("enhance: " + err, true)
			return
		_pending_id = str(r.data.id)
		_stage.text = "building ..."
		Backend.call_cli_async("enh_apply", _progress_args() + ["apply"])
		return
	if key == "enh_apply":
		_end()
		if r.get("ok", false):
			log_line.emit("enhanced models are built and applied (restart EverQuest to see them)", false)
		else:
			if _pending_id != "":
				Backend.call_cli(["remove", _pending_id])  # a decision that could not be applied is not kept
			log_line.emit("could not build the enhanced model: %s" % str(r.get("error", "failed")), true)
		_pending_id = ""
		enhanced_changed.emit()
		return
	if key == "enh_up_install":
		_end()
		if r.get("ok", false):
			log_line.emit("AI upscaler installed", false)
		else:
			log_line.emit("upscaler install: " + str(r.get("error", "failed")), true)
		enhanced_changed.emit()
		return
	if key == "enh_up_remove":
		_end()
		log_line.emit("AI upscaler removed", false)
		enhanced_changed.emit()


func _confirm_take_over(err: String) -> void:
	var dlg := ConfirmationDialog.new()
	dlg.title = "Replace the modified file?"
	dlg.dialog_text = err
	dlg.ok_button_text = "Continue"
	dlg.confirmed.connect(func() -> void:
		dlg.queue_free()
		_add_with_take_over())
	dlg.canceled.connect(func() -> void: dlg.queue_free())
	add_child(dlg)
	dlg.popup_centered(Vector2i(560, 200))


func _on_upscaler_button() -> void:
	if bool(_upscaler.get("installed", false)):
		_begin("removing ...", false)
		Backend.call_cli_async("enh_up_remove", ["upscaler-remove"])
		return
	var dlg := ConfirmationDialog.new()
	dlg.title = "Get the AI upscaler?"
	dlg.dialog_text = ("Tweeq can use Real-ESRGAN for sharper textures. It is optional: without it Tweeq uses a built-in "
			+ "resize.\n\nTweeq will download about 45 MB from:\n%s\n\nand install it to:\n%s\n\nThe download is checked "
			+ "against a known checksum before anything is installed. Real-ESRGAN's program code is BSD-3 / MIT "
			+ "licensed; the AI model it ships with was trained by its authors on third-party image sets. "
			+ "It needs a Vulkan-capable graphics card.") % [str(_upscaler.get("url", "")), str(_upscaler.get("install_dir", ""))]
	dlg.ok_button_text = "Download and install"
	dlg.confirmed.connect(func() -> void:
		dlg.queue_free()
		_begin("downloading ...", false)
		Backend.call_cli_async("enh_up_install", _progress_args() + ["upscaler-install"]))
	dlg.canceled.connect(func() -> void: dlg.queue_free())
	add_child(dlg)
	dlg.popup_centered(Vector2i(640, 340))
