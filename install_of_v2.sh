#!/bin/bash
set -e
LOG="/mnt/d/Phase2_CFD_ML/of_install_v2.log"
echo "=== OpenFOAM Install v2 for Ubuntu 24.04 ===" > "$LOG"
echo "Started: $(date)" >> "$LOG"

# Update package lists first
echo "[1/3] Updating apt..." | tee -a "$LOG"
sudo apt-get update -y >> "$LOG" 2>&1

# Try installing openfoam from Ubuntu repos
echo "[2/3] Installing OpenFOAM from apt..." | tee -a "$LOG"
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y openfoam 2>> "$LOG" || {
    echo "openfoam package not found, trying Docker approach..." | tee -a "$LOG"
    # Install Docker
    sudo apt-get install -y docker.io 2>> "$LOG" || true
    sudo systemctl start docker 2>> "$LOG" || true
    sudo docker pull openfoam/openfoam2312-default 2>> "$LOG" || true
}

# Verify
echo "[3/3] Verifying..." | tee -a "$LOG"
which blockMesh 2>/dev/null && echo "blockMesh found" | tee -a "$LOG" || echo "blockMesh NOT found" | tee -a "$LOG"
which simpleFoam 2>/dev/null && echo "simpleFoam found" | tee -a "$LOG" || echo "simpleFoam NOT found" | tee -a "$LOG"
echo "=== DONE $(date) ===" >> "$LOG"
