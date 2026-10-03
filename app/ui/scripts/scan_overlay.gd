extends Control
## Full-window "one-time scan" screen: builds the model list for the chosen EverQuest folder.
## Talks to the engine only through Backend (command `scan`, progress + cancel through small files).

signal finished(ok: bool, message: String)

const PROGRESS_FILE := "scan_progress.json"
const CANCEL_FILE := "scan_cancel"

var _bar: ProgressBar
var _stage: Label
var _cancel: Button
var _timer: Timer
var _running := false


func _ready() -> void:
	visible = false
	set_anchors_preset(Control.PRESET_FULL_RECT)
	mouse_filter = Control.MOUSE_FILTER_STOP     # nothing behind the overlay can be clicked
	var dim := ColorRect.new()
	dim.color = Color(0, 0, 0, 0.72)
	dim.set_anchors_preset(Control.PRESET_FULL_RECT)
	add_child(dim)
	var center := CenterContainer.new()
	center.set_anchors_preset(Control.PRESET_FULL_RECT)
	add_child(center)
	var panel := PanelContainer.new()
	panel.custom_minimum_size = Vector2(560, 0)
	center.add_child(panel)
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 10)
	panel.add_child(box)
	var title := Label.new()
	title.text = "Getting Tweeq ready for your EverQuest folder"
	title.add_theme_font_size_override("font_size", 18)
	box.add_child(title)
	var sub := Label.new()
	sub.text = "Tweeq is reading your model files to find every character model. This can take a few minutes, and only happens the first time and after a game patch. Nothing in your game is changed."
	sub.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	sub.custom_minimum_size.x = 520
	box.add_child(sub)
	_bar = ProgressBar.new()
	_bar.max_value = 1.0
	_bar.step = 0.001
	box.add_child(_bar)
	_stage = Label.new()
	box.add_child(_stage)
	_cancel = Button.new()
	_cancel.text = "Cancel"
	_cancel.pressed.connect(_on_cancel)
	box.add_child(_cancel)
	_timer = Timer.new()
	_timer.wait_time = 0.25
	_timer.timeout.connect(_poll)
	add_child(_timer)
	Backend.cli_done.connect(_on_cli_done)


func start() -> void:
	if _running:
		return
	_running = true
	_bar.value = 0.0
	_stage.text = "starting ..."
	_cancel.disabled = false
	visible = true
	var cancel_local := Backend.local_data_dir() + "/" + CANCEL_FILE
	if FileAccess.file_exists(cancel_local):
		DirAccess.remove_absolute(cancel_local)
	DirAccess.make_dir_recursive_absolute(Backend.local_data_dir())
	var base := Backend.engine_data_dir()
	_timer.start()
	Backend.call_cli_async("scan", ["--progress-file", base + "/" + PROGRESS_FILE, "--cancel-file", base + "/" + CANCEL_FILE, "scan"])


func is_running() -> bool:
	return _running


func _poll() -> void:
	var f := Backend.local_data_dir() + "/" + PROGRESS_FILE
	if not FileAccess.file_exists(f):
		return
	var parsed = JSON.parse_string(FileAccess.get_file_as_string(f))
	if parsed is Dictionary:
		_bar.value = float(parsed.get("pct", 0.0))
		_stage.text = str(parsed.get("stage", "")).capitalize()


func _on_cancel() -> void:
	var f := FileAccess.open(Backend.local_data_dir() + "/" + CANCEL_FILE, FileAccess.WRITE)
	if f:
		f.store_string("cancel")
	_stage.text = "Cancelling ..."
	_cancel.disabled = true


func _on_cli_done(key: String, r: Dictionary) -> void:
	if key != "scan":
		return
	_running = false
	_timer.stop()
	visible = false
	if r.get("ok", false):
		var d: Dictionary = r.data
		finished.emit(true, "Model list ready: %d models%s." % [int(d.get("models", 0)),
				"" if not bool(d.get("skipped", false)) else " (already up to date)"])
	else:
		finished.emit(false, str(r.get("error", "the scan failed")))
