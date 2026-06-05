#!/bin/bash
# OpenFOAM installation script for Ubuntu 22.04 WSL2
# Uses Tsinghua mirror for faster download from China
set -e

LOG="/mnt/d/Phase2_CFD_ML/of_install.log"
echo "=== OpenFOAM Install Log ===" > "$LOG"
echo "Started: $(date)" >> "$LOG"

# Step 1: Backup original sources.list
echo "[1/4] Backing up original sources.list..." | tee -a "$LOG"
sudo cp /etc/apt/sources.list /etc/apt/sources.list.bak 2>/dev/null || true
echo "Backup done." >> "$LOG"

# Step 2: Switch to Tsinghua mirror
echo "[2/4] Switching to Tsinghua mirror..." | tee -a "$LOG"
sudo sed -i 's|http://archive.ubuntu.com|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list
sudo sed -i 's|http://security.ubuntu.com|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list
echo "Mirror changed." >> "$LOG"

# Step 3: Update package lists
echo "[3/4] Updating package lists..." | tee -a "$LOG"
sudo apt-get update -y >> "$LOG" 2>&1
echo "Update done." >> "$LOG"

# Step 4: Install OpenFOAM
echo "[4/4] Installing OpenFOAM (this may take a while)..." | tee -a "$LOG"
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y openfoam >> "$LOG" 2>&1
echo "Install done." >> "$LOG"

# Verify installation
echo "" >> "$LOG"
echo "=== Verification ===" >> "$LOG"
dpkg -l | grep openfoam >> "$LOG" 2>&1
echo "" >> "$LOG"
echo "OpenFOAM binaries:" >> "$LOG"
ls /usr/bin/*Foam* 2>/dev/null >> "$LOG" || echo "No OpenFOAM binaries found in /usr/bin" >> "$LOG"
echo "" >> "$LOG"
ls /usr/lib/openfoam 2>/dev/null >> "$LOG" || echo "No /usr/lib/openfoam" >> "$LOG"
echo "" >> "$LOG"

# Source the OpenFOAM environment
if [ -f /usr/lib/openfoam/openfoam/etc/bashrc ]; then
    source /usr/lib/openfoam/openfoam/etc/bashrc
    echo "Sourced OpenFOAM bashrc" >> "$LOG"
elif [ -f /opt/openfoam*/etc/bashrc ]; then
    source /opt/openfoam*/etc/bashrc 2>/dev/null
    echo "Sourced OpenFOAM from /opt" >> "$LOG"
fi

which blockMesh >> "$LOG" 2>&1 || echo "blockMesh not in PATH" >> "$LOG"
which simpleFoam >> "$LOG" 2>&1 || echo "simpleFoam not in PATH" >> "$LOG"

echo "" >> "$LOG"
echo "=== INSTALL COMPLETE ===" >> "$LOG"
echo "Finished: $(date)" >> "$LOG"
