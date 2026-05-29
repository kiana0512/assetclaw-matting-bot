# Win3090 Full Setup Guide — From Zero to Production

## Prerequisites

### 1. NVIDIA Driver
```powershell
winget install Nvidia.GeForceExperience  # or download directly from nvidia.com/drivers
# Verify:
nvidia-smi
```

### 2. Core Tools
```powershell
winget install Git.Git
winget install GitHub.GitLFS
winget install Anaconda.Miniconda3
winget install OpenJS.NodeJS.LTS
winget install Cloudflare.cloudflared
winget install NSSM.NSSM
winget install Microsoft.VisualStudio.2022.BuildTools  # for Python native extensions
```

After installing Miniconda, open a new terminal to activate conda.

### 3. Clone the Repository
```powershell
cd E:\
git clone https://github.com/yourorg/assetclaw-matting-bot.git
cd assetclaw-matting-bot
```

Or copy the existing repo to `E:\assetclaw-matting-bot\`.

---

## Unified Environment Setup

### 4. Create conda env: assetclaw (one env for everything)
```powershell
.\scripts\setup_unified_env.ps1
```

Or manually:
```powershell
conda create -n assetclaw python=3.11 -y
conda activate assetclaw
pip install -r requirements.txt
Copy-Item .env.example .env
python -m assetclaw_matting.cli.main init-db
```

### 5. Configure .env
Edit `E:\assetclaw-matting-bot\.env`:
```env
SKILL_API_TOKEN=your_strong_token_here
WORKER_TOKEN=your_strong_token_here
BRAIN_PROVIDER=llm_proxy            # or local_command for testing
LLM_PROXY_ENABLED=true
LLM_PROXY_BASE_URL=https://your-company-llm-proxy.com
LLM_PROXY_API_KEY=your_key
COMFYUI_FAKE_MODE=true              # start with fake mode
```

---

## Fake Mode Test (No GPU Required)

### 6. Test the full pipeline without ComfyUI
```powershell
# Terminal 1: Gateway
conda activate assetclaw
python -m assetclaw_matting.cli.main gateway

# Terminal 2: Create and run a batch
conda activate assetclaw
# Put some images in storage\batch_inputs\
python -m assetclaw_matting.cli.main batch-create `
    --input-dir E:\assetclaw-matting-bot\storage\batch_inputs `
    --output-dir E:\assetclaw-matting-bot\storage\batch_outputs
python -m assetclaw_matting.cli.main batch-start --batch-id BATCH_XXX

# Terminal 3: Worker
conda activate assetclaw
python -m assetclaw_matting.cli.main worker
```

Expected: `batch_outputs/*.png` files appear, batch status = SUCCEEDED.

### 7. Test Skill API
```powershell
.\scripts\health_check.ps1
```

---

## Real ComfyUI Setup

### 8. Install ComfyUI in unified env
```powershell
.\scripts\setup_comfyui_in_unified_env.ps1
```

### 9. Install matting model
Launch ComfyUI (`.\scripts\run_comfyui.ps1`) and via ComfyUI Manager install:
- **BRIA RMBG** (recommended) or **BiRefNet**
- Download the model weights when prompted

### 10. Export your workflow
1. Build matting workflow in ComfyUI (LoadImage → RMBG → SaveImage)
2. Settings → Enable Dev Mode Options
3. Click **Save (API Format)** → save as `workflows\matting_api.json`

### 11. Switch to real mode
```env
COMFYUI_FAKE_MODE=false
```

---

## cloudflared (for Feishu / External Access)

### 12. Start tunnel
```powershell
cloudflared tunnel --url http://127.0.0.1:7865
# Copy the https://xxxx.trycloudflare.com URL
```

Configure Feishu callback: `https://xxxx.trycloudflare.com/feishu/events`

Note: cloudflared URL changes on restart. Use a named tunnel for production.

---

## Service Installation (Run on Startup)

### 13. Install as Windows services
```powershell
.\scripts\install_services.ps1
# Then:
nssm start AssetClawGateway
nssm start AssetClawWorker
```

---

## Final Verification
```powershell
.\scripts\health_check.ps1
```

Expected output:
- GPU: RTX 3090 24GB listed
- Gateway /health: ok=true
- Skill manifest: 12+ implemented skills
- ComfyUI: online (or fake_online in fake mode)
