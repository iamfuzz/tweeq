# Third-party components

| Component | License | Use |
|---|---|---|
| Godot Engine 4.x | MIT | UI runtime (https://godotengine.org/license) |
| quail (xackery/quail) | MIT | WLD/S3D/EQG conversion; used as an external tool |
| Microsoft texconv (DirectXTex) | MIT | DDS conversion (when bundled in a release build) |
| Real-ESRGAN (xinntao/Real-ESRGAN) and Real-ESRGAN-ncnn-vulkan | BSD-3-Clause / MIT | Optional texture upscaler for Enhance. Not included here: downloaded on request from the official release (v0.2.5.0) and checked against a pinned sha256. Its model weights were trained on DIV2K, Flickr2K and OST300; DIV2K is licensed "for academic research purposes only", so whether the weights may be bundled in a commercial product is unresolved. |
| Pillow | HPND | Image and DDS handling |
| NumPy | BSD-3-Clause | Image maths |
| PyInstaller | GPL-2.0 with bootloader exception | Packaging, if used for a release build |
| EQEmu / ProjectEQ database | GPL | Slim extract shipped as `data/peq_slim.sqlite` (https://projecteq.net, https://github.com/EQEmu) |

When a release build bundles Godot, quail or texconv, their full license texts must ship alongside it.
