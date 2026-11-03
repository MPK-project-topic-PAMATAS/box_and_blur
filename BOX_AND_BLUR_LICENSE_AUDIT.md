# Box & Blur licensing audit

Reviewed 3 November 2026. Scope is the current review application, its local
model integrations, and the dependencies used to run and set them up.
The dataset workspace also contains separate research scripts and data.

## Release recommendation

Use AGPL-3.0 for the integrated application source. The application imports
Ultralytics in-process for two detectors, and the installed Ultralytics and
Ultralytics THOP distributions declare AGPL-3.0. Ultralytics publishes
separate enterprise terms for proprietary integration.
[Ultralytics source license](https://github.com/ultralytics/ultralytics/blob/v8.3.253/LICENSE) ·
[Ultralytics licensing guidance](https://www.ultralytics.com/license)

Release a source-only repository with dependency installation instructions.
Keep local virtual environments, GPU libraries, model caches, and datasets
outside the release. Preserve upstream notices for any third-party files
included. Use a separate model inventory with publisher links and terms.
Add `CITATION.cff` for academic attribution.

## Model files

| Local asset | Evidence and release decision |
| --- | --- |
| Road detector, YOLO11s `best.pt` | The exact local checkpoint carries an Ultralytics AGPL-3.0 metadata tag and records `yolo11s.pt` as its training base. Confirm ownership and release authority for the fine-tune before uploading. The public project licenses its code under AGPL-3.0 and documents an external weights path. [Road detector project](https://github.com/eldux/road-irregularities-detection-characterization-tracking) |
| Dashcam YOLOv8 `dashcam.pt` | The fork's MIT code license names Varun Gupta. The checkpoint is separately hosted on Drive and its training metadata names a YOLOv8l base. I found no explicit license grant for redistribution of that exact binary. Retain the MIT notice if incorporating fork code, and get clarification for the weights before mirroring. [Fork license](https://raw.githubusercontent.com/eldux/dashcam_anonymizer/master/LICENSE) · [Fork setup](https://raw.githubusercontent.com/eldux/dashcam_anonymizer/master/setup.sh) |
| YuNet 2023mar `yunet.onnx` | OpenCV Zoo explicitly places all files in its YuNet model directory under MIT. The released model can be included with the model directory's original copyright and license notice. [Model directory](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet#license) |
| YOLOv9-t-640 `plates.onnx` | The Open Image Models software is MIT. Its separate model release supplies the exact ONNX asset, but I found no explicit terms for that asset in the release or embedded metadata. The original YOLOv9 repository uses GPLv3, which alone does not establish this third-party checkpoint's terms. Get an explicit grant or use an alternative with clear weight terms before bundling. [Library license](https://github.com/ankandrew/open-image-models/blob/main/LICENSE) · [Model release](https://github.com/ankandrew/open-image-models/releases/tag/assets) · [Original YOLOv9 license](https://github.com/WongKinYiu/yolov9/blob/main/LICENSE.md) |
| OpenCV ResNet-10 SSD face weights | The OpenCV sample links these separately hosted Caffe weights. Keep the weights out of a source release pending verification of the exact asset's redistribution terms. [OpenCV sample](https://github.com/opencv/opencv/tree/4.x/samples/dnn/face_detector) |
| YOLOv9-s-608 plate weights | The second specialized plate checkpoint comes from the same model release. The library's MIT terms are verified and the exact ONNX asset's redistribution terms remain unverified. [Model release](https://github.com/ankandrew/open-image-models/releases/tag/assets) |

## Runtime and packaging

| Component | License and scope |
| --- | --- |
| Python standard library | PSF license and Python's accompanying notices. [Python license](https://docs.python.org/3/license.html) |
| Browser UI | Local HTML, CSS, and vanilla JavaScript. The review app does not ship a browser or a third-party frontend framework. |
| Pillow and NumPy | Installed Pillow declares MIT-CMU. NumPy's wheel declares BSD-3-Clause plus notices for bundled components. Preserve the wheel's complete notices if redistributing it. [Pillow license](https://github.com/python-pillow/Pillow/blob/main/LICENSE) · [NumPy license](https://github.com/numpy/numpy/blob/main/LICENSE.txt) |
| PyTorch and Torchvision | BSD-style source licenses. The installed builds include CUDA support. Follow the NVIDIA terms for redistribution of specific GPU binaries. [PyTorch](https://github.com/pytorch/pytorch/blob/v2.7.1/LICENSE) · [Torchvision](https://github.com/pytorch/vision/blob/v0.22.1/LICENSE) · [NVIDIA terms](https://docs.nvidia.com/cuda/eula/index.html) |
| OpenCV Python wheels | Apache-2.0 OpenCV core, MIT packaging scripts, and bundled libraries including LGPL-2.1 FFmpeg. Both OpenCV wheel variants are currently installed. [Wheel licensing](https://github.com/opencv/opencv-python#licensing) |
| Open Image Models and ONNX Runtime | MIT software. ONNX Runtime distributes its own third-party notices. [Open Image Models](https://github.com/ankandrew/open-image-models/blob/main/LICENSE) · [ONNX Runtime](https://github.com/microsoft/onnxruntime/blob/main/LICENSE) |
| dill and gdown | BSD-3-Clause checkpoint serialization dependency and MIT setup downloader, respectively. gdown was used for setup and is not called by the app. [dill](https://github.com/uqfoundation/dill/blob/master/LICENSE) · [gdown](https://github.com/wkentaro/gdown/blob/main/LICENSE) |

Installed package metadata is recorded below. It is a Python package inventory,
not a clearance of the native binaries inside the wheels. An installer or
container bundling those wheels needs an artifact-specific notice review.

| Package | Installed version | Declared license or classifier |
| --- | --- | --- |
| beautifulsoup4 | 4.15.0 | MIT License |
| certifi | 2026.10.23 | MPL-2.0 |
| charset-normalizer | 3.5.2 | MIT |
| colorama | 0.4.6 | BSD License |
| contourpy | 1.4.0 | BSD-3-Clause |
| cycler | 0.12.1 | BSD License |
| dill | 0.4.1 | BSD-3-Clause |
| filelock | 4.2.4 | MIT |
| flatbuffers | 25.12.19 | Apache 2.0 |
| fonttools | 4.68.0 | MIT |
| fsspec | 2026.9.0 | BSD-3-Clause |
| gdown | 6.5.1 | MIT |
| idna | 3.20 | BSD-3-Clause |
| Jinja2 | 3.1.6 | BSD License |
| kiwisolver | 1.5.1 | BSD License |
| markdown-it-py | 4.2.0 | MIT License |
| MarkupSafe | 3.0.3 | BSD-3-Clause |
| matplotlib | 3.11.2 | Python Software Foundation License |
| mdurl | 0.1.2 | MIT License |
| mpmath | 1.3.0 | BSD |
| networkx | 3.7 | BSD-3-Clause |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| onnxruntime | 1.31.1 | MIT License |
| open-image-models | 0.6.0 | MIT |
| opencv-python | 4.14.0.94 | Apache 2.0 |
| opencv-python-headless | 4.14.0.94 | Apache 2.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pillow | 13.0.0 | MIT-CMU |
| polars | 2.1.0 | MIT License |
| polars-runtime-32 | 2.1.0 | MIT License |
| protobuf | 7.36.2 | 3-Clause BSD License |
| psutil | 7.2.2 | BSD-3-Clause |
| Pygments | 2.21.0 | BSD-2-Clause |
| pyparsing | 3.3.3 | MIT |
| PySocks | 1.7.1 | BSD |
| python-dateutil | 4.0.0 | BSD-3-Clause |
| PyYAML | 6.0.3 | MIT |
| requests | 2.34.2 | Apache-2.0 |
| rich | 15.0.0 | MIT |
| scipy | 1.18.1 | BSD License |
| setuptools | 78.1.0 | MIT License |
| soupsieve | 3.0.4 | MIT |
| sympy | 1.14.0 | BSD |
| torch | 2.7.1+cu126 | BSD-3-Clause |
| torchvision | 0.22.1+cu126 | BSD |
| tqdm | 4.70.1 | MPL-2.0 AND MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| tzdata | 2026.6 | Apache-2.0 |
| ultralytics | 8.3.253 | AGPL-3.0 |
| ultralytics-thop | 2.2.4 | AGPL-3.0 |
| urllib3 | 2.8.0 | MIT |

EasyOCR 1.7.2 adds local plate recognition and is published under Apache-2.0.
The recognizer downloads upstream model files as needed. Those files stay in
the local EasyOCR cache and are excluded from the source release.
[EasyOCR license](https://github.com/JaidedAI/EasyOCR/blob/v1.7.2/LICENSE)
