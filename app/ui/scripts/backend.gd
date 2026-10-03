extends Node
## Bridge to the tweeq engine. Every call is one process that prints a single JSON object:
##   {"ok": true, "data": ...}  or  {"ok": false, "error": "..."}
## Dev setup: Godot runs on Windows and the engine in WSL, so the launcher is wsl.exe.
## A packaged build swaps `exe` / `prefix` for the frozen tweeq.exe; nothing else changes.

const CFG_PATH := "user://config.json"

## Emitted (on the main thread) when a background engine call started with call_cli_async finishes.
signal cli_done(key: String, result: Dictionary)

## True when no config file existed at startup: the UI then auto-discovers the EverQuest folder.
var first_run := false

## Emitted (on the main thread) when a background preview conversion finishes.
signal preview_ready(tag: String, result: Dictionary)

var _pending := {}
var _threads: Array[Thread] = []

var cfg := {
	"exe": "wsl.exe",
	"prefix": ["-d", "Ubuntu", "--cd", "/home/brian/eq/app", "-e", "python3", "-m", "tweeq.cli"],
	"mode": "real",  # "real" = the user's EverQuest folder, "sandbox" = vanilla test copy
	"eq": "/mnt/c/Users/Public/Daybreak Game Company/Installed Games/EverQuest",
	"vault": "/mnt/c/Users/brian/AppData/Roaming/Tweeq/vault",
	"real_eq": "/mnt/c/Users/Public/Daybreak Game Company/Installed Games/EverQuest",
	"real_vault": "/mnt/c/Users/brian/AppData/Roaming/Tweeq/vault",
	"sandbox_eq": "/home/brian/eq/app/sandbox/EverQuest",
	"sandbox_vault": "/home/brian/eq/app/sandbox/vault",
	"vanilla": "/mnt/c/Users/brian/EQ_Launcher/backups/vanilla",
	"index": "/home/brian/eq/data/installed_model_index.json",
	"peq": "/home/brian/eq/data/peq_slim.sqlite",
	"models": "/mnt/c/Users/Public/Daybreak Game Company/Installed Games/EverQuest",
	"previews": "user://previews",
	"previews_wsl": "/mnt/c/Users/brian/AppData/Roaming/Tweeq/previews",
}


func _ready() -> void:
	first_run = not FileAccess.file_exists(CFG_PATH)
	load_config()


func load_config() -> void:
	if not FileAccess.file_exists(CFG_PATH):
		return
	var parsed = JSON.parse_string(FileAccess.get_file_as_string(CFG_PATH))
	if parsed is Dictionary:
		cfg.merge(parsed, true)


func save_config() -> void:
	var f := FileAccess.open(CFG_PATH, FileAccess.WRITE)
	f.store_string(JSON.stringify(cfg, "  "))


## The dev launcher runs the engine inside WSL, so a typed Windows path (C:\\Users\\...) must become /mnt/c/Users/...
## A packaged build calls the engine natively and passes paths through unchanged.
func to_engine_path(p: String) -> String:
	p = p.strip_edges()
	if str(cfg.exe) == "wsl.exe" and p.length() >= 3 and p[1] == ":" and (p[2] == "\\" or p[2] == "/"):
		return "/mnt/%s/%s" % [p[0].to_lower(), p.substr(3).replace("\\", "/")]
	return p


## Windows spelling of an engine path (/mnt/c/Users/... -> C:\\Users\\...). Windows paths pass through.
func to_windows_path(p: String) -> String:
	p = p.strip_edges()
	if p.length() > 6 and p.begins_with("/mnt/") and p[6] == "/":
		return "%s:\\%s" % [p[5].to_upper(), p.substr(7).replace("/", "\\")]
	return p


## Start EverQuest the way the Dragon launcher does: eqgame.exe patchme, working directory = the install
## (patchme skips the patcher so modified files survive). Returns "started", "running" or "missing".
func launch_eq() -> String:
	var dir := to_windows_path(str(cfg.eq)).rstrip("\\")
	var exe := dir + "\\eqgame.exe"
	var q := func(s: String) -> String: return s.replace("'", "''")
	var script := "if (Get-Process eqgame -ErrorAction SilentlyContinue) { 'running' } " \
			+ "elseif (Test-Path -LiteralPath '%s') { Start-Process -FilePath '%s' -ArgumentList 'patchme' -WorkingDirectory '%s'; 'started' } " \
			+ "else { 'missing' }"
	script = script % [q.call(exe), q.call(exe), q.call(dir)]
	var out: Array = []
	OS.execute("powershell.exe", PackedStringArray(["-NoProfile", "-Command", script]), out, true)
	return "".join(out).strip_edges().split("\n")[-1].strip_edges()


