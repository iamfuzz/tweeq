# Third-party components

The Windows installer bundles the components below. Their full license texts are installed in the `licenses` folder
next to Tweeq, and are reproduced by `release/build_release.sh` from the exact versions that were built.

| Component | Version | License | Use |
|---|---|---|---|
| Godot Engine | 4.7.2 | MIT (plus its bundled third-party notices, see `godot-COPYRIGHT.txt`) | The Tweeq window (`Tweeq.exe` is the Godot runtime with Tweeq's project embedded) |
| CPython (embeddable distribution) | 3.14.8 | PSF License | Runs Tweeq's engine; installed privately under `python\` |
| NumPy | 2.5.2 | BSD-3-Clause (and the licenses of the libraries it ships) | Image maths |
| Pillow | 12.3.0 | HPND | Image and DDS handling |
| quail (xackery/quail), built from source | 1.6.0 | MIT | WLD/S3D/EQG conversion; used as an external tool (`bin\quail.exe`) |
| Go standard library, spf13/cobra, spf13/pflag, xackery/encdec, inconshreveable/mousetrap | as built into quail | BSD-3-Clause / Apache-2.0 / MIT | Linked into `quail.exe` |
| EQEmu / ProjectEQ database | extract | GPL | Slim extract shipped as `data/peq_slim.sqlite` (https://projecteq.net, https://github.com/EQEmu) |

## Not bundled
| Component | License | Notes |
|---|---|---|
| Real-ESRGAN (xinntao/Real-ESRGAN) and Real-ESRGAN-ncnn-vulkan | BSD-3-Clause / MIT | Optional texture upscaler for Enhance. Downloaded on request from the official release (v0.2.5.0) and checked against a pinned sha256. Its model weights were trained on DIV2K, Flickr2K and OST300; DIV2K is licensed "for academic research purposes only", so whether the weights may be bundled in a commercial product is unresolved. |

## Game files
Tweeq contains no EverQuest game files. It reads and edits the copy of EverQuest you already have installed, and keeps
your original files in `%APPDATA%\Tweeq` so every change can be undone. Tweeq is not affiliated with or endorsed by
Daybreak Game Company; EverQuest is a trademark of Daybreak Game Company LLC.
