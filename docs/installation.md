# Installation

PyLightSuite (the Python port of [LightSuite](https://github.com/dimokaramanlis/LightSuite)) is installed from this repository with [`uv`](https://docs.astral.sh/uv/). You also need **Elastix 5.1.0** on your system `PATH` and atlas files for registration. **BCPD** is recommended for the brain coarse alignment.

## Requirements

| Component | Version / notes |
|-----------|-----------------|
| Python | 3.11 – 3.12 (3.13 is blocked, see [Troubleshooting](#troubleshooting)) |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | Package and environment manager |
| [Elastix](https://github.com/SuperElastix/elastix/releases/tag/5.1.0) | 5.1.0, with `elastix` and `transformix` on `PATH` |
| [BCPD](https://github.com/ohirose/bcpd) | Recommended for brain `init-registration` |
| Atlas files | Allen CCF (10 µm), Perens LSFM (20 µm), a BrainGlobe atlas, or the Fiederling spinal cord atlas |
| Scratch disk | ≥50 GB free (500 GB+ recommended for large brains) |

MATLAB is **not** required for any of the Python pipelines.

---

## 1. Install uv and clone the repository

Install uv if `uv --version` does not work yet:

```bash
# Linux / macOS
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc          # or open a new terminal
uv --version
```

On Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`.

Clone PyLightSuite:

```bash
git clone https://github.com/Wyss-Microscopy/PyLightSuite.git
cd PyLightSuite
```

The Python pipeline is developed on the `main` branch. Do not clone the upstream [MATLAB repository](https://github.com/dimokaramanlis/LightSuite) for the Python CLI; it does not contain the `lightsuite` package.

---

## 2. Install Python dependencies

From the repository root (use **Python 3.11 or 3.12**: `open3d` has no PyPI wheel for 3.13 yet):

```bash
# Pin Python for this repo (recommended; writes .python-version)
uv python pin 3.12

# Recommended: Napari GUIs + BrainGlobe atlases + tests
uv sync --extra dev --extra gui --extra atlas

# Minimal: command line + tests only (headless servers)
uv sync --extra dev

# All optional extras
uv sync --extra dev --extra gui --extra atlas --extra formats --extra viz
```

This creates a virtual environment in `.venv` and installs the `lightsuite` command. Run it with **`uv run lightsuite …`** from the repository folder; there is no need to activate the environment.

> **List every extra in one command.** `uv sync` makes the environment match exactly what you ask for, so a later `uv sync --extra gui` **uninstalls** `dev` and `atlas`. Always repeat the full list.

| Extra | Purpose |
|-------|---------|
| `gui` | Napari GUIs (orientation, slice alignment, match-points, straightening, registration review) and `lightsuite gui` |
| `atlas` | BrainGlobe Atlas API (`atlas.source: brainglobe` in config) |
| `dev` | pytest, ruff, mypy |
| `formats` | Imaris `.ims` file reader (experimental; not used by the current stages) |
| `viz` | BrainGlobe heatmaps and brainrender visualizations (not used by the current stages) |

Verify:

```bash
uv run lightsuite --version
uv run lightsuite --help
uv run lightsuite doctor
```

You should see `check-orientation` listed under `lightsuite brain` commands.

---

## 3. Install Elastix 5.1.0

PyLightSuite calls `elastix` and `transformix` as subprocesses, as the MATLAB toolbox does.

### Download

Get the [5.1.0 release](https://github.com/SuperElastix/elastix/releases/tag/5.1.0) for your platform and unpack to a permanent location (e.g. `~/software/elastix-5.1.0`, `/opt/elastix` or `C:\elastix`).

On **Linux**:

```bash
mkdir -p ~/software && cd ~/software
wget https://github.com/SuperElastix/elastix/releases/download/5.1.0/elastix-5.1.0-linux.zip
unzip elastix-5.1.0-linux.zip -d elastix-5.1.0
ls elastix-5.1.0/bin      # elastix  transformix
```

### Add to PATH

**Linux** — add to `~/.bashrc` (Elastix also needs its bundled libraries on `LD_LIBRARY_PATH`):

```bash
echo 'export PATH="$HOME/software/elastix-5.1.0/bin:$PATH"' >> ~/.bashrc
echo 'export LD_LIBRARY_PATH="$HOME/software/elastix-5.1.0/lib:$LD_LIBRARY_PATH"' >> ~/.bashrc
source ~/.bashrc
```

**macOS** — add to `~/.zshrc`:

```bash
export PATH="/path/to/elastix/bin:$PATH"
```

**Windows** — add the folder containing `elastix.exe` and `transformix.exe` to the system PATH.

### Verify

Open a **new** terminal:

```bash
elastix --version         # elastix version: 5.1.0
transformix --help
```

---

## 4. Install BCPD (recommended for init-registration)

The MATLAB brain pipeline uses [BCPD](https://github.com/ohirose/bcpd) for coarse point-cloud alignment. The Python `init-registration` step calls the same `bcpd` binary.

If BCPD is missing, PyLightSuite falls back to Open3D ICP, which is often too weak for sparse lightsheet clouds and produces scattered boundary overlays. The spinal cord workflow does not use BCPD.

### Build or locate the binary

Use the same `bcpd` / `bcpd.exe` you already use with a MATLAB LightSuite install, or build it from the [BCPD repository](https://github.com/ohirose/bcpd).

On **Linux/macOS**, do **not** point `bcpd_path` at `win/bcpd.exe`: that file is the Windows build. Compile a native binary instead:

```bash
# Linux (needs gcc and make: sudo apt install build-essential)
git clone https://github.com/ohirose/bcpd.git ~/software/bcpd
cd ~/software/bcpd
make OPT=-DUSE_OPENMP ENV=LINUX   # produces ./bcpd
./bcpd -v

# put it on PATH
echo 'export PATH="$HOME/software/bcpd:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

PyLightSuite looks for BCPD in this order: the `LIGHTSUITE_BCPD_PATH` environment variable, then `registration.bcpd_path` in the YAML, then `bcpd` on `PATH`. If `bcpd -v` already works in your shell, you can omit `bcpd_path`.

Optional YAML override:

```yaml
registration:
  bcpd_path: /path/to/bcpd
```

### Verify

```bash
which bcpd    # or: where bcpd.exe on Windows
uv run lightsuite doctor
```

Doctor should list **BCPD (coarse registration)** with the resolved path.

---

## 5. Download atlas data

### Allen brain atlas (default)

Registration expects these files in one directory:

- `average_template_10.nii.gz`
- `annotation_10.nii.gz`
- `annotation_boundary_10.nii.gz` (recommended: precomputed atlas region outlines for registration preview PNGs)

Download from the [Allen Brain Cell Atlas](https://alleninstitute.github.io/abc_atlas_access/descriptions/Allen-CCF-2020.html) ([volume files](https://allen-brain-cell-atlas.s3.us-west-2.amazonaws.com/index.html#image_volumes/Allen-CCF-2020/20230630/)).

**Optional (for parcellation CSV export):**

- [`parcellation_to_parcellation_term_membership.csv`](https://allen-brain-cell-atlas.s3-us-west-2.amazonaws.com/metadata/Allen-CCF-2020/20230630/parcellation_to_parcellation_term_membership.csv): required for region names in the exported tables

On **Linux**, the CSV can be fetched directly:

```bash
mkdir -p ~/atlases/allen_ccf_10um && cd ~/atlases/allen_ccf_10um
wget https://allen-brain-cell-atlas.s3-us-west-2.amazonaws.com/metadata/Allen-CCF-2020/20230630/parcellation_to_parcellation_term_membership.csv
```

Point PyLightSuite at the atlas in either way:

```yaml
# In your config YAML
atlas:
  provider: allen
  resolution_um: 10
  atlas_dir: /path/to/allen_ccf_10um
```

or (add to `~/.bashrc` to make it permanent):

```bash
export LIGHTSUITE_ATLAS_PATH=/path/to/allen_ccf_10um
```

For Allen parcellation tables, when the CSV is not in the atlas folder:

```bash
export LIGHTSUITE_ALLEN_PARCELLATION_CSV=/path/to/parcellation_to_parcellation_term_membership.csv
```

### Perens LSFM atlas (20 µm)

The [Perens et al. LSFM mouse brain atlas](https://github.com/Gubra-ApS/LSFM-mouse-brain-atlas) (Gubra) is distributed at **20 µm** isotropic resolution, the same layout used by [BrainGlobe's `perens_lsfm_mouse` packager](https://github.com/brainglobe/brainglobe-atlasapi).

**Download** the repository archive:

- [LSFM-mouse-brain-atlas `master.tar.gz`](https://github.com/Gubra-ApS/LSFM-mouse-brain-atlas/archive/master.tar.gz)

On **Linux**:

```bash
mkdir -p ~/atlases && cd ~/atlases
wget -O LSFM-mouse-brain-atlas.tar.gz https://github.com/Gubra-ApS/LSFM-mouse-brain-atlas/archive/master.tar.gz
tar -xzf LSFM-mouse-brain-atlas.tar.gz
ls LSFM-mouse-brain-atlas-master/LSFM_atlas_files/
```

Unpack and use the NIfTIs under `LSFM-mouse-brain-atlas-master/LSFM_atlas_files/`:

| File | Role |
|------|------|
| `gubra_template_olf.nii.gz` | Template (reference) volume |
| `gubra_ano_olf.nii.gz` | Annotation / parcellation volume |
| `ARA2_annotation_info_avail_regions.csv` | Optional: region names for parcellation CSV export |

Some downloads place the template only under `LSFM_atlas_files/perens/` while the annotation stays in `LSFM_atlas_files/`. PyLightSuite searches both layouts when `atlas_dir` points at either folder.

Point PyLightSuite at the atlas in either way:

```yaml
# In your config YAML
atlas:
  provider: perens
  resolution_um: 20
  atlas_dir: /path/to/LSFM-mouse-brain-atlas-master/LSFM_atlas_files
```

or:

```bash
export LIGHTSUITE_ATLAS_PATH=/path/to/LSFM-mouse-brain-atlas-master/LSFM_atlas_files
```

**Citation:** Perens et al. 2021, [doi:10.1007/s12021-020-09490-8](https://doi.org/10.1007/s12021-020-09490-8)

### BrainGlobe atlases (optional)

With the `atlas` extra installed, the atlas is downloaded from [BrainGlobe](https://brainglobe.info/documentation/brainglobe-atlasapi/) on first use (into `~/.brainglobe/`), so no `atlas_dir` is needed:

```yaml
atlas:
  source: brainglobe
  provider: allen                 # allen, perens, princeton or rat
  brainglobe_name: allen_mouse_10um
```

| `provider` | `brainglobe_name` |
|------------|-------------------|
| `allen` | `allen_mouse_10um` |
| `perens` | `perens_lsfm_mouse_20um` |
| `princeton` | `princeton_mouse_20um` |
| `rat` | `whs_sd_rat_39um` (Waxholm rat) |

The Princeton and rat atlases are available only through BrainGlobe.

### Fiederling spinal cord atlas

For the [spinal cord workflow](usage_spinal_cord.md), download the atlas from [Mendeley Data](https://data.mendeley.com/datasets/4rrggzv5d5/1) (Fiederling et al. 2021). Point `atlas.atlas_dir` at the unpacked folder that contains `Template.tif`, `Annotation.tif` and `Segments.csv` (e.g. `SC_P56_Atlas_10x10x20_v5_2020`).

---

## 6. Verify installation

```bash
uv run lightsuite doctor
```

With a config file (checks scratch path, atlas, etc.):

```bash
cp examples/brain_lightsheet.yaml my_sample.yaml   # then edit the paths
uv run lightsuite doctor -c my_sample.yaml
```

Use `--strict` to fail on optional atlas warnings.

Expected checks:

| Check | Required |
|-------|----------|
| Python 3.11 – 3.12 | Yes |
| `elastix` / `transformix` on PATH | Yes |
| BCPD | Recommended (brain) |
| Atlas files | Yes (for registration) |
| Scratch free space | Warning if low |

The run ends with `All required checks passed.` GPU-accelerated cell detection is **not implemented in Python**; it remains in the [original MATLAB LightSuite](https://github.com/dimokaramanlis/LightSuite).

### Run the tests (optional)

```bash
uv run pytest -q
```

Tests that need Elastix, BCPD, Napari or local atlas files are skipped automatically when those are missing.

---

## Updating after `git pull`

If a new CLI command is missing after pulling, the virtualenv is usually still pointing at an old install. From the repo root:

```bash
git pull origin main
uv sync --extra dev --extra gui --extra atlas --reinstall-package lightsuite
uv run lightsuite doctor
uv run lightsuite --help
```

Always run the CLI with **`uv run lightsuite`** (or activate `.venv` first). A `lightsuite` binary elsewhere on your PATH may be an older global install.

---

## Speed and data storage

LightSuite is I/O-heavy: many stages read large TIFF stacks or thousands of small plane files. **Where data lives** often matters more than CPU core count or `compute.workers`.

### Hardware and storage

| Dataset | CPU | RAM | Free scratch | Notes |
|---------|-----|-----|--------------|-------|
| Spinal cord, 1–2 channels | 4+ cores | 16 GB | 50 GB SSD | `register` and `export` usually fit in 16 GB |
| Whole brain, 1–2 channels | 8+ cores | 32–64 GB | 100–200 GB SSD | `register` is the RAM peak |
| Large brain, wide field of view, dual-channel | 8–16 cores | 64–128 GB | 200–500 GB SSD | Use content cropping and B-spline tuning |

No GPU is used.

### Put hot paths on fast local disk

| Path | Used for |
|------|----------|
| `sample.source` / channel folders | Raw SmartSPIM / lightsheet TIFFs |
| `sample.scratch` | Preprocess memmaps and temporary volumes |
| `sample.save_path` | Checkpoints and registered outputs |

**Prefer local NVMe or SSD** for these paths. Registration on a **spinning HDD** or **network share** (SMB/NAS) can be **several times slower** than the same data on NVMe, especially for SmartSPIM **plane-per-file** folders (`Z000000_Ch0.tif`, …) where preprocess reads **one file per Z plane**.

Copy or symlink acquisition data to a fast drive before long runs when the instrument export lives on slow storage. On Linux, check free space and disk type with:

```bash
df -h /path/to/scratch
lsblk -d -o NAME,ROTA,SIZE,MODEL     # ROTA=0 means SSD/NVMe
```

### `compute.workers` (brain / spinal preprocess only)

| Layout | Workers |
|--------|---------|
| Brain **channelperfile** (one hyperstack per channel) | Uses `compute.workers` (default 4) |
| Brain **planeperfile** (SmartSPIM `Ch0/`, Terastitcher) | **Forced to 1**: parallel reads thrash the disk |
| Spinal **planeperfile** | Uses workers (capped at 8) |
| Init-registration, register, export | **Ignored** |

Raising workers on SmartSPIM brain data does **not** speed up preprocess.

### Windows notes

- Exclude scratch, save, and data folders from real-time antivirus scanning when possible.
- After `split-smartspim-channels`, point configs at **`Ch0` / `Ch1` folders**, not the flat interleaved `All_Channels` parent.
- Use `lightsuite doctor -c my.yaml` to confirm atlas, scratch, and BCPD paths before long runs.

---

## Troubleshooting

| Message or symptom | Cause and fix |
|--------------------|---------------|
| `uv: command not found` | Install uv ([step 1](#1-install-uv-and-clone-the-repository)): `curl -LsSf https://astral.sh/uv/install.sh \| sh`, then open a new terminal. |
| `uv sync` fails with `open3d ... only has wheels ... cp311, cp312` | uv selected **Python 3.13**, which Open3D does not ship on PyPI yet. Recreate the environment with 3.12 (see below). |
| `warning: VIRTUAL_ENV=… does not match the project environment path .venv` | Another Python environment is activated in your shell. It is harmless (`uv run` uses this repository's `.venv`). Run `deactivate`, and remove any `source …/activate` line from `~/.bashrc`. |
| `error: Extra '…' is not defined` | That extra does not exist (for example, the old `registration` extra was removed). Use the extras listed in [step 2](#2-install-python-dependencies). |
| `pytest` or Napari disappeared after `uv sync` | `uv sync` removes extras you did not list. Rerun with all extras. |
| `elastix not found` in `doctor` | Elastix is not on `PATH` in the shell where you run `lightsuite`. Check with `which elastix` (`where elastix` on Windows). |
| `error while loading shared libraries: libANNlib-5.1.so.1` | Add the Elastix `lib/` folder to `LD_LIBRARY_PATH` ([step 3](#3-install-elastix-510)). |
| `init-registration` logs `(ICP fallback)` | BCPD was not found. Install it ([step 4](#4-install-bcpd-recommended-for-init-registration)) and rerun `init-registration`. |
| Atlas not found | Set `atlas.atlas_dir` in YAML or `LIGHTSUITE_ATLAS_PATH`. Both template and annotation files must exist in that folder. |
| `lightsuite gui` prints nothing and does not return | This is normal: the command stays busy while the Napari window is open. |
| Napari fails to launch | Install the `gui` extra. On Linux over SSH, use a desktop session or X forwarding, and check that `QT_QPA_PLATFORM` is not set to `offscreen`. |
| A command is missing after `git pull` | See [Updating after `git pull`](#updating-after-git-pull). |
| Elastix integration tests are skipped | Expected when `elastix` is not on `PATH`; install Elastix to run them. |

Recreate the environment with Python 3.12:

```bash
rm -rf .venv          # Windows: Remove-Item -Recurse -Force .venv
uv python pin 3.12
uv sync --extra dev --extra gui --extra atlas
```

Or pass `--python 3.12` once without pinning.

---

## Next steps

- [Brain lightsheet](usage_lightsheet_brain.md): run your first registration
- [Spinal cord](usage_spinal_cord.md): straightening, registration and region statistics
- [Configuration reference](config_reference.md): every YAML key
