#!/bin/bash
# OpenFOAM v2312 install for Ubuntu 24.04 via ESI official repo
set -e

LOG="/mnt/d/Phase2_CFD_ML/of_install_2404.log"
echo "=== OpenFOAM Install for Ubuntu 24.04 ===" > "$LOG"
echo "Started: $(date)" >> "$LOG"

# Add ESI OpenFOAM repository
echo "[1/4] Adding ESI OpenFOAM repo..." | tee -a "$LOG"
curl -sSL https://dl.openfoam.com/add-deb-repo.sh | sudo bash >> "$LOG" 2>&1

# Update package lists
echo "[2/4] Updating package lists..." | tee -a "$LOG"
sudo apt-get update -y >> "$LOG" 2>&1

# Install OpenFOAM v2312
echo "[3/4] Installing OpenFOAM v2312 (this will take a while)..." | tee -a "$LOG"
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y openfoam2312-default >> "$LOG" 2>&1

# Verify
echo "[4/4] Verifying..." | tee -a "$LOG"
if [ -f /usr/lib/openfoam/openfoam2312/etc/bashrc ]; then
    echo "OpenFOAM v2312 installed successfully!" | tee -a "$LOG"
    source /usr/lib/openfoam/openfoam2312/etc/bashrc
    which blockMesh >> "$LOG" 2>&1
    which simpleFoam >> "$LOG" 2>&1
    which snappyHexMesh >> "$LOG" 2>&1
    echo "All tools available" | tee -a "$LOG"
else
    echo "ERROR: OpenFOAM bashrc not found" | tee -a "$LOG"
    # Try alternative paths
    find /usr -name "bashrc" -path "*/openfoam*" 2>/dev/null >> "$LOG"
fi

echo "=== DONE $(date) ===" >> "$LOG"
