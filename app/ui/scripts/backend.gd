extends Node
## Bridge to the tweeq engine. Every call is one process that prints a single JSON object:
##   {"ok": true, "data": ...}  or  {"ok": false, "error": "..."}
## Dev setup: Godot runs on Windows and the engine in WSL, so the launcher is wsl.exe.
## A packaged build swaps `exe` / `prefix` for the frozen tweeq.exe; nothing else changes.

const CFG_PATH := "user://config.json"
const SETTINGS_FILE := "settings.json"   # installed app: only the user's own choices live here

## True when running as the installed app (an exported Tweeq.exe sitting next to its bundled python\). The editor
## and the WSL development setup are "not packaged" and behave exactly as before.
var packaged := false
var _data_root := ""   # installed app: the per-user data folder (%APPDATA%\Tweeq, overridable for tests)

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
	packaged = not OS.has_feature("editor") and FileAccess.file_exists(app_dir() + "/python/python.exe")
	if packaged:
		_setup_packaged()
		return
	first_run = not FileAccess.file_exists(CFG_PATH)
	load_config()


func app_dir() -> String:
	return OS.get_executable_path().get_base_dir().replace("\\", "/")


## Per-user data folder as Godot sees it (the same folder the engine is told about through engine_data_dir()).
func local_data_dir() -> String:
	if packaged and _data_root != "":
		return _data_root
	return OS.get_user_data_dir().replace("\\", "/")


## The same folder spelled the way the engine process wants it (WSL path in the dev setup, Windows path installed).
func engine_data_dir() -> String:
	if packaged:
		return local_data_dir()
	return str(cfg.previews_wsl).get_base_dir()


func _setup_packaged() -> void:
	var env_root := OS.get_environment("TWEEQ_DATA_ROOT")           # tests only
	_data_root = env_root.replace("\\", "/") if env_root != "" else OS.get_user_data_dir().replace("\\", "/")
	var app := app_dir()
	cfg.exe = app + "/python/python.exe"
	cfg.prefix = ["-X", "utf8", "-B", "-m", "tweeq.cli"]
	cfg.mode = "real"
	cfg.vanilla = ""
	cfg.peq = app + "/data/peq_slim.sqlite"
	cfg.previews = _data_root + "/previews"
	cfg.previews_wsl = _data_root + "/previews"
	cfg.real_vault = _data_root + "/vault"
	cfg.vault = _data_root + "/vault"
	cfg.eq = ""
	cfg.models = ""
	cfg.index = ""
	for pair in [["TWEEQ_SANDBOX_EQ", "sandbox_eq"], ["TWEEQ_SANDBOX_VAULT", "sandbox_vault"]]:   # tests only
		if OS.get_environment(pair[0]) != "":
			cfg[pair[1]] = OS.get_environment(pair[0]).replace("\\", "/")
	OS.set_environment("TWEEQ_LOG", _data_root + "/logs/engine.log")
	var sp := _data_root + "/" + SETTINGS_FILE
	first_run = not FileAccess.file_exists(sp)
	if not first_run:
		var parsed = JSON.parse_string(FileAccess.get_file_as_string(sp))
		if parsed is Dictionary and str(parsed.get("eq", "")) != "":
			set_install(to_windows_path(str(parsed.eq)))


## Point the app at an EverQuest folder: its own vault, and (installed app) its own scan of the model files.
func set_install(path: String) -> void:
	var p := path.strip_edges().replace("\\", "/") if packaged else to_engine_path(path)
	cfg.eq = p
	cfg.real_eq = p
	cfg.models = p
	cfg.vault = vault_for(p)
	if packaged:
		cfg.index = "%s/installs/%s/installed_model_index.json" % [local_data_dir(), install_id(p)]


func load_config() -> void:
	if not FileAccess.file_exists(CFG_PATH):
		return
	var parsed = JSON.parse_string(FileAccess.get_file_as_string(CFG_PATH))
	if parsed is Dictionary:
		cfg.merge(parsed, true)


func save_config() -> void:
	if packaged:   # only the user's own choice; everything else is derived from where the app is installed
		DirAccess.make_dir_recursive_absolute(local_data_dir())
		var fp := FileAccess.open(local_data_dir() + "/" + SETTINGS_FILE, FileAccess.WRITE)
		if fp:
			fp.store_string(JSON.stringify({"eq": str(cfg.eq)}, "  "))
		return
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
	if p.length() > 2 and p[1] == ":":   # C:/x -> C:\x
		return p.replace("/", "\\")
	return p


## Comparable spelling of an install path (matches the engine's norm_dir): /mnt/c/... lower case, no trailing slash.
func canon_path(p: String) -> String:
	p = p.strip_edges().replace("\\", "/").rstrip("/")
	if p.length() >= 3 and p[1] == ":" and p[2] == "/":
		p = "/mnt/%s/%s" % [p[0].to_lower(), p.substr(3)]
	return p.to_lower()


## Short stable id of an install (same value the dev setup always used for its per-install folders).
func install_id(path: String) -> String:
	return canon_path(path).sha1_text().left(8)


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


## One vault per install. The vault this machine already has (a manifest whose recorded install is this one) is
## reused, so existing swaps stay attached; every other install gets its own vault_<id> next to it.
const DEFAULT_REAL_EQ := "/mnt/c/Users/Public/Daybreak Game Company/Installed Games/EverQuest"   # dev setup only


func is_default_install(path: String) -> bool:
	return canon_path(path) == DEFAULT_REAL_EQ.to_lower()


func vault_for(path: String) -> String:
	var local := local_data_dir()
	var engine := engine_data_dir()
	var legacy_manifest := local + "/vault/manifest.json"
	if FileAccess.file_exists(legacy_manifest):
		var m = JSON.parse_string(FileAccess.get_file_as_string(legacy_manifest))
		if m is Dictionary and canon_path(str(m.get("eq_dir", ""))) == canon_path(path):
			return engine + "/vault"
	if not packaged and is_default_install(path):
		return str(cfg.real_vault)
	return "%s/vault_%s" % [engine, install_id(path)]


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