## Run one engine command. Blocking (calls are sub-second); returns {"ok", "data"|"error"}.
func call_cli(args: Array, with_install := true) -> Dictionary:
	var argv: Array = cfg.prefix.duplicate()
	if not with_install:  # e.g. `discover`: it is how an install is found, so it needs none
		argv.append("--json")
		argv.append_array(args)
		return _run(argv)
	argv.append_array(["--eq", to_engine_path(str(cfg.eq)), "--vault", to_engine_path(str(cfg.vault)), "--json"])
	if str(cfg.vanilla) != "":
		argv.append_array(["--vanilla", to_engine_path(str(cfg.vanilla))])
	if str(cfg.index) != "":
		argv.append_array(["--index", cfg.index])
	if str(cfg.peq) != "":
		argv.append_array(["--peq", cfg.peq])
	if str(cfg.models) != "":
		argv.append_array(["--models", to_engine_path(str(cfg.models))])
	argv.append_array(args)
	return _run(argv)


func _run(argv: Array) -> Dictionary:
	var out: Array = []
	var code := OS.execute(str(cfg.exe), PackedStringArray(argv), out, true)
	if code == -1:
		return {"ok": false, "error": "could not launch %s" % cfg.exe}
	var lines := "".join(out).split("\n")
	for i in range(lines.size() - 1, -1, -1):  # the JSON object is the last line that starts with {
		var ln := lines[i].strip_edges()
		if ln.begins_with("{"):
			var parsed = JSON.parse_string(ln)
			if parsed is Dictionary:
				return parsed
	return {"ok": false, "error": "engine gave no JSON (exit %d): %s" % [code, "".join(out).left(300)]}


## Run any engine call on a worker thread; the result arrives via cli_done(key, result).
func call_cli_async(key: String, args: Array, with_install := true) -> void:
	var t := Thread.new()
	_threads.append(t)
	t.start(_async_worker.bind(key, args, with_install, t))


func _async_worker(key: String, args: Array, with_install: bool, t: Thread) -> void:
	var r := call_cli(args, with_install)
	call_deferred("_async_done", key, r, t)


func _async_done(key: String, r: Dictionary, t: Thread) -> void:
	t.wait_to_finish()
	_threads.erase(t)
	cli_done.emit(key, r)


## One vault per install (a vault belongs to the install it was created for). The original install keeps
## its legacy vault so existing swaps stay attached to it.
const DEFAULT_REAL_EQ := "/mnt/c/Users/Public/Daybreak Game Company/Installed Games/EverQuest"


func is_default_install(path: String) -> bool:
	return to_engine_path(path).to_lower().rstrip("/") == DEFAULT_REAL_EQ.to_lower()


func vault_for(path: String) -> String:
	if is_default_install(path):
		return str(cfg.real_vault)
	var root := str(cfg.real_vault).get_base_dir()
	return "%s/vault_%s" % [root, to_engine_path(path).to_lower().sha1_text().left(8)]


## Build/cache a preview .glb for a model tag on a worker thread; result arrives via preview_ready.
func ensure_preview_async(tag: String) -> void:
	tag = tag.to_lower()
	if _pending.has(tag):
		return
	_pending[tag] = true
	var t := Thread.new()
	_threads.append(t)
	t.start(_preview_worker.bind(tag, t))


func _preview_worker(tag: String, t: Thread) -> void:
	var r := call_cli(["preview", tag, "--out", str(cfg.previews_wsl)])
	call_deferred("_preview_done", tag, r, t)


func _preview_done(tag: String, r: Dictionary, t: Thread) -> void:
	t.wait_to_finish()
	_threads.erase(t)
	_pending.erase(tag)
	preview_ready.emit(tag, r)


func preview_path(tag: String) -> String:
	var p := "%s/%s.glb" % [str(cfg.previews), tag.to_lower()]
	var real := ProjectSettings.globalize_path(p) if (p.begins_with("res://") or p.begins_with("user://")) else p
	return real if FileAccess.file_exists(real) else ""
