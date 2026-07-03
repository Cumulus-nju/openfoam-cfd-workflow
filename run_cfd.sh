#!/bin/bash
set -e

CASE_DIR="/mnt/e/UrbanWind/cfd_cases/nju_xianlin"
LOG="/mnt/e/UrbanWind/run_cfd.log"

echo "=========================================" | tee "$LOG"
echo " UrbanWind CFD Pipeline (E drive)" | tee -a "$LOG"
echo " Case: nju_xianlin" | tee -a "$LOG"
echo " Started: $(date)" | tee -a "$LOG"
echo "=========================================" | tee -a "$LOG"

# Source OpenFOAM environment (Ubuntu 24.04 package)
if [ -f /usr/share/openfoam/etc/bashrc ]; then
    source /usr/share/openfoam/etc/bashrc
elif [ -f /usr/lib/openfoam/openfoam/etc/bashrc ]; then
    source /usr/lib/openfoam/openfoam/etc/bashrc
fi

cd "$CASE_DIR"
echo "Working directory: $(pwd)" | tee -a "$LOG"

echo "=== [1/4] blockMesh ===" | tee -a "$LOG"
blockMesh 2>&1 | tee -a "$LOG"
echo "blockMesh DONE at $(date)" | tee -a "$LOG"

echo "=== [2/4] snappyHexMesh -overwrite ===" | tee -a "$LOG"
snappyHexMesh -overwrite 2>&1 | tee -a "$LOG"
echo "snappyHexMesh DONE at $(date)" | tee -a "$LOG"

echo "=== [3/4] checkMesh ===" | tee -a "$LOG"
checkMesh 2>&1 | tee -a "$LOG"
echo "checkMesh DONE at $(date)" | tee -a "$LOG"

echo "=== [4/4] simpleFoam ===" | tee -a "$LOG"
echo "Start solve at $(date)" | tee -a "$LOG"
simpleFoam 2>&1 | tee -a "$LOG"
echo "simpleFoam DONE at $(date)" | tee -a "$LOG"

echo "=========================================" | tee -a "$LOG"
echo " PIPELINE COMPLETE: $(date)" | tee -a "$LOG"
echo "=========================================" | tee -a "$LOG"
