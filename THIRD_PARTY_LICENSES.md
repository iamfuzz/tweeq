# Third-party components

| Component | License | Use |
|---|---|---|
| Godot Engine 4.x | MIT | UI runtime (https://godotengine.org/license) |
| quail (xackery/quail) | MIT | WLD/S3D/EQG conversion; used as an external tool |
| Microsoft texconv (DirectXTex) | MIT | DDS conversion (when bundled in a release build) |
| PyInstaller | GPL-2.0 with bootloader exception | Packaging, if used for a release build |
| EQEmu / ProjectEQ database | GPL | Dev/data source; not included in this repository |

When a release build bundles Godot, quail or texconv, their full license texts must ship alongside it.
