# -*- coding: utf-8 -*-
"""运行环境自检与自动安装。

安装位置固定到用户级缓存目录（Windows 为 %LOCALAPPDATA%/pdf-extract-skill，
可通过 PDFSKILL_HOME 覆盖），换工作目录/项目不用重新下载；工作目录只放产物。

策略（按顺序）：
1. 显式指定 --python / PDFSKILL_PYTHON；
2. 新缓存目录或旧版记录中依赖齐全的解释器；
3. 本地已有 venv（PDFSKILL_VENVS、skill 邻近 tool/venv、旧工作目录）；
4. 当前解释器、DSH 运行时自带 Python、PATH 里的 python/py；
5. 都没有齐全依赖 → 在固定目录建 venv 并 pip 安装。
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# 模块名 -> 说明（用于报告）
MODULES = {
    "pymupdf": "PDF 解析与渲染",
    "rapidocr_onnxruntime": "OCR 引擎（PP-OCR 中文）",
    "numpy": "数值计算",
    "cv2": "图像处理（opencv）",
    "PIL": "图像读写（Pillow）",
    "openpyxl": "Excel 导出与读取",
    "docx": "Word 导出（python-docx）",
    "pdf2docx": "复杂版面 PDF 转 Word",
}
# 必需包（pymupdf + OCR + 基础图像）
CORE_PACKAGES = ["pymupdf", "rapidocr-onnxruntime", "numpy", "pillow"]
# 可选包（缺了会降级：xlsx/docx 导出不可用）
OPTIONAL_PACKAGES = ["openpyxl", "python-docx", "pdf2docx"]

CHECK_CODE = (
    "import json,sys\n"
    "res={}\n"
    "for mod,key in [('pymupdf','pymupdf'),('rapidocr_onnxruntime','rapidocr'),"
    "('numpy','numpy'),('cv2','cv2'),('PIL','pillow'),('openpyxl','openpyxl'),"
    "('docx','docx'),('pdf2docx','pdf2docx')]:\n"
    "    try:\n"
    "        m=__import__(mod)\n"
    "        res[key]=getattr(m,'__version__',None) or 'ok'\n"
    "    except Exception as e:\n"
    "        res[key]=None\n"
    "print(json.dumps(res))\n"
)


class EnvError(RuntimeError):
    pass


class Env:
    """记录可用的解释器与实际安装位置。"""

    def __init__(self, python: str, packages: dict, source: str, work_dir: Path):
        self.python = python
        self.packages = packages
        self.source = source
        self.work_dir = work_dir

    def run(self, args, cwd=None, timeout=None, capture=True):
        cmd = [self.python] + [str(a) for a in args]
        kw = dict(cwd=str(cwd) if cwd else None, timeout=timeout,
                  stdout=subprocess.PIPE if capture else None,
                  stderr=subprocess.PIPE if capture else None)
        return subprocess.run(cmd, **kw)

    def as_dict(self):
        return {
            "python": self.python,
            "source": self.source,
            "work_dir": str(self.work_dir),
            "packages": self.packages,
            "missing_optional": [m for m in ("openpyxl", "docx", "pdf2docx")
                                 if not self.packages.get(m)],
        }


def _probe(python: str):
    """返回 (ok, packages)。"""
    try:
        proc = subprocess.run([python, "-c", CHECK_CODE], capture_output=True, timeout=120)
        if proc.returncode != 0:
            return False, {}
        pkgs = json.loads(proc.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
        ok = all(pkgs.get(k) for k in ("pymupdf", "rapidocr", "numpy"))
        return ok, pkgs
    except Exception:
        return False, {}


def probe(python: str):
    """公开：探测某解释器是否具备核心依赖。返回 (ok, packages)。"""
    return _probe(python)


def _candidate_pythons(explicit=None):
    seen, out = set(), []

    def add(p):
        if not p:
            return
        p = str(p)
        if p in seen or not Path(p).exists():
            return
        seen.add(p)
        out.append(p)

    add(explicit)
    add(os.environ.get("PDFSKILL_PYTHON"))
    add(sys.executable)
    # DSH 运行时自带 Python（不同版本目录都可能）
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    for pat in (r".dsh\dsh-runtimes\*\dependencies\python\python.exe",):
        add_glob = glob.glob(str(home / pat))
        for p in add_glob:
            add(p)
    # PATH
    for name in ("python", "python3"):
        add(shutil.which(name))
    add(shutil.which("py"))
    return out


def _known_venvs():
    """本地可能已有完整环境的 venv（存在即用，省一次下载）。"""
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    cands = []
    extra = os.environ.get("PDFSKILL_VENVS")
    if extra:
        cands.extend(Path(p) for p in extra.split(os.pathsep) if p)
    try:
        skill_root = Path(__file__).resolve().parents[2]
        # skills/tool/venv
        cands.append(skill_root.parent / "tool" / "venv" / "Scripts" / "python.exe")
        cands.append(skill_root.parent / "tool" / "venv" / "bin" / "python")
        # <project>/tool/venv
        cands.append(skill_root.parent.parent / "tool" / "venv" / "Scripts" / "python.exe")
        cands.append(skill_root.parent.parent / "tool" / "venv" / "bin" / "python")
    except Exception:
        pass
    cands.append(skill_home() / "venv" / "Scripts" / "python.exe")
    cands.append(skill_home() / "venv" / "bin" / "python")
    # 只复用旧目录中的现有环境，不把新依赖安装到旧目录
    cands.append(home / ".dsh" / "pdfskill" / "venv" / "Scripts" / "python.exe")
    cands.append(home / ".dsh" / "pdfskill" / "venv" / "bin" / "python")
    # 兼容旧版本：曾装在 <某工作目录>/Temp/pdfskill
    cands.append(Path.cwd() / "Temp" / "pdfskill" / "venv" / "Scripts" / "python.exe")
    cands.append(Path.cwd() / "Temp" / "pdfskill" / "venv" / "bin" / "python")
    return [str(p) for p in cands if p.exists()]


def skill_home() -> Path:
    """用户级环境缓存目录（换工作目录不用重装）。

    优先级：PDFSKILL_HOME > 平台用户缓存目录/pdf-extract-skill。
    旧版 .dsh 记录只作为现有解释器的迁移线索，不会作为新安装目录。
    """
    override = os.environ.get("PDFSKILL_HOME")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or
                    (Path(os.environ.get("USERPROFILE") or Path.home()) / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return base / "pdf-extract-skill"


def _state_path(work_dir=None) -> Path:
    return skill_home() / "env.json"


def _state_paths(work_dir: Path):
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    candidates = [
        _state_path(work_dir),
        home / ".dsh" / "pdfskill" / "env.json",
        Path(work_dir).resolve() / "Temp" / "pdfskill" / "env.json",
    ]
    seen = set()
    output = []
    for path in candidates:
        key = str(path)
        if key not in seen:
            seen.add(key)
            output.append(path)
    return output


def _recorded_pythons(work_dir: Path):
    found = []
    for path in _state_paths(work_dir):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        python = data.get("python")
        if python and python not in found:
            found.append(python)
    return found


def _write_state(work_dir: Path, info: dict):
    p = _state_path(work_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _pip_install(python: str, packages, work_dir: Path, log):
    cache = skill_home() / "pip-cache"
    cache.mkdir(parents=True, exist_ok=True)
    cmd = [python, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
           "--cache-dir", str(cache), "-q"] + list(packages)
    log("pip install: " + " ".join(packages))
    proc = subprocess.run(cmd, capture_output=True, timeout=1800)
    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode("utf-8", "replace").strip()[-1500:]
        log("pip install failed: " + tail)
        raise EnvError("pip install failed (offline?). Pass an existing environment via --python.\n" + tail)
    log("pip install done")


def ensure(work_dir: Path, explicit_python=None, install=True, verbose=True):
    """确保有可用的解释器与依赖；需要时在用户级缓存目录安装。"""
    work_dir = Path(work_dir).resolve()
    log = (lambda m: print("[env] " + m, file=sys.stderr)) if verbose else (lambda m: None)

    # 1) 显式路径 > 已记录环境 > 本地已有 venv > 当前解释器/PATH
    preferred = [
        explicit_python,
        os.environ.get("PDFSKILL_PYTHON"),
        *_recorded_pythons(work_dir),
        *_known_venvs(),
        *_candidate_pythons(),
    ]
    seen = set()
    for p in preferred:
        if not p:
            continue
        if p in seen:
            continue
        seen.add(p)
        ok, pkgs = _probe(p)
        if ok:
            missing = [pkg for mod, pkg in (("openpyxl", "openpyxl"),
                                            ("docx", "python-docx"),
                                            ("pdf2docx", "pdf2docx"))
                       if not pkgs.get(mod)]
            if missing and install:
                try:
                    _pip_install(p, missing, work_dir, log)
                    ok, pkgs = _probe(p)
                except EnvError:
                    log("optional packages install failed; xlsx/docx export will degrade")
            log("using existing environment: " + p)
            info = {"python": p, "source": "existing", "packages": pkgs,
                    "install_home": str(skill_home()),
                    "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}
            _write_state(work_dir, info)
            return Env(p, pkgs, "existing", work_dir)

    if not install:
        raise EnvError("no usable Python environment (missing pymupdf / rapidocr_onnxruntime) and auto-install is disabled")

    # 2) 找基础解释器，在用户级缓存目录建 venv
    bases = [p for p in _candidate_pythons(explicit_python) if Path(p).exists()]
    if not bases:
        raise EnvError("no Python found; install Python 3.12 or pass --python")

    temp_root = skill_home()
    temp_root.mkdir(parents=True, exist_ok=True)
    log("install home: " + str(temp_root))
    base = bases[0]
    log("base python: " + base)

    venv_dir = temp_root / "venv"
    vpy = _venv_python(venv_dir)
    if not vpy.exists():
        log("creating venv: " + str(venv_dir))
        proc = subprocess.run([base, "-m", "venv", str(venv_dir)], capture_output=True, timeout=600)
        if proc.returncode != 0:
            tail = (proc.stderr or b"").decode("utf-8", "replace")[-800:]
            raise EnvError("venv creation failed: " + tail)
    if vpy.exists():
        _pip_install(str(vpy), CORE_PACKAGES, work_dir, log)
        try:
            _pip_install(str(vpy), OPTIONAL_PACKAGES, work_dir, log)
        except EnvError:
            log("optional packages failed; xlsx/docx export will degrade")
        ok, pkgs = _probe(str(vpy))
        if ok:
            info = {"python": str(vpy), "source": "fixed-venv", "base_python": base,
                    "packages": pkgs, "install_home": str(skill_home()),
                    "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}
            _write_state(work_dir, info)
            return Env(str(vpy), pkgs, "fixed-venv", work_dir)

    # 3) 兜底：--target 安装到用户级缓存目录，靠 PYTHONPATH 生效
    log("venv 方案不可用，改用 --target 安装到用户级缓存")
    site = temp_root / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    cmd = [base, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "-q",
           "--target", str(site), "--cache-dir", str(temp_root / "pip-cache")] + CORE_PACKAGES + OPTIONAL_PACKAGES
    proc = subprocess.run(cmd, capture_output=True, timeout=1800)
    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode("utf-8", "replace")[-1500:]
        raise EnvError("dependency install failed (network?): " + tail)
    os.environ["PYTHONPATH"] = str(site) + os.pathsep + os.environ.get("PYTHONPATH", "")
    ok, pkgs = _probe(base)
    if ok:
        info = {"python": base, "source": "fixed-target", "site_packages": str(site),
                "packages": pkgs, "install_home": str(skill_home()),
                "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        _write_state(work_dir, info)
        return Env(base, pkgs, "fixed-target", work_dir)
    raise EnvError("imports still failing after install; check Python version (tested 3.12; 3.13 not supported by rapidocr-onnxruntime)")
